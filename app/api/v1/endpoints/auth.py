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

class UnifiedLoginRequest(BaseModel):
    identifier: str = Field(..., description="Mobile number (Primary) or Email (Secondary)")
    otp_code: str = Field(..., description="Primary authentication method")

class OTPRequest(BaseModel):
    identifier: str

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
        # BYPASS: Use raw dictionary queries
        return await Admin.find_one({"email": identifier}) or \
               await Employer.find_one({"email": identifier}) or \
               await Employee.find_one({"email": identifier})
    else:
        clean_phone = identifier[-10:]
        # BYPASS: Use raw dictionary queries
        return await Admin.find_one({"phone": clean_phone}) or \
               await Employer.find_one({"phone": clean_phone}) or \
               await Employee.find_one({"phone": clean_phone})


# ==============================================================================
# --- ADMIN AUTHENTICATION ENDPOINTS ---
# ==============================================================================

@router.post("/admin/login", response_model=dict)
@limiter.limit("5/minute")
async def admin_login(data: UnifiedLoginRequest, request: Request):
    """
    Highly secure OTP-only login endpoint strictly for System Administrators.
    """
    identity_type = "email" if is_email(data.identifier) else "phone"
    
    # 1. STRICT LOOKUP: Use raw dictionary queries
    if identity_type == "email":
        admin_user = await Admin.find_one({"email": data.identifier})
    else:
        clean_phone = data.identifier[-10:]
        admin_user = await Admin.find_one({"phone": clean_phone})

    # Security: Do NOT redirect admins. Just fail immediately if not found.
    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid administrator credentials."
        )

    if not admin_user.is_active:
        raise HTTPException(status_code=403, detail="Admin account suspended.")

    # 2. AUTHENTICATION: OTP ONLY
    if identity_type == "phone":
        clean_phone = data.identifier[-10:]
        
        # 🛑 DUMMY ADMIN BYPASS
        if clean_phone == "9999999999" and data.otp_code == "1234":
            pass # Skip database verification entirely!
            
        else:
            # Standard Database Verification
            otp_record = await OTP.find_one({"phone": clean_phone, "user_type": "admin"})
            
            if not otp_record or not otp_record.hashed_code or not verify_password(data.otp_code, otp_record.hashed_code):
                raise HTTPException(status_code=401, detail="Invalid or expired SMS OTP.")
                
            # Consume the OTP safely (DO NOT DELETE THE TRACKER RECORD)
            otp_record.hashed_code = None
            await otp_record.save()
        
    else:
        if not admin_user.otp_code or admin_user.otp_code != data.otp_code:
            raise HTTPException(status_code=401, detail="Invalid Email OTP.")
        if admin_user.otp_expires_at < datetime.utcnow():
            raise HTTPException(status_code=401, detail="Email OTP expired.")
        
        admin_user.otp_code = None
        admin_user.otp_expires_at = None
        await admin_user.save()

    # 3. SUCCESS: Generate Admin Token
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
async def update_admin_phone(
    data: UpdateAdminPhoneRequest,
    current_admin = Depends(get_current_user) 
):
    """
    Protected Administration Gate: Updates an administrator's primary phone 
    after performing critical checks and multi-step OTP re-verification.
    """
    # Fetch the actual Admin document first to ensure object lookups work perfectly
    admin_doc = await Admin.get(ObjectId(current_admin["id"]))
    if not admin_doc:
        raise HTTPException(status_code=404, detail="Admin document mismatch.")

    clean_new_phone = data.new_phone[-10:]

    # 1. Validation check
    if getattr(admin_doc, "phone", None) == clean_new_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="This is already your registered administrative phone number."
        )

    # 2. Global ecosystem uniqueness verification
    phone_taken = await Admin.find_one({"phone": clean_new_phone}) or \
                  await Employer.find_one({"phone": clean_new_phone}) or \
                  await Employee.find_one({"phone": clean_new_phone})
    
    if phone_taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="This phone number is actively linked to another entity inside Roji Roti."
        )

    # 3. Secure authorization token verification
    otp_record = await OTP.find_one({"phone": clean_new_phone})
    if not otp_record or not otp_record.hashed_code or not verify_password(data.otp_code, otp_record.hashed_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Verification failed: Invalid or expired OTP."
        )

    # 4. Safely nullify the validation sequence to consume it
    otp_record.hashed_code = None
    await otp_record.save()

    # 5. Database updates
    admin_doc.phone = clean_new_phone
    await admin_doc.save()

    return {
        "status": "success",
        "message": "Administrative phone registry successfully updated.",
        "new_phone": admin_doc.phone
    }


# ==============================================================================
# --- PUBLIC UNIFIED AUTHENTICATION ENDPOINT ---
# ==============================================================================

@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
async def unified_login(data: UnifiedLoginRequest, request: Request):
    """
    Master OTP Verification Endpoint.
    - If user exists -> Logs them in.
    - If user does NOT exist -> Returns a registration_token to complete signup.
    """
    identity_type = "email" if is_email(data.identifier) else "phone"
    
    # =====================================================================
    # STEP 1: VERIFY THE OTP FIRST (Before we care who they are)
    # =====================================================================
    if identity_type == "phone":
        clean_phone = data.identifier[-10:]
        
        # 🛑 DUMMY ACCOUNT BYPASS
        TEST_ACCOUNTS = {
            "9999999999": "1234", # Root Admin
            "8989792276": "5678", # Employer
            "8989792275": "9012", # Employee
        }
        
        if clean_phone in TEST_ACCOUNTS and data.otp_code == TEST_ACCOUNTS[clean_phone]:
            # Skip the database check entirely!
            verified_identity = clean_phone
            
        else:
            # Standard Database Verification
            otp_record = await OTP.find_one({"phone": clean_phone})
            
            if not otp_record or not otp_record.hashed_code or not verify_password(data.otp_code, otp_record.hashed_code):
                raise HTTPException(status_code=400, detail="Invalid or expired SMS OTP.")
            
            # OTP is correct, consume it by setting to None (DO NOT DELETE THE TRACKER)
            otp_record.hashed_code = None
            await otp_record.save()
            verified_identity = clean_phone
        
    else:
        # Email Verification
        user = await get_user_by_identifier(data.identifier)
        if not user:
            raise HTTPException(status_code=404, detail="Email not registered.")

        if not getattr(user, "otp_code") or user.otp_code != data.otp_code:
            raise HTTPException(status_code=400, detail="Invalid Email OTP.")
        if user.otp_expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Email OTP expired.")
        
        user.otp_code = None
        user.otp_expires_at = None
        await user.save()
        verified_identity = data.identifier

    # =====================================================================
    # STEP 2: ROUTE THE USER (Login vs Registration)
    # =====================================================================
    user = await get_user_by_identifier(data.identifier)

    # Security Upgrade: Block Admins from the public portal
    if isinstance(user, Admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Administrators must use the dedicated /admin/login portal."
        )

    # --- ROUTE A: UNREGISTERED USER (Seamless Pivot to Registration) ---
    if not user:
        if identity_type == "email":
            # We don't allow seamless email registration, only mobile
            raise HTTPException(status_code=404, detail="Email not registered.")
            
        registration_token = create_access_token(
            subject=verified_identity, 
            user_type="registration_token",
            expires_delta=timedelta(minutes=15)
        )
        
        return {
            "status": "unregistered",
            "action": "redirect_to_register",
            "message": "Phone number verified. Proceed to profile setup.",
            "registration_token": registration_token,
            "verified_phone": verified_identity
        }

    # --- ROUTE B: EXISTING USER (Standard Login) ---
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Account is suspended.")

    user_type = "employer" if isinstance(user, Employer) else "employee"
    
    # Generate standard Access Token for the app
    access_token = create_access_token(subject=str(user.id), user_type=user_type)
    
    return {
        "status": "success",
        "action": "login",
        "access_token": access_token, 
        "token_type": "bearer",
        "user_type": user_type,
        "user_id": str(user.id),
        "user_name": getattr(user, "name", None) or getattr(user, "company_name", None)
    }


# ==============================================================================
# --- UNIVERSAL OTP REQUEST SYSTEM ---
# ==============================================================================

@router.post("/resend-otp")
@router.post("/request-otp")
@limiter.limit("3/minute")
async def request_otp_challenge(data: OTPRequest, request: Request):
    """
    Universal OTP Request. Prioritizes Mobile numbers.
    Allows sending OTP to unregistered mobile numbers, and handles Admin OTPs securely.
    """
    identifier = data.identifier
    now = datetime.utcnow()
    user = await get_user_by_identifier(identifier)
    
    # Cooldown Check
    if user and getattr(user, "last_otp_requested_at", None):
        if now - user.last_otp_requested_at < timedelta(seconds=60):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, 
                detail="Too many requests. Please wait 60 seconds."
            )
    
    # 1. Generate a standard random 4-digit OTP
    otp_code = ''.join(random.choices(string.digits, k=4))
    clean_phone = identifier[-10:] if not is_email(identifier) else None

    # =================================================================
    # DUMMY OTP OVERRIDE FOR TESTING & APP STORE REVIEWERS
    # =================================================================
    TEST_ACCOUNTS = {
        "9999999999": "1234", # Root Admin
        "8989792276": "5678", # Employer
        "8989792275": "9012", # Employee
    }
    
    is_test_account = False
    if clean_phone and clean_phone in TEST_ACCOUNTS:
        otp_code = TEST_ACCOUNTS[clean_phone]
        is_test_account = True
    # =================================================================

    if is_email(identifier):
        # --- SECONDARY: Email Flow ---
        if not user:
            return {"message": "If this email is registered, an OTP has been sent."}

        user.otp_code = otp_code
        user.otp_expires_at = now + timedelta(minutes=5)
        user.last_otp_requested_at = now 
        await user.save()
        
        await EmailService.send_otp_email(to_email=identifier, otp=otp_code)
        dest_type = "email"
        
    else:
        # --- PRIMARY: Mobile Flow ---
        hashed_otp = get_password_hash(otp_code)
        
        # Determine exact user type
        user_type = "unknown" 
        if user:
            user.last_otp_requested_at = now
            await user.save()
            if isinstance(user, Admin):
                user_type = "admin"
            elif isinstance(user, Employer):
                user_type = "employer"
            else:
                user_type = "employee"

        # Check existing OTP tracker for abuse prevention
        otp_record = await OTP.find_one({"phone": clean_phone})
        
        if otp_record:
            # If the last request was yesterday, reset the counter
            if otp_record.last_request_date.date() < now.date():
                otp_record.daily_count = 0
                
            # THE HARD CAP: Block if more than 10 requests today
            if otp_record.daily_count >= 10:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS, 
                    detail="Daily SMS limit reached. Please try again tomorrow."
                )
                
            # Update existing record
            otp_record.hashed_code = hashed_otp
            otp_record.user_type = user_type
            otp_record.daily_count += 1
            otp_record.last_request_date = now
            await otp_record.save()
            
        else:
            # First time this phone is requesting an OTP
            new_otp = OTP(
                phone=clean_phone, 
                hashed_code=hashed_otp, 
                user_type=user_type,
                daily_count=1,
                last_request_date=now
            )
            await new_otp.insert()
        
        # ONLY trigger the real SMS API if it is NOT a dummy account
        if not is_test_account:
            await SMSService.send_otp(identifier, otp_code)
            
        dest_type = "mobile number"

    # =================================================================
    # NEW CODE GOES HERE (Replacing the old return statement)
    # =================================================================
    
    # For local development convenience, print the OTP to your terminal
    print(f"🔐 DEV ALERT: OTP for {identifier} is {otp_code}")

    # Create a dynamic response payload
    response_payload = {"message": f"OTP has been successfully sent to your {dest_type}."}
    
    # If you have a settings.DEBUG flag, use that. 
    # Otherwise, this safely prints the OTP directly into Postman/Mobile App during development
    response_payload["dev_otp_bypass"] = otp_code 

    return response_payload


# ==============================================================================
# --- SESSION MANAGEMENT ---
# ==============================================================================

@router.post("/logout")
async def logout(token: str = Depends(oauth2_scheme)):
    """
    Destroys the user's active session by adding their token's unique JTI to the blacklist.
    """
    try:
        # Decode the token just to read its JTI and Expiration Date
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        jti = payload.get("jti")
        exp = payload.get("exp")
    except JWTError:
        # If the token is already expired or invalid, they are technically already logged out!
        return {"status": "success", "message": "Successfully logged out."}

    if jti and exp:
        # Convert the UNIX expiration timestamp to a proper UTC Datetime object
        # We need this so MongoDB knows exactly when to delete the blacklist record
        expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
        
        # Save it to the database
        blacklisted_token = TokenBlacklist(jti=jti, expires_at=expires_at)
        await blacklisted_token.insert()

    return {"status": "success", "message": "Successfully logged out."}