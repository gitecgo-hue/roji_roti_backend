from fastapi import APIRouter, HTTPException, status, Request, Depends, Query
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import jwt, JWTError
import re

# --- Cores Imports ---
from app.core.limiter import limiter
from app.core.config import settings
from app.core.security import create_access_token

# --- Models Imports ---
from app.models.employer import Employer, EmployerType, SubscriptionTier
from app.models.transaction import Transaction, TransactionType, TransactionStatus
from app.models.notification import Notification, NotificationType
from app.models.admin import Admin
from app.models.auth import OTP, TokenBlacklist
from app.models.employee import Employee

# --- Services Imports ---
from app.services.notification import NotificationService
from app.services.otp import OTPService

# --- Utilities Imports ---
from app.utils.referral import generate_referral_code

# --- Dependencies Imports ---
from app.api.dependencies import get_any_current_user
from app.api.dependencies import get_current_admin

router = APIRouter()

# --- OAuth2 Scheme for Token Authentication ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# --- PYDANTIC SCHEMAS ---
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
    referred_by_code: Optional[str] = None  # <-- Added for the Referral System

# --- ADMIN AUTHENTICATION ENDPOINTS ---
@router.post("/admin/request_otp")
async def request_admin_otp(data: AdminOTPRequest): 
    clean_phone = data.identifier[-10:] 
    existing_admin = await Admin.find_one({"phone": clean_phone})
    if not existing_admin:
        raise HTTPException(status_code=404, detail="Admin account not found.")
    
    dest_type, otp_code = await OTPService.generate_and_send_otp(clean_phone, "admin", existing_admin)
    
    response = {"message": "Admin OTP Sent"}
    if getattr(settings, "DEBUG", False):
        response["test_otp"] = otp_code
        
    return response

# --- ADMIN LOGIN ENDPOINTS ---
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

# --- THE STRICT SIGN-UP FLOW ---
@router.post("/register/request_otp")
@limiter.limit("3/minute")
async def request_signup_otp(data: RequestSignupOTP, request: Request):
    app_role = data.app_role.lower()
    user = await OTPService.get_user_by_identifier(data.identifier)

    if user:
        raise HTTPException(status_code=409, detail="This number is already registered. Please go to Login.")

    dest_type, otp_code = await OTPService.generate_and_send_otp(data.identifier, app_role, user=None, name=data.name)
    
    response = {"message": f"OTP sent to your {dest_type}. Phone number available for registration."}
    if getattr(settings, "DEBUG", False):
        response["test_otp"] = otp_code
        
    return response

# --- VERIFY OTP DURING LOGIN ---
@router.post("/register/verify_otp", response_model=dict)
@limiter.limit("5/minute")
async def verify_signup_otp(data: PublicVerifyRequest, request: Request):
    """
    Verifies the OTP and IMMEDIATELY creates the Employee/Employer record 
    with their Name and Phone number.
    """
    is_email_auth = OTPService.is_email(data.identifier)
    app_role = data.app_role.lower()
    clean_phone = data.identifier[-10:]
    
    # Double check they didn't register in the last 5 minutes
    user = await OTPService.get_user_by_identifier(data.identifier)
    if user:
        raise HTTPException(status_code=409, detail="Number already registered. Please Login.")

    # Fetch the OTP record FIRST to grab the temporarily saved Name
    otp_record = await OTP.find_one({"phone": clean_phone})
    if not otp_record:
        raise HTTPException(status_code=400, detail="Please request an OTP first.")
    
    saved_name = otp_record.name or "Unknown User"

    # Verify and consume the OTP
    await OTPService.verify_and_consume_otp(data.identifier, data.otp_code, is_email_auth)

    # Create the new user record based on the app_role
    if app_role == "employee":
        new_user = Employee(
            phone=clean_phone,
            name=saved_name,
            is_active=True
        )
        await new_user.insert()
    else:
        new_user = Employer(
            phone=clean_phone,
            name=saved_name,
            is_active=True,
            employer_type=EmployerType.COMPANY, 
            subscription_tier=SubscriptionTier.FREE,
            referral_code=generate_referral_code()
        )
        
        # Check if they used a friend's referral code
        referrer = None
        if data.referred_by_code:
            referrer = await Employer.find_one({"referral_code": data.referred_by_code})
            if referrer:
                new_user.referred_by_code = data.referred_by_code
                new_user.available_credits = 50

        # Save the new employer to the database
        await new_user.insert()

        # Process the rewards via our Ledger!
        if referrer:
            # Reward the Referrer
            referrer.available_credits += 50
            await referrer.save()
            
            await Transaction(
                employer_id=str(referrer.id),
                amount=50,
                transaction_type=TransactionType.ADDED,
                title="Referral Bonus",
                description="Bonus for referring a new employer.",
                status=TransactionStatus.SUCCESS
            ).insert()

            # Record the bonus for the New Employer
            await Transaction(
                employer_id=str(new_user.id),
                amount=50,
                transaction_type=TransactionType.ADDED,
                title="Welcome Bonus",
                description=f"Referred by {getattr(referrer, 'company_name', None) or 'a friend'}.",
                status=TransactionStatus.SUCCESS
            ).insert()

    # Issue the PERMANENT Access Token
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

# --- THE STRICT LOGIN FLOW ---
@router.post("/login/request_otp")
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

    dest_type, otp_code = await OTPService.generate_and_send_otp(data.identifier, app_role, user)
    
    response = {"message": f"OTP sent to your {dest_type}."}
    if getattr(settings, "DEBUG", False):
        response["test_otp"] = otp_code
        
    return response

# --- VERIFY OTP DURING LOGIN ---
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

    # Verify the OTP
    await OTPService.verify_and_consume_otp(data.identifier, data.otp_code, is_email_auth)
    
    # Generate the Token
    access_token = create_access_token(subject=str(user.id), user_type=actual_role)

    # Fire the Security Notification ONLY for employers
    await NotificationService.notify_user(
        user_id=str(user.id),
        title="New Login Detected",
        message="Your account was just accessed.",
        notif_type=NotificationType.SECURITY_LOGIN
    )
    
    # Return success response
    return {
        "status": "success",
        "access_token": access_token, 
        "token_type": "bearer",
        "user_type": actual_role,
        "user_id": str(user.id),
        "user_name": getattr(user, "name", None) or getattr(user, "company_name", None)
    }

# --- SESSION MANAGEMENT ENDPOINTS ---
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

# --- DATABASE MAINTENANCE ENDPOINTS ---
@router.post("/system/db-maintenance/fix-schema", status_code=status.HTTP_200_OK)
async def fix_corrupted_schema(
    dry_run: bool = Query(False, description="If true, reports corruption without modifying the DB"),
    current_admin = Depends(get_current_admin) 
):
    """
    DATABASE MAINTENANCE UTILITY (Enterprise Grade)
    -----------------------------------------------
    Purpose: Recovers corrupted arrays, integers, and malformed objects.
    
    Safety Measures:
    1. Admin-only access.
    2. Dry Run Support: Preview changes before committing them.
    3. Memory Protection: Processes backups in batches of 500.
    4. Soft-recovers data by moving corrupted arrays to legacy backup fields.
    """
    
    # 1. LOCK IT DOWN (Security)
    # The `get_current_admin` dependency inherently guarantees the user is a valid admin,
    # so the manual role check has been safely removed.

    db = Employee.get_motor_collection().database
    employees_coll = db["employees"]
    backups_coll = db["corrupted_employees_backup"]

    repair_results = {}
    total_modifications = 0

    # ========================================================
    # PHASE 1: SWEEP AND RECOVER ARRAY FIELDS (Saved as Strings)
    # ========================================================
    array_fields_to_check = [
        "education", 
        "work_experience", 
        "skills", 
        "languages", 
        "saved_job_ids"
    ]

    for field in array_fields_to_check:
        corrupted_query = {field: {"$type": "string"}}
        corrupted_count = await employees_coll.count_documents(corrupted_query)
        
        # --- DRY RUN CHECK ---
        if dry_run:
            if corrupted_count > 0:
                repair_results[field] = f"Found {corrupted_count} corrupted records (Dry run - no changes made)"
            continue 

        if corrupted_count > 0:
            # --- MEMORY PROTECTION: BATCH PROCESSING BACKUPS ---
            cursor = employees_coll.find(corrupted_query)
            batch = []
            
            async for doc in cursor:
                batch.append({
                    "original_employee_id": doc["_id"],
                    "corrupted_field": field,
                    "corrupted_value": doc.get(field),
                    "backed_up_at": datetime.now(timezone.utc)
                })
                
                if len(batch) >= 500:
                    await backups_coll.insert_many(batch)
                    batch = []
                    
            if batch:
                await backups_coll.insert_many(batch)

            # --- SOFT RECOVERY (Rename & Reset) ---
            fix_result = await employees_coll.update_many(
                corrupted_query,
                [
                    {
                        "$set": {
                            f"legacy_{field}_text": f"${field}", 
                            field: [] 
                        }
                    }
                ]
            )
            repair_results[field] = fix_result.modified_count
            total_modifications += fix_result.modified_count
        else:
            repair_results[field] = 0

    # ========================================================
    # PHASE 2: SWEEP AND RECOVER INTEGER FIELDS
    # ========================================================
    integer_fields_to_check = ["age", "experience_years", "current_salary", "experience"]
    
    for field in integer_fields_to_check:
        corrupted_query = {field: {"$type": "string"}}
        corrupted_count = await employees_coll.count_documents(corrupted_query)
        
        # --- DRY RUN CHECK ---
        if dry_run:
            if corrupted_count > 0:
                repair_results[f"{field}_int_fix"] = f"Found {corrupted_count} corrupted records (Dry run - no changes made)"
            continue

        if corrupted_count > 0:
            fix_result = await employees_coll.update_many(
                corrupted_query,
                [{"$set": {field: {"$toInt": f"${field}"}}}]
            )
            repair_results[f"{field}_int_fix"] = fix_result.modified_count
            total_modifications += fix_result.modified_count
        else:
             repair_results[f"{field}_int_fix"] = 0

    # ========================================================
    # PHASE 3: SWEEP AND RECOVER MALFORMED ARRAY OBJECTS
    # ========================================================
    # Query: Find any document where the work_experience array has an item missing the 'company' field
    malformed_we_query = {
        "work_experience": {
            "$elemMatch": {"company": {"$exists": False}}
        }
    }
    malformed_we_count = await employees_coll.count_documents(malformed_we_query)

    if dry_run:
        if malformed_we_count > 0:
            repair_results["malformed_work_experience"] = f"Found {malformed_we_count} corrupted records (Dry run - no changes made)"
        else:
            repair_results["malformed_work_experience"] = 0
    elif malformed_we_count > 0:
        # --- MEMORY PROTECTION: BATCH PROCESSING BACKUPS ---
        cursor = employees_coll.find(malformed_we_query)
        batch = []
        
        async for doc in cursor:
            batch.append({
                "original_employee_id": doc["_id"],
                "corrupted_field": "work_experience (Missing company field)",
                "corrupted_value": doc.get("work_experience"),
                "backed_up_at": datetime.now(timezone.utc)
            })
            
            if len(batch) >= 500:
                await backups_coll.insert_many(batch)
                batch = []
                
        if batch:
            await backups_coll.insert_many(batch)

        # --- SOFT RECOVERY (Rename & Reset) ---
        fix_result = await employees_coll.update_many(
            malformed_we_query,
            [
                {
                    "$set": {
                        "legacy_malformed_work_experience": "$work_experience",
                        "work_experience": []
                    }
                }
            ]
        )
        repair_results["malformed_work_experience"] = fix_result.modified_count
        total_modifications += fix_result.modified_count
    else:
        repair_results["malformed_work_experience"] = 0

    # ========================================================
    # FINAL RESPONSE
    # ========================================================
    mode = "DRY RUN MODE (No changes saved)" if dry_run else "LIVE MODE (Database Updated)"
    
    return {
        "status": "success",
        "mode": mode,
        "message": "Database sweep completed safely.",
        "total_records_modified": total_modifications,
        "fixes_applied": repair_results
    }

# --- DELIVERY WEBHOOK ---
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