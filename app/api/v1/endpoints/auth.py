import re
import random
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

async def get_user_by_identifier(identifier: str):
    """Utility to find a user (Admin, Employer, or Employee) by phone or email."""
    if is_email(identifier):
        return await Admin.find_one({"email": identifier}) or \
               await Employer.find_one({"email": identifier}) or \
               await Employee.find_one({"email": identifier})
    else:
        clean_phone = identifier[-10:]
        return await Admin.find_one({"phone": clean_phone}) or \
               await Employer.find_one({"phone": clean_phone}) or \
               await Employee.find_one({"phone": clean_phone})

async def verify_and_consume_otp(identifier: str, otp_code: str, is_email_auth: bool):
    """Helper to cleanly verify OTPs for all endpoints"""
    if is_email_auth:
        user = await get_user_by_identifier(identifier)
        if not user:
            raise HTTPException(status_code=404, detail="Email not registered.")
        if not getattr(user, "otp_code") or user.otp_code != otp_code:
            raise HTTPException(status_code=400, detail="Invalid Email OTP.")
        if user.otp_expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Email OTP expired.")
        
        user.otp_code = None
        user.otp_expires_at = None
        await user.save()
    else:
        clean_phone = identifier[-10:]
        otp_record = await OTP.find_one({"phone": clean_phone})
        if not otp_record or not otp_record.hashed_code or not verify_password(otp_code, otp_record.hashed_code):
            raise HTTPException(status_code=400, detail="Invalid or expired SMS OTP.")
        
        otp_record.hashed_code = None
        await otp_record.save()

async def generate_and_send_otp(identifier: str, app_role: str, user=None):
    """Helper to generate and send OTP via SMS/Email"""
    now = datetime.utcnow()
    otp_code = ''.join(random.choices(string.digits, k=4))

    if is_email(identifier):
        if user:
            user.otp_code = otp_code
            user.otp_expires_at = now + timedelta(minutes=5)
            user.last_otp_requested_at = now 
            await user.save()
        await EmailService.send_otp_email(to_email=identifier, otp=otp_code)
        return "email"
    else:
        clean_phone = identifier[-10:]
        hashed_otp = get_password_hash(otp_code)
        
        if user:
            user.last_otp_requested_at = now
            await user.save()

        otp_record = await OTP.find_one({"phone": clean_phone})
        if otp_record:
            if otp_record.last_request_date.date() < now.date():
                otp_record.daily_count = 0
            if otp_record.daily_count >= 10:
                raise HTTPException(status_code=429, detail="Daily SMS limit reached.")
                
            otp_record.hashed_code = hashed_otp
            otp_record.user_type = app_role
            otp_record.daily_count += 1
            otp_record.last_request_date = now
            await otp_record.save()
        else:
            new_otp = OTP(phone=clean_phone, hashed_code=hashed_otp, user_type=app_role, daily_count=1, last_request_date=now)
            await new_otp.insert()
            # Re-fetch to guarantee we have the object to update the session_id
            otp_record = await OTP.find_one({"phone": clean_phone})
        
        # Trigger real SMS API and get the session ID
        session_id = await SMSService.send_otp(identifier, otp_code)
        
        # Save the session ID to the database so the webhook can find it later
        if session_id and otp_record:
            otp_record.session_id = session_id
            otp_record.delivery_status = "PENDING"
            await otp_record.save()
            
        return "mobile number"


# ==============================================================================
# --- ADMIN AUTHENTICATION ENDPOINTS (FIREWALLED) ---
# ==============================================================================

@router.post("/admin/request-otp")
async def request_admin_otp(data: AdminOTPRequest): 
    clean_phone = data.identifier[-10:] 

    existing_admin = await Admin.find_one({"phone": clean_phone})
    if not existing_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin account not found. Please contact the Super Admin to register."
        )
    await generate_and_send_otp(clean_phone, "admin", existing_admin)
    return {"message": "Admin OTP Sent"}

@router.post("/admin/login", response_model=dict)
@limiter.limit("5/minute")
async def admin_login(data: AdminLoginRequest, request: Request):
    is_email_auth = is_email(data.identifier)
    
    user = await Admin.find_one({"email": data.identifier}) if is_email_auth else await Admin.find_one({"phone": data.identifier[-10:]})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Admin account suspended.")

    await verify_and_consume_otp(data.identifier, data.otp_code, is_email_auth)
    
    access_token = create_access_token(subject=str(user.id), user_type="admin")
    return {
        "status": "success", "access_token": access_token, 
        "token_type": "bearer", "user_type": "admin",
        "role": user.role, "user_name": user.name
    }

@router.patch("/admin/me/phone", status_code=status.HTTP_200_OK)
async def update_admin_phone(data: UpdateAdminPhoneRequest, current_admin = Depends(get_current_user)):
    admin_doc = await Admin.get(ObjectId(current_admin["id"]))
    if not admin_doc:
        raise HTTPException(status_code=404, detail="Admin document mismatch.")

    clean_new_phone = data.new_phone[-10:]

    if getattr(admin_doc, "phone", None) == clean_new_phone:
        raise HTTPException(status_code=400, detail="This is already your registered phone number.")

    phone_taken = await Admin.find_one({"phone": clean_new_phone}) or \
                  await Employer.find_one({"phone": clean_new_phone}) or \
                  await Employee.find_one({"phone": clean_new_phone})
    
    if phone_taken:
        raise HTTPException(status_code=409, detail="Phone number is actively linked to another entity.")

    otp_record = await OTP.find_one({"phone": clean_new_phone})
    if not otp_record or not otp_record.hashed_code or not verify_password(data.otp_code, otp_record.hashed_code):
        raise HTTPException(status_code=401, detail="Verification failed: Invalid or expired OTP.")

    otp_record.hashed_code = None
    await otp_record.save()

    admin_doc.phone = clean_new_phone
    await admin_doc.save()

    return {"status": "success", "message": "Administrative phone registry successfully updated.", "new_phone": admin_doc.phone}


# ==============================================================================
# --- PUBLIC AUTHENTICATION ENDPOINTS ---
# ==============================================================================

@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
async def unified_login(data: PublicLoginRequest, request: Request):
    """
    Master OTP Verification Endpoint.
    Strictly checks the requested app_role to prevent cross-app login.
    """
    identity_type = "email" if is_email(data.identifier) else "phone"
    app_role = data.app_role.lower()
    is_signup = data.is_signup

    if app_role not in ["employer", "employee"]:
        raise HTTPException(status_code=400, detail="Invalid app_role. Must be 'employer' or 'employee'.")

    # 1. Verify OTP
    is_email_auth = is_email(data.identifier)
    await verify_and_consume_otp(data.identifier, data.otp_code, is_email_auth)

    # 2. THE SMART ROLE GATEKEEPER
    user = await get_user_by_identifier(data.identifier)

    if isinstance(user, Admin):
        raise HTTPException(status_code=403, detail="Administrators must use the dedicated /admin portal.")

    # --- SCENARIO A: NEW USER VERIFYING OTP FOR SIGNUP ---
    if not user and is_signup:
        registration_token = create_access_token(
            subject=data.identifier, 
            user_type="registration_token",
            expires_delta=timedelta(minutes=15)
        )
        return {
            "status": "unregistered",
            "action": "redirect_to_register",
            "message": "Phone verified. Proceed to registration.",
            "registration_token": registration_token,
            "verified_phone": data.identifier
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

@router.post("/resend-otp")
@router.post("/request-otp")
@limiter.limit("2/minute")
async def request_otp_challenge(data: PublicOTPRequest, request: Request):
    """
    Universal OTP Request.
    Smart Gate: Blocks unregistered users from logging in, and blocks existing users from signing up.
    """
    identifier = data.identifier
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

    # Cross-Login Check
    if user:
        if isinstance(user, Admin):
            raise HTTPException(status_code=403, detail="Administrators must use the dedicated admin portal.")
            
        actual_role = "employer" if isinstance(user, Employer) else "employee"
        if actual_role != app_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access Denied: This number is registered as an {actual_role.capitalize()}."
            )

    # 2. Cooldown Check
    if user and getattr(user, "last_otp_requested_at", None):
        if datetime.utcnow() - user.last_otp_requested_at < timedelta(seconds=60):
            raise HTTPException(status_code=429, detail="Too many requests. Please wait 60 seconds.")
    
    dest_type = await generate_and_send_otp(identifier, app_role, user)
    return {"message": f"OTP has been successfully sent to your {dest_type}."}


# ==============================================================================
# --- SESSION MANAGEMENT ---
# ==============================================================================

@router.post("/logout")
async def logout(token: str = Depends(oauth2_scheme)):
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


# ==============================================================================
# --- DELIVERY WEBHOOK ---
# ==============================================================================

@router.post("/webhooks/2factor", include_in_schema=False)
async def twofactor_delivery_webhook(request: Request):
    """
    Listens for delivery receipts from 2Factor.in and updates the database.
    Hidden from Swagger docs.
    """
    try:
        # 2Factor might send data as JSON or standard form data, so we handle both
        try:
            data = await request.json()
        except:
            data = dict(await request.form())

        # 2Factor typically sends SessionId and Status
        session_id = data.get("SessionId") or data.get("Session_Id")
        status = data.get("Status")

        if session_id and status:
            # Find the exact OTP request in your database
            otp_record = await OTP.find_one({"session_id": session_id})
            
            if otp_record:
                # Update it with the real outcome from the telecom operator!
                otp_record.delivery_status = status.upper() 
                await otp_record.save()
                
                print(f"WEBHOOK ALERT: SMS to {otp_record.phone} is now {status.upper()}")

        # You MUST return a 200 OK so 2Factor knows you received it.
        return {"status": "received"}

    except Exception as e:
        print(f"Webhook error: {str(e)}")
        return {"status": "error_handled"}