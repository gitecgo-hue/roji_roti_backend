import re
import random
import string
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status, Request
from bson import ObjectId

from app.core.limiter import limiter
from app.models.employee import Employee
from app.models.employer import Employer
from app.models.admin import Admin  # <-- Added Admin model import
from app.models.auth import OTP 
from app.core.security import (
    get_password_hash, 
    verify_password, 
    create_access_token
)
from app.utils.sms import SMSService
from app.services.email import EmailService

router = APIRouter()

# --- Pydantic Schemas ---

class UnifiedLoginRequest(BaseModel):
    identifier: str = Field(..., description="Mobile number (Primary) or Email (Secondary)")
    otp_code: Optional[str] = Field(None, description="Primary authentication method")
    password: Optional[str] = Field(None, description="Secondary/Fallback authentication method")

class OTPRequest(BaseModel):
    identifier: str

# --- Helpers ---

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
# --- ADMIN AUTHENTICATION ENDPOINT ---
# ==============================================================================

@router.post("/admin/login", response_model=dict)
@limiter.limit("5/minute")
async def admin_login(data: UnifiedLoginRequest, request: Request):
    """
    Highly secure login endpoint strictly for System Administrators.
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

    # 2. PRIMARY AUTH: OTP
    if data.otp_code:
        if identity_type == "phone":
            clean_phone = data.identifier[-10:]
            otp_record = await OTP.find_one({"phone": clean_phone, "user_type": "admin"})
            
            if not otp_record or not verify_password(data.otp_code, otp_record.hashed_code):
                raise HTTPException(status_code=401, detail="Invalid or expired SMS OTP.")
            await otp_record.delete()
            
        else:
            if not admin_user.otp_code or admin_user.otp_code != data.otp_code:
                raise HTTPException(status_code=401, detail="Invalid Email OTP.")
            if admin_user.otp_expires_at < datetime.utcnow():
                raise HTTPException(status_code=401, detail="Email OTP expired.")
            
            admin_user.otp_code = None
            admin_user.otp_expires_at = None
            await admin_user.save()

    # 3. SECONDARY AUTH: Password
    elif data.password:
        if not verify_password(data.password, admin_user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid administrator credentials.")
            
    else:
        raise HTTPException(status_code=400, detail="OTP or Password required.")

    # 4. SUCCESS: Generate Admin Token
    access_token = create_access_token(subject=str(admin_user.id), user_type="admin")
    
    return {
        "status": "success",
        "access_token": access_token, 
        "token_type": "bearer",
        "user_type": "admin",
        "role": admin_user.role,
        "user_name": admin_user.name
    }


# ==============================================================================
# --- PUBLIC UNIFIED AUTHENTICATION ENDPOINT ---
# ==============================================================================

@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
async def unified_login(data: UnifiedLoginRequest, request: Request):
    identity_type = "email" if is_email(data.identifier) else "phone"
    user = await get_user_by_identifier(data.identifier)

    # Security Upgrade: Block Admins from using the public login portal
    if isinstance(user, Admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Administrators must use the dedicated /admin/login portal."
        )

    # 1. HANDLE UNREGISTERED USERS (Mobile Primary Flow)
    if not user:
        if identity_type == "phone":
            # Seamless Mobile Flow: Tell the frontend this is a new user
            return {
                "status": "unregistered",
                "message": "Mobile number not found. Redirecting to registration...",
                "action": "redirect_to_register",
                "phone_provided": data.identifier 
            }
        else:
            # Secondary Email Flow: Standard hard rejection
            raise HTTPException(status_code=404, detail="Email not registered.")

    # 2. HANDLE REGISTERED USERS
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Account is suspended.")

    user_type = "employer" if isinstance(user, Employer) else "employee"

    # PRIMARY AUTHENTICATION: OTP 
    if data.otp_code:
        if identity_type == "phone":
            clean_phone = data.identifier[-10:]
            otp_record = await OTP.find_one({"phone": clean_phone, "user_type": "admin"})

            if not otp_record or not verify_password(data.otp_code, otp_record.hashed_code):
                raise HTTPException(status_code=400, detail="Invalid or expired SMS OTP.")
            
            await otp_record.delete()
            
        else:
            if not getattr(user, "otp_code") or user.otp_code != data.otp_code:
                raise HTTPException(status_code=400, detail="Invalid Email OTP.")
            if user.otp_expires_at < datetime.utcnow():
                raise HTTPException(status_code=400, detail="Email OTP expired.")
            
            user.otp_code = None
            user.otp_expires_at = None
            await user.save()

    # SECONDARY AUTHENTICATION: Password
    elif data.password:
        hashed_pass = getattr(user, "hashed_password", None)
        if not hashed_pass or not verify_password(data.password, hashed_pass):
            raise HTTPException(status_code=400, detail="Invalid credentials.")
            
    else:
        raise HTTPException(status_code=400, detail="An OTP code or password is required to login.")

    # 3. SUCCESS - GENERATE TOKEN
    access_token = create_access_token(subject=str(user.id), user_type=user_type)
    
    return {
        "status": "success",
        "access_token": access_token, 
        "token_type": "bearer",
        "user_type": user_type,
        "user_id": str(user.id),
        "user_name": getattr(user, "name", None) or getattr(user, "company_name", None)
    }


# ==============================================================================
# --- UNIVERSAL OTP REQUEST SYSTEM ---
# ==============================================================================

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
    
    otp_code = ''.join(random.choices(string.digits, k=6))

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
        clean_phone = identifier[-10:]
        hashed_otp = get_password_hash(otp_code)
        
        # Determine exact user type for the OTP record
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

        # Delete any old OTPs for this phone number
        await OTP.find({"phone": clean_phone}).delete() 
        
        # Save new OTP
        new_otp = OTP(phone=clean_phone, hashed_code=hashed_otp, user_type=user_type)
        await new_otp.insert()
        
        # Trigger SMS API
        await SMSService.send_otp(identifier, otp_code)
        dest_type = "mobile number"

    return {"message": f"OTP has been successfully sent to your {dest_type}."}