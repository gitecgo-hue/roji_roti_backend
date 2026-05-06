import re
import random
import string
from datetime import datetime, timedelta
from typing import Optional, List
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, status, Request
from bson import ObjectId

from app.core.limiter import limiter
from app.models.employee import Employee
from app.models.employer import Employer
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
    identifier: str = Field(..., description="Email address or Phone number")
    password: Optional[str] = None
    otp_code: Optional[str] = None

class OTPRequest(BaseModel):
    identifier: str

# --- Helpers ---

def is_email(identifier: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", identifier))

async def get_user_by_identifier(identifier: str):
    """
    Utility to find a user (Employer or Employee) by email or phone.
    """
    if is_email(identifier):
        return await Employer.find_one(Employer.email == identifier) or \
               await Employee.find_one(Employee.email == identifier)
    else:
        clean_phone = identifier[-10:]
        return await Employer.find_one(Employer.phone == clean_phone) or \
               await Employee.find_one(Employee.phone == clean_phone)

# --- Unified Authentication Endpoint ---

@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
async def unified_login(data: UnifiedLoginRequest, request: Request):
    identity_type = "email" if is_email(data.identifier) else "phone"
    
    user = await get_user_by_identifier(data.identifier)

    # -----------------------------------------------------------------------
    # SEAMLESS FLOW: Handle Unregistered Users
    # -----------------------------------------------------------------------
    if not user:
        # If it's a phone number, assume they are a new worker trying to use the app
        if identity_type == "phone":
            return {
                "status": "unregistered",
                "message": "User not found. Redirecting to registration...",
                "action": "redirect_to_register",
                "phone_provided": data.identifier 
            }
        else:
            # If it's an email, we still throw a standard 404 (Employers usually register via a different flow)
            raise HTTPException(status_code=404, detail="User not found.")

    # -----------------------------------------------------------------------
    # Standard Login Flow for Registered Users
    # -----------------------------------------------------------------------
    
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Account is suspended.")

    user_type = "employer" if isinstance(user, Employer) else "employee"

    # Password Flow
    if data.password:
        hashed_pass = getattr(user, "hashed_password", None)
        
        if not hashed_pass or not verify_password(data.password, hashed_pass):
            raise HTTPException(status_code=400, detail="Invalid credentials.")
            
    # OTP Flow
    elif data.otp_code:
        if identity_type == "email":
            if not getattr(user, "otp_code") or user.otp_code != data.otp_code:
                raise HTTPException(status_code=400, detail="Invalid Email OTP.")
            if user.otp_expires_at < datetime.utcnow():
                raise HTTPException(status_code=400, detail="Email OTP expired.")
            
            user.otp_code = None
            user.otp_expires_at = None
            await user.save()
        else:
            otp_record = await OTP.find_one(OTP.phone == data.identifier[-10:], OTP.user_type == user_type)
            if not otp_record or not verify_password(data.otp_code, otp_record.hashed_code):
                raise HTTPException(status_code=400, detail="Invalid or expired SMS OTP.")
            await otp_record.delete()
    
    else:
        raise HTTPException(status_code=400, detail="Either password or OTP is required.")

    # Success: Generate Token
    access_token = create_access_token(subject=str(user.id), user_type=user_type)
    
    # Return enriched response for the frontend
    return {
        "status": "success",
        "access_token": access_token, 
        "token_type": "bearer",
        "user_type": user_type,
        "user_id": str(user.id),
        "user_name": getattr(user, "name", None) or getattr(user, "company_name", None)
    }

# --- OTP Request System ---

@router.post("/request-otp")
@limiter.limit("3/minute")
async def request_otp_challenge(data: OTPRequest, request: Request):
    """
    Universal OTP Request with a 60-second cooldown per user.
    """
    identifier = data.identifier
    now = datetime.utcnow()
    
    # 1. User Cooldown Check
    user = await get_user_by_identifier(identifier)
    
    if user and getattr(user, "last_otp_requested_at", None):
        time_since_last_request = now - user.last_otp_requested_at
        if time_since_last_request < timedelta(seconds=60):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, 
                detail="Too many requests. Please wait 60 seconds."
            )
    
    # 2. Proceed with OTP generation
    otp_code = ''.join(random.choices(string.digits, k=6))

    if is_email(identifier):
        if not user:
            return {"message": "If account exists, OTP has been sent to your email."}

        user.otp_code = otp_code
        user.otp_expires_at = now + timedelta(minutes=5)
        user.last_otp_requested_at = now 
        await user.save()
        
        await EmailService.send_otp_email(to_email=identifier, otp=otp_code)
        dest = "email"
    else:
        clean_phone = identifier[-10:]
        hashed_otp = get_password_hash(otp_code)
        
        if user:
            user.last_otp_requested_at = now
            await user.save()

        user_type = "employer" if isinstance(user, Employer) else "employee"
        await OTP.find(OTP.phone == clean_phone).delete() 
        new_otp = OTP(phone=clean_phone, hashed_code=hashed_otp, user_type=user_type)
        await new_otp.insert()
        
        await SMSService.send_otp(identifier, otp_code)
        dest = "phone"

    return {"message": f"OTP has been sent to your {dest}."}