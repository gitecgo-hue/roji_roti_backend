import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status, Request, Depends
from fastapi.security import OAuth2PasswordBearer
from bson import ObjectId
from jose import jwt, JWTError

from app.core.limiter import limiter
from app.core.config import settings
from app.models.employee import Employee
from app.models.employer import Employer
from app.models.admin import Admin
from app.models.auth import OTP, TokenBlacklist
from app.core.security import (
    get_password_hash, 
    verify_password, 
    create_access_token
)
from app.utils.sms import SMSService
from app.services.email import EmailService
from app.api.dependencies import get_current_user

router = APIRouter()

# This tells FastAPI where to look for the token for endpoints in this router
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# ==============================================================================
# --- PYDANTIC SCHEMAS ---
# ==============================================================================

class AdminOTPRequest(BaseModel):
    identifier: str

class AdminLoginRequest(BaseModel):
    identifier: str = Field(..., description="Admin mobile number or Email")
    otp_code: str = Field(..., description="4-digit OTP code")

class PublicOTPRequest(BaseModel):
    identifier: str
    app_role: str = Field(..., description="Must be 'employer' or 'employee'")
    is_signup: bool = Field(default=False, description="True if requesting from the registration page")

class PublicLoginRequest(BaseModel):
    identifier: str = Field(..., description="Mobile number (Primary) or Email (Secondary)")
    otp_code: str = Field(..., description="Primary authentication method")
    app_role: str = Field(..., description="Must be 'employer' or 'employee'")
    is_signup: bool = Field(default=False, description="True if verifying OTP during registration")

class UpdateAdminPhoneRequest(BaseModel):
    new_phone: str = Field(..., description="The new admin mobile number")
    otp_code: str = Field(..., description="4-digit verification code sent to the new mobile number")


# ==============================================================================
# --- HELPERS ---
# ==============================================================================

def is_email(identifier: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", identifier))


def normalize_identifier(identifier: str) -> str:
    """
    Canonicalizes an identifier so the same person always maps to the same
    lookup key, regardless of how they typed it (extra whitespace, country
    code prefix, mixed-case email). Emails are lowercased; phone numbers are
    reduced to the last 10 digits, matching the slicing used historically
    throughout this file.
    """
    identifier = identifier.strip()
    if is_email(identifier):
        return identifier.lower()
    return identifier[-10:]


async def get_user_by_identifier(identifier: str):
    """Utility to find a user (Admin, Employer, or Employee) by phone or email."""
    norm = normalize_identifier(identifier)
    if is_email(identifier):
        return await Admin.find_one({"email": norm}) or \
               await Employer.find_one({"email": norm}) or \
               await Employee.find_one({"email": norm})
    else:
        return await Admin.find_one({"phone": norm}) or \
               await Employer.find_one({"phone": norm}) or \
               await Employee.find_one({"phone": norm})


async def issue_otp_and_notify(identifier: str, user_type: str, dispatch_identifier: Optional[str] = None) -> None:
    """
    Generates a cryptographically secure OTP, persists its hash in the shared
    OTP collection, and dispatches it via SMS or email depending on the
    identifier type.

    `identifier` is the normalized (lowercased email / last-10-digit phone)
    key used for OTP storage and lookups. `dispatch_identifier` is the raw,
    as-submitted value handed to SMSService/EmailService for actual delivery
    -- it defaults to `identifier`, but callers should pass the untouched
    original string if their SMS/email provider needs it in a different
    format (e.g. with a country code prefix) than the normalized lookup key.

    NOTE: This intentionally works for identifiers that do NOT yet have a
    User/Employer/Employee/Admin document (e.g. a brand-new signup) because
    the OTP record lives in its own collection, keyed by `identifier`, rather
    than as a field on a user document. The OTP model's field is still named
    `phone` for historical reasons even though it now also stores normalized
    email addresses -- consider renaming it to `identifier` in a future
    migration for clarity.

    Raises HTTPException for cooldown / daily-limit violations.
    """
    dispatch_identifier = dispatch_identifier or identifier
    now = datetime.utcnow()
    otp_code = ''.join(secrets.choice(string.digits) for _ in range(4))
    hashed_otp = get_password_hash(otp_code)

    otp_record = await OTP.find_one({"phone": identifier})

    if otp_record:
        if now - otp_record.last_request_date < timedelta(seconds=60):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please wait 60 seconds."
            )

        if otp_record.last_request_date.date() < now.date():
            otp_record.daily_count = 0

        if otp_record.daily_count >= 10:
            raise HTTPException(status_code=429, detail="Daily OTP limit reached.")

        otp_record.hashed_code = hashed_otp
        otp_record.user_type = user_type
        otp_record.daily_count += 1
        otp_record.last_request_date = now
        await otp_record.save()
    else:
        otp_record = OTP(
            phone=identifier, hashed_code=hashed_otp, user_type=user_type,
            daily_count=1, last_request_date=now
        )
        await otp_record.insert()

    if is_email(identifier):
        await EmailService.send_otp_email(to_email=dispatch_identifier, otp=otp_code)
    else:
        await SMSService.send_otp(dispatch_identifier, otp_code)


async def verify_and_consume_otp(identifier: str, otp_code: str, expected_user_type: Optional[str] = None) -> bool:
    """
    Verifies a submitted OTP against the hashed value stored in the OTP
    collection and, if valid, invalidates it so it cannot be replayed.

    `expected_user_type` lets a caller (e.g. the admin portal) require that
    the most recent OTP issued for this identifier was specifically issued
    for that flow, rather than accepting an OTP requested via a different
    role's login/signup screen.
    """
    query = {"phone": identifier}
    if expected_user_type:
        query["user_type"] = expected_user_type

    otp_record = await OTP.find_one(query)
    if not otp_record or not otp_record.hashed_code or not verify_password(otp_code, otp_record.hashed_code):
        return False

    otp_record.hashed_code = None
    await otp_record.save()
    return True


# ==============================================================================
# --- ADMIN AUTHENTICATION ENDPOINTS (FIREWALLED) ---
# ==============================================================================

@router.post("/admin/request-otp")
@limiter.limit("3/minute")
async def request_admin_otp(data: AdminOTPRequest, request: Request):
    identifier = normalize_identifier(data.identifier)

    existing_admin = await Admin.find_one(
        {"email": identifier} if is_email(identifier) else {"phone": identifier}
    )
    if not existing_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin account not found. Please contact the Super Admin to register."
        )

    await issue_otp_and_notify(identifier, user_type="admin", dispatch_identifier=data.identifier)

    dest_type = "email" if is_email(identifier) else "mobile number"
    return {"message": f"OTP has been sent to your registered {dest_type}."}


@router.post("/admin/login", response_model=dict)
@limiter.limit("5/minute")
async def admin_login(data: AdminLoginRequest, request: Request):
    """
    Highly secure OTP-only login endpoint strictly for System Administrators.
    """
    identifier = normalize_identifier(data.identifier)

    admin_user = await Admin.find_one(
        {"email": identifier} if is_email(identifier) else {"phone": identifier}
    )

    if not admin_user:
        raise HTTPException(status_code=401, detail="Invalid administrator credentials.")
    if not admin_user.is_active:
        raise HTTPException(status_code=403, detail="Admin account suspended.")

    if not await verify_and_consume_otp(identifier, data.otp_code, expected_user_type="admin"):
        raise HTTPException(status_code=401, detail="Invalid or expired OTP.")

    access_token = create_access_token(subject=str(admin_user.id), user_type="admin")
    
    return {
        "status": "success",
        "access_token": access_token, 
        "token_type": "bearer",
        "user_type": "admin",
        "role": admin_user.role,
        "user_name": admin_user.name
    }


@router.patch("/admin/me/phone", status_code=status.HTTP_200_OK)
async def update_admin_phone(data: UpdateAdminPhoneRequest, current_admin = Depends(get_current_user)):
    admin_doc = await Admin.get(ObjectId(current_admin["id"]))
    if not admin_doc:
        raise HTTPException(status_code=404, detail="Admin document mismatch.")

    clean_new_phone = normalize_identifier(data.new_phone)

    if getattr(admin_doc, "phone", None) == clean_new_phone:
        raise HTTPException(status_code=400, detail="This is already your registered phone number.")

    phone_taken = await Admin.find_one({"phone": clean_new_phone}) or \
                  await Employer.find_one({"phone": clean_new_phone}) or \
                  await Employee.find_one({"phone": clean_new_phone})
    
    if phone_taken:
        raise HTTPException(status_code=409, detail="Phone number is actively linked to another entity.")

    if not await verify_and_consume_otp(clean_new_phone, data.otp_code):
        raise HTTPException(status_code=401, detail="Verification failed: Invalid or expired OTP.")

    admin_doc.phone = clean_new_phone
    await admin_doc.save()

    return {"status": "success", "message": "Administrative phone registry successfully updated.", "new_phone": admin_doc.phone}


# ==============================================================================
# --- PUBLIC UNIFIED AUTHENTICATION ENDPOINT ---
# ==============================================================================

@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
async def unified_login(data: PublicLoginRequest, request: Request):
    """
    Master OTP Verification Endpoint.
    Strictly checks the requested app_role to prevent cross-app login.
    """
    identifier = normalize_identifier(data.identifier)
    app_role = data.app_role.lower()
    is_signup = data.is_signup

    if app_role not in ["employer", "employee"]:
        raise HTTPException(status_code=400, detail="Invalid app_role. Must be 'employer' or 'employee'.")

    # Look up any existing user FIRST. This lets us redirect admins to the
    # dedicated portal before spending their OTP attempt, and it works
    # uniformly for phone and email since both go through the same lookup.
    user = await get_user_by_identifier(identifier)

    if isinstance(user, Admin):
        raise HTTPException(status_code=403, detail="Administrators must use the dedicated /admin portal.")

    # =====================================================================
    # STEP 1: VERIFY THE OTP (works for both phone and email, login and
    # signup, since it's keyed by identifier rather than by user document).
    # =====================================================================
    if not await verify_and_consume_otp(identifier, data.otp_code):
        raise HTTPException(status_code=400, detail="Invalid or expired OTP.")

    # =====================================================================
    # STEP 2: THE SMART ROLE GATEKEEPER
    # =====================================================================

    # --- SCENARIO A: NEW USER VERIFYING OTP FOR SIGNUP ---
    if not user and is_signup:
        # Success! Give them the temporary pass to the /register endpoint.
        # The chosen app_role is embedded in the user_type claim so /register
        # can recover it -- previously this was silently dropped.
        registration_token = create_access_token(
            subject=identifier, 
            user_type=f"registration_token:{app_role}",
            expires_delta=timedelta(minutes=15)
        )
        return {
            "status": "unregistered",
            "action": "redirect_to_register",
            "message": "Identifier verified. Proceed to registration.",
            "registration_token": registration_token,
            "verified_identifier": identifier
        }

    # --- SCENARIO B: USER TRYING TO LOGIN, BUT DOESN'T EXIST ---
    if not user and not is_signup:
        raise HTTPException(status_code=404, detail=f"This {app_role} account does not exist. Please sign up.")

    # --- SCENARIO C: USER TRYING TO SIGN UP, BUT ALREADY EXISTS ---
    if user and is_signup:
        raise HTTPException(status_code=409, detail="This number is already registered. Please go to the Login page.")

    # --- SCENARIO D: EXISTING USER LOGGING IN (Standard Flow) ---
    actual_role = "employer" if isinstance(user, Employer) else "employee"
    if actual_role != app_role:
        raise HTTPException(
            status_code=403,
            detail=f"Access Denied: This number is registered as an {actual_role.capitalize()}."
        )

    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Account is suspended.")
    
    access_token = create_access_token(subject=str(user.id), user_type=actual_role)
    
    return {
        "status": "success",
        "action": "login",
        "access_token": access_token, 
        "token_type": "bearer",
        "user_type": actual_role,
        "user_id": str(user.id),
        "user_name": getattr(user, "name", None) or getattr(user, "company_name", None)
    }


# ==============================================================================
# --- UNIVERSAL OTP REQUEST SYSTEM ---
# ==============================================================================

@router.post("/resend-otp")
@router.post("/request-otp")
@limiter.limit("3/minute")
async def request_otp_challenge(data: PublicOTPRequest, request: Request):
    """
    Universal OTP Request.
    Smart Gate: Blocks unregistered users from logging in, and blocks existing users from signing up.
    """
    identifier = normalize_identifier(data.identifier)
    app_role = data.app_role.lower()
    is_signup = data.is_signup
    
    if app_role not in ["employer", "employee"]:
        raise HTTPException(status_code=400, detail="Invalid app_role.")

    # 1. THE SMART GATEKEEPER
    user = await get_user_by_identifier(identifier)

    # SCENARIO A: They are trying to LOGIN, but don't have an account
    if not user and not is_signup:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"This {app_role} account does not exist. Please go to the Sign Up page to register."
        )

    # SCENARIO B: They are trying to SIGN UP, but already have an account
    if user and is_signup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail=f"This number is already registered. Please go to the Login page."
        )

    # Cross-Login Check (Only applies if the user exists)
    if user:
        if isinstance(user, Admin):
            raise HTTPException(status_code=403, detail="Administrators must use the dedicated admin portal.")
            
        actual_role = "employer" if isinstance(user, Employer) else "employee"
        if actual_role != app_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access Denied: This number is registered as an {actual_role.capitalize()}."
            )

    # 2. Generate, store, and dispatch the OTP. This works even when `user`
    # is None (new signup) because the OTP lives in its own collection keyed
    # by identifier, not as a field on a user document. The cooldown and
    # daily-limit checks inside also now apply uniformly to email AND phone.
    await issue_otp_and_notify(identifier, user_type=app_role, dispatch_identifier=data.identifier)

    dest_type = "email" if is_email(identifier) else "mobile number"
    return {
        "message": f"OTP has been successfully sent to your {dest_type}."
    }


# ==============================================================================
# --- SESSION MANAGEMENT ---
# ==============================================================================

@router.post("/logout")
async def logout(token: str = Depends(oauth2_scheme)):
    """
    Destroys the user's active session by adding their token's unique JTI to the blacklist.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        jti = payload.get("jti")
        exp = payload.get("exp")
    except JWTError:
        return {"status": "success", "message": "Successfully logged out."}

    if jti and exp:
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        blacklisted_token = TokenBlacklist(jti=jti, expires_at=expires_at)
        await blacklisted_token.insert()

    return {"status": "success", "message": "Successfully logged out."}