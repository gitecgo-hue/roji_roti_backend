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

class RequestSignupOTP(BaseModel):
    identifier: str = Field(..., description="Mobile number for registration")
    name: str = Field(..., description="The user's full name")
    app_role: str = Field(..., description="Must be 'employer' or 'employee'")

class PublicVerifyRequest(BaseModel):
    identifier: str = Field(..., description="Mobile number (Primary) or Email (Secondary)")
    otp_code: str = Field(..., description="Primary authentication method")
    app_role: str = Field(..., description="Must be 'employer' or 'employee'")

class UpdateAdminPhoneRequest(BaseModel):
    new_phone: str = Field(..., description="The new admin mobile number")
    otp_code: str = Field(..., description="4-digit verification code sent to the new mobile number")


# ==============================================================================
# --- HELPERS ---
# ==============================================================================

def is_email(identifier: str) -> bool:
    return bool(re.match(r"[^@]+@[^@]+\.[^@]+", identifier))

async def get_user_by_identifier(identifier: str):
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

async def generate_and_send_otp(identifier: str, app_role: str, user=None, name: str = None):
    """Helper to generate and send OTP via SMS/Email and track Webhook Session"""
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
            if name: 
                otp_record.name = name  # Update the temp name if they retry
            await otp_record.save()
        else:
            new_otp = OTP(
                phone=clean_phone, 
                hashed_code=hashed_otp, 
                user_type=app_role, 
                daily_count=1, 
                last_request_date=now,
                name=name # Save the name during registration request
            )
            await new_otp.insert()
            otp_record = await OTP.find_one({"phone": clean_phone})
        
        # Trigger real SMS API and get the session ID for the webhook
        session_id = await SMSService.send_otp(identifier, otp_code)
        
        if session_id and otp_record:
            otp_record.session_id = session_id
            otp_record.delivery_status = "PENDING"
            await otp_record.save()
            
        return "mobile number"


# ==============================================================================
# --- 1. THE STRICT LOGIN FLOW (For Existing Users Only) ---
# ==============================================================================

@router.post("/login/request-otp")
@limiter.limit("3/minute")
async def request_login_otp(data: PublicOTPRequest, request: Request):
    """Requests an OTP for Login. BLOCKS UNREGISTERED NUMBERS."""
    app_role = data.app_role.lower()
    user = await get_user_by_identifier(data.identifier)

    # BLOCK NEW USERS
    if not user:
        raise HTTPException(status_code=404, detail=f"This {app_role} account does not exist. Please go to Sign Up.")
    
    if isinstance(user, Admin):
        raise HTTPException(status_code=403, detail="Administrators must use the dedicated admin portal.")
        
    actual_role = "employer" if isinstance(user, Employer) else "employee"
    if actual_role != app_role:
        raise HTTPException(status_code=403, detail=f"Access Denied: Registered as {actual_role.capitalize()}.")

    if getattr(user, "last_otp_requested_at", None):
        if datetime.utcnow() - user.last_otp_requested_at < timedelta(seconds=60):
            raise HTTPException(status_code=429, detail="Please wait 60 seconds.")

    dest_type = await generate_and_send_otp(data.identifier, app_role, user)
    return {"message": f"OTP sent to your {dest_type}."}


@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
async def login_verify(data: PublicVerifyRequest, request: Request):
    """Verifies OTP and logs the user in, returning an Access Token."""
    is_email_auth = is_email(data.identifier)
    app_role = data.app_role.lower()

    # Verify the user actually exists before checking OTP
    user = await get_user_by_identifier(data.identifier)
    if not user:
        raise HTTPException(status_code=404, detail="Account not found. Please register.")
        
    actual_role = "employer" if isinstance(user, Employer) else "employee"
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Account is suspended.")

    # Verify OTP
    await verify_and_consume_otp(data.identifier, data.otp_code, is_email_auth)
    
    # Issue Token
    access_token = create_access_token(subject=str(user.id), user_type=actual_role)
    
    return {
        "status": "success",
        "access_token": access_token, 
        "token_type": "bearer",
        "user_type": actual_role,
        "user_id": str(user.id),
        "user_name": getattr(user, "name", None) or getattr(user, "company_name", None)
    }


# ==============================================================================
# --- 2. THE STRICT SIGN-UP FLOW (For New Users Only) ---
# ==============================================================================

@router.post("/register/request-otp")
@limiter.limit("3/minute")
async def request_signup_otp(data: RequestSignupOTP, request: Request):
    """Requests an OTP for Sign-Up. BLOCKS ALREADY REGISTERED NUMBERS and saves the Name temporarily."""
    app_role = data.app_role.lower()
    user = await get_user_by_identifier(data.identifier)

    # BLOCK EXISTING USERS
    if user:
        raise HTTPException(status_code=409, detail="This number is already registered. Please go to Login.")

    # Pass the name into the helper so it gets saved in the OTP database
    dest_type = await generate_and_send_otp(data.identifier, app_role, user=None, name=data.name)
    
    return {"message": f"OTP sent to your {dest_type}. Phone number available for registration."}


@router.post("/register/verify-otp", response_model=dict)
@limiter.limit("5/minute")
async def verify_signup_otp(data: PublicVerifyRequest, request: Request):
    """Verifies Sign-Up OTP and returns a temporary Registration Token."""
    is_email_auth = is_email(data.identifier)
    
    # Double check they didn't register in the last 5 minutes (prevent race conditions)
    user = await get_user_by_identifier(data.identifier)
    if user:
        raise HTTPException(status_code=409, detail="Number already registered.")

    # Verify OTP
    await verify_and_consume_otp(data.identifier, data.otp_code, is_email_auth)

    # Issue Temporary Registration Token
    access_token = create_access_token(
        subject=data.identifier, 
        user_type="access_token",
        expires_delta=timedelta(minutes=15)
    )
    
    return {
        "status": "success",
        "message": "Phone verified. Proceed to registration.",
        "access_token": access_token,
        "verified_phone": data.identifier
    }


# ==============================================================================
# --- 3. ADMIN AUTHENTICATION (Unchanged) ---
# ==============================================================================

@router.post("/admin/request-otp")
async def request_admin_otp(data: AdminOTPRequest): 
    clean_phone = data.identifier[-10:] 
    existing_admin = await Admin.find_one({"phone": clean_phone})
    if not existing_admin:
        raise HTTPException(status_code=404, detail="Admin account not found.")
    
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

@router.get("/webhooks/2factor", include_in_schema=False)
@router.post("/webhooks/2factor", include_in_schema=False)
async def twofactor_delivery_webhook(request: Request):
    """
    Listens for delivery receipts from 2Factor.in and updates the database.
    Handles both GET (Query Params) and POST (JSON/Form) methods.
    """
    try:
        session_id = None
        status = None

        if request.method == "GET":
            session_id = request.query_params.get("SessionId") or request.query_params.get("Session_Id")
            status = request.query_params.get("Status")
        elif request.method == "POST":
            try:
                data = await request.json()
            except:
                data = dict(await request.form())
            session_id = data.get("SessionId") or data.get("Session_Id")
            status = data.get("Status")

        if session_id and status:
            otp_record = await OTP.find_one({"session_id": session_id})
            if otp_record:
                otp_record.delivery_status = status.upper() 
                await otp_record.save()
                print(f"✅ WEBHOOK SUCCESS: SMS to {otp_record.phone} is now {status.upper()}")

        return {"status": "received"}

    except Exception as e:
        print(f"Webhook error: {str(e)}")
        return {"status": "error_handled"}