import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, status, Request, Depends
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError

from app.core.limiter import limiter
from app.core.config import settings
from app.models.employer import Employer, EmployerType, SubscriptionTier
from app.models.admin import Admin
from app.models.auth import OTP, TokenBlacklist
from app.core.security import create_access_token
from app.models.employee import Employee

# --- IMPORT OUR NEW SERVICE ---
from app.services.otp import OTPService

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# ==============================================================================
# PYDANTIC SCHEMAS
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

# ==============================================================================
# 1. ADMIN AUTHENTICATION
# ==============================================================================

@router.post("/admin/request-otp")
async def request_admin_otp(data: AdminOTPRequest): 
    clean_phone = data.identifier[-10:] 
    existing_admin = await Admin.find_one({"phone": clean_phone})
    if not existing_admin:
        raise HTTPException(status_code=404, detail="Admin account not found.")
    
    # Unpack both the destination type and the OTP code from the service
    dest_type, otp_code = await OTPService.generate_and_send_otp(clean_phone, "admin", existing_admin)
    
    response = {"message": "Admin OTP Sent"}
    
    # --- 🔖 BOOKMARK: TESTING ONLY ---
    if getattr(settings, "DEBUG", False):
        response["test_otp"] = otp_code
    # ---------------------------------
        
    return response

# ==============================================================================
# 2. ADMIN LOGIN
# ==============================================================================

@router.post("/admin/login", response_model=dict)
@limiter.limit("5/minute")
async def admin_login(data: AdminLoginRequest, request: Request):
    is_email_auth = OTPService.is_email(data.identifier)
    
    user = await Admin.find_one({"email": data.identifier}) if is_email_auth else await Admin.find_one({"phone": data.identifier[-10:]})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid admin credentials.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Admin account suspended.")

    await OTPService.verify_and_consume_otp(data.identifier, data.otp_code, is_email_auth)
    
    access_token = create_access_token(subject=str(user.id), user_type="admin")
    return {
        "status": "success", "access_token": access_token, 
        "token_type": "bearer", "user_type": "admin",
        "role": user.role, "user_name": user.name
    }

# ==============================================================================
# 3. THE STRICT SIGN-UP FLOW
# ==============================================================================

@router.post("/register/request-otp")
@limiter.limit("3/minute")
async def request_signup_otp(data: RequestSignupOTP, request: Request):
    app_role = data.app_role.lower()
    user = await OTPService.get_user_by_identifier(data.identifier)

    if user:
        raise HTTPException(status_code=409, detail="This number is already registered. Please go to Login.")

    # Unpack both the destination type and the OTP code from the service
    dest_type, otp_code = await OTPService.generate_and_send_otp(data.identifier, app_role, user=None, name=data.name)
    
    response = {"message": f"OTP sent to your {dest_type}. Phone number available for registration."}
    
    # --- 🔖 BOOKMARK: TESTING ONLY ---
    if getattr(settings, "DEBUG", False):
        response["test_otp"] = otp_code
    # ---------------------------------
        
    return response

# ==============================================================================
# 4. VERIFY OTP DURING LOGIN
# ==============================================================================

@router.post("/register/verify-otp", response_model=dict)
@limiter.limit("5/minute")
async def verify_signup_otp(data: PublicVerifyRequest, request: Request):
    """
    Verifies the OTP and IMMEDIATELY creates the Employee/Employer record 
    with their Name and Phone number.
    """
    is_email_auth = OTPService.is_email(data.identifier)
    app_role = data.app_role.lower()
    clean_phone = data.identifier[-10:]
    
    # 1. Double check they didn't register in the last 5 minutes
    user = await OTPService.get_user_by_identifier(data.identifier)
    if user:
        raise HTTPException(status_code=409, detail="Number already registered. Please Login.")

    # 2. Fetch the OTP record FIRST to grab the temporarily saved Name
    otp_record = await OTP.find_one({"phone": clean_phone})
    if not otp_record:
        raise HTTPException(status_code=400, detail="Please request an OTP first.")
    
    saved_name = otp_record.name or "Unknown User"

    # 3. Verify and consume the OTP
    await OTPService.verify_and_consume_otp(data.identifier, data.otp_code, is_email_auth)

# =================================================================
# 5. CREATE THE USER IN THE MAIN DATABASE IMMEDIATELY
# =================================================================
    if app_role == "employee":
        new_user = Employee(
            phone=clean_phone,
            name=saved_name,
            # Note: category, location, etc. must be Optional in your Employee model!
            is_active=True
        )
    else:
        new_user = Employer(
            phone=clean_phone,
            name=saved_name,
            is_active=True,
            employer_type=EmployerType.COMPANY, 
            subscription_tier=SubscriptionTier.FREE
        )
    
    # Save to MongoDB
    await new_user.insert()

    # 5. Issue the PERMANENT Access Token
    access_token = create_access_token(
        subject=str(new_user.id), 
        user_type=app_role
    )
    
    return {
        "status": "success",
        "message": "Phone verified and account created! Please complete your profile.",
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": str(new_user.id),
        "user_name": new_user.name
    }

# ==============================================================================
# 6. THE STRICT LOGIN FLOW
# ==============================================================================

@router.post("/login/request-otp")
@limiter.limit("3/minute")
async def request_login_otp(data: PublicOTPRequest, request: Request):
    app_role = data.app_role.lower()
    user = await OTPService.get_user_by_identifier(data.identifier)

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

    # Unpack both the destination type and the OTP code from the service
    dest_type, otp_code = await OTPService.generate_and_send_otp(data.identifier, app_role, user)
    
    response = {"message": f"OTP sent to your {dest_type}."}
    
    # --- 🔖 BOOKMARK: TESTING ONLY ---
    # This exposes the OTP to the frontend for easy testing without SMS credits.
    # You do NOT need to delete this for production, just set DEBUG=False in your .env file!
    if getattr(settings, "DEBUG", False):
        response["test_otp"] = otp_code
    # ---------------------------------
        
    return response

# ==============================================================================
# 7. VERIFY OTP DURING LOGIN
# ==============================================================================

@router.post("/login", response_model=dict)
@limiter.limit("5/minute")
async def login_verify(data: PublicVerifyRequest, request: Request):
    is_email_auth = OTPService.is_email(data.identifier)
    app_role = data.app_role.lower()

    user = await OTPService.get_user_by_identifier(data.identifier)
    if not user:
        raise HTTPException(status_code=404, detail="Account not found. Please register.")
        
    actual_role = "employer" if isinstance(user, Employer) else "employee"
    if not getattr(user, "is_active", True):
        raise HTTPException(status_code=403, detail="Account is suspended.")

    await OTPService.verify_and_consume_otp(data.identifier, data.otp_code, is_email_auth)
    
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
# 8. SESSION MANAGEMENT
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