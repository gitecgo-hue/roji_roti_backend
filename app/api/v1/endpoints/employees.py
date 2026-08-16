# --- IMPORTS ---
from concurrent.futures import wait
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, Response, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field, ConfigDict, ValidationError
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, date, timezone
from bson import ObjectId
from beanie import PydanticObjectId
import cloudinary.uploader
import random
import re

# --- Core Imports ---
from app.core.config import settings
from app.core.security import create_access_token

# --- Dependencies Imports ---
from app.api.dependencies import (
    get_current_employee,
    get_current_employer,
    get_any_current_user,
    get_current_user,
    get_user_language
)

# --- Schema Imports ---
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeResponse,
    EmployeeProfileUpdate,
    EmployeeDashboardResponse,
    WorkExperienceInput,
    SkillInput,
    EducationUpdate,
    WorkExperienceUpdate,
    AvailabilityUpdate,
    AppliedJobResponse,
    SavedJobResponse
)
from app.schemas.employer import CompanyProfilePublicResponse

# --- Models Imports ---
from app.models.employer import Employer, EmployerType
from app.models.employee import (
    EmployeeProfileUpdate,
    Employee,
    GeoLocation,
    Skill,
    WorkExperience,
    Education,
    ProfileDocument,
    Availability,
    Preferences,
    ProfileMetadata
)
from app.models.application import JobApplication, ApplicationStatus
from app.models.contact import ContactUnlock 
from app.models.job import Job 
from app.models.payment import Payment 
from app.models.review import Review 
from app.models.notification import Notification, NotificationType
from app.models.auth import OTP 
from app.models.category import Category

# --- Services Imports ---
from app.services.notification import NotificationService
from app.services.email import EmailService
from app.services.otp import OTPService
from app.services.webhooks import WebhookService 
from app.services.resumes import ResumeService
from app.services.subscriptions import SubscriptionService
from app.services.recommendation import RecommendationService
from app.services.parser import ResumeParserService
from app.services.cloudinary_service import upload_file
from app.services.cloudinary_service import delete_file
from app.services.location import OlaMapsService

# --- Utilities Imports ---
from app.utils.maps import MapService
from app.utils.translator import translate_document_fields

router = APIRouter()

# --- PYDANTIC SCHEMAS FOR PROFILE COMPLETION & UPDATES ---
class SendUpdateOtpRequest(BaseModel):
    new_phone: str

class UpdatePhoneRequest(BaseModel):
    new_phone: str = Field(..., description="The new 10-digit mobile number")
    otp_code: str = Field(..., description="The 4-digit OTP sent to the NEW number") 

class LocationInput(BaseModel):
    latitude: float
    longitude: float

class WorkExperienceInput(BaseModel):
    job_title: Optional[str] = None
    job_role: Optional[str] = None
    company_name: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    currently_working_here: Optional[bool] = None

class EmailUpdateRequest(BaseModel):
    email: EmailStr

class VerifyEmailUpdateRequest(BaseModel):
    email: EmailStr
    otp_code: str

# --- Employee Dashboard & Stats ---
@router.get("/dashboard", response_model=EmployeeDashboardResponse)
async def get_employee_dashboard(
    current_employee: Employee = Depends(get_current_employee),
    lang: str = Depends(get_user_language)
):
    """
    Returns real-time stats of how many employers have 'unlocked' this employee.
    Safely handles incomplete profiles by providing fallback strings.
    """
    unlock_count = await ContactUnlock.find(
        ContactUnlock.employee_id == current_employee.id
    ).count()

    # Localize the employee for any translated fields
    loc_emp = current_employee.localize(lang_code=lang)

    # Safely access nested properties
    category_display = (current_employee.preferences.job_types[0]
                        if current_employee.preferences and current_employee.preferences.job_types
                        else "Profile Incomplete")
    
    is_available = current_employee.availability.is_available if current_employee.availability else False
    location_display = current_employee.location.city if current_employee.location and current_employee.location.city else "Location pending"
    daily_rate = current_employee.expected_salary

    return EmployeeDashboardResponse(
        name=loc_emp.get("name", "User"),
        category=category_display,
        is_available=is_available,
        total_unlocks=unlock_count,
        location=location_display,
        expected_salary=daily_rate,
        rating=getattr(current_employee, "rating", 0.0)
    )

# --- Profile Management ---
@router.get("/profile")
async def read_employee_profile(
    current_employee: Employee = Depends(get_current_employee),
    lang: str = Depends(get_user_language)
):
    """
    Fetches the currently logged-in employee's full profile, translated.
    """
    employee_data = current_employee.localize(lang_code=lang)
    employee_data["id"] = str(current_employee.id)
    return employee_data

# --- Helper Function to Parse Salary Strings ---
def parse_salary_string(salary_input: Union[str, Dict[str, Any], int, float, Any]) -> Optional[float]:
    """
    Safely parses salary input and returns a single float amount.
    """
    if not salary_input:
        return None

    # If the frontend sent a dictionary, grab the first numeric value it can find
    if isinstance(salary_input, dict):
        return float(salary_input.get("amount") or salary_input.get("min_salary") or salary_input.get("min") or 0)

    # If the frontend sent an actual number already
    if isinstance(salary_input, (int, float)):
        return float(salary_input)

    # If the frontend sent a string (e.g., "50k - 60k")
    if isinstance(salary_input, str):
        clean_str = salary_input.replace(",", "").replace(" ", "").lower()
        if "k" in clean_str:
            clean_str = clean_str.replace("k", "000")
        
        # Find all numbers in the string
        numbers = re.findall(r'\d+', clean_str)
        
        if numbers:
            # Just grab the very first number they typed
            return float(numbers[0])

    return None

# --- Profile Completion Score Calculation ---
def calculate_profile_completion(employee: Employee) -> int:
    score = 0
    
    # Core Identity (40 points)
    if getattr(employee, "name", None): score += 10
    if getattr(employee, "title", None): score += 10
    if getattr(employee, "summary", None): score += 10
    if getattr(employee, "location_name", None): score += 10
    
    # Contact & Media (20 points)
    if getattr(employee, "email", None): score += 10
    if getattr(employee, "profile_picture_url", None): score += 10
    
    # Professional Details (40 points)
    if getattr(employee, "skills", None) and len(employee.skills) > 0: score += 10
    if getattr(employee, "work_experience", None) and len(employee.work_experience) > 0: score += 10
    if getattr(employee, "education", None) and len(employee.education) > 0: score += 10
    if getattr(employee, "resume_url", None): score += 10

    return min(score, 100)

#--- Profile Update ---
@router.patch("/profile_update", response_model=dict, status_code=status.HTTP_200_OK)
async def update_employee_profile(
    request: Request,
    profile_data: EmployeeProfileUpdate,
    background_tasks: BackgroundTasks,
    current_employee: Employee = Depends(get_current_employee),
):
    # GRAB THE RAW JSON (Bypassing Pydantic completely!)
    raw_payload = await request.json()

    # by_alias=False automatically converts 'full_name' to 'name', 'job_title' to 'title', etc.
    update_dict = profile_data.model_dump(exclude_unset=True, by_alias=False)

    if not raw_payload:
        return {
            "status": "success", 
            "message": "No changes were provided.",
            "updated_fields": []
        }
    
    # ==========================================
    # 2. OLA MAPS AUTO-GEOCODING & LOCATION
    # ==========================================
    if "location" in update_dict and isinstance(update_dict["location"], str):
        raw_loc = update_dict["location"].strip()
        
        try:
            # 1. Ask MapService for the coordinates
            coords = await MapService.get_coordinates(raw_loc)
            
            # 2. Get the address string (prefer the Map API's detailed address if available)
            if coords and coords.get("formatted_address"):
                best_address = coords["formatted_address"]
            else:
                best_address = raw_loc
                
            # 3. Clean up the Map API's mess (Deduplicate the words)
            # This turns "Indore, MP, India, MP, India" back into "Indore, MP, India"
            seen = set()
            parts = []
            for p in best_address.split(","):
                p_clean = p.strip()
                if p_clean and p_clean not in seen:
                    seen.add(p_clean)
                    parts.append(p_clean)
                    
            final_location_name = ", ".join(parts)
            
            # 4. Save the perfectly cleaned string
            current_employee.location_name = final_location_name
            
            if coords:
                # 5. Extract City, State, Country from the cleaned Map API result!
                current_employee.location = GeoLocation(
                    type="Point",
                    coordinates=[coords["longitude"], coords["latitude"]],
                    city=parts[0] if len(parts) > 0 else None,
                    state=parts[1] if len(parts) > 1 else None,
                    country=parts[2] if len(parts) > 2 else None
                )
        except Exception:
            # Fallback if MapService crashes completely
            current_employee.location_name = raw_loc
            
        del update_dict["location"]

    # ==========================================
    # 3. DIRECT FIELD MAPPING
    # ==========================================
    if update_dict.get("email") and update_dict["email"] != getattr(current_employee, "email", None):
        current_employee.email = update_dict["email"]
        current_employee.email_verified = False 

    direct_fields = [
        "name", "title", "summary", "phone", "languages", 
        "expected_salary", "age", "gender", "referred_by_id", "total_experience"
    ]
    
    for field in direct_fields:
        if field in update_dict:
            setattr(current_employee, field, update_dict[field])

    # ==========================================
    # 4. COMPLEX NESTED OBJECT MAPPING
    # ==========================================
    db_force_updates = {} # Dictionary to store aggressive overrides

    try:
        # --- Availability & Preferences ---
        if "notice_period_days" in update_dict:
            if not current_employee.availability:
                current_employee.availability = Availability()
            current_employee.availability.notice_period_days = update_dict["notice_period_days"]

        # --- Preferences ---
        pref_keys = ["preferred_job_types", "category", "preferred_roles", "preferred_locations", "remote_work"]
        if any(k in update_dict for k in pref_keys):
            if not current_employee.preferences:
                current_employee.preferences = Preferences()
            
            job_types = set(current_employee.preferences.job_types or [])
            
            if update_dict.get("preferred_job_types"):
                job_types = set(update_dict["preferred_job_types"])
                
            if update_dict.get("category"):
                job_types.add(update_dict["category"])
            if update_dict.get("preferred_roles"):
                job_types.update(update_dict["preferred_roles"])
            
            current_employee.preferences.job_types = [
                jt for jt in list(job_types) if jt and jt.lower() != "string"
            ]

            if "preferred_locations" in update_dict and update_dict["preferred_locations"]:
                current_employee.preferences.locations = [
                    loc for loc in update_dict["preferred_locations"] 
                    if loc and loc.lower() != "string"
                ]
                
            if "remote_work" in update_dict:
                current_employee.preferences.remote_ok = update_dict["remote_work"]

        # --- Arrays (Direct override to prevent ghost appending) ---
        if "skills" in raw_payload:
            # Overwrites entirely. If user sends [], it deletes all old skills.
            skills_list = raw_payload["skills"]
            current_employee.skills = [Skill(name=skill) for skill in skills_list if isinstance(skill, str)]
            db_force_updates["skills"] = [{"name": skill} for skill in skills_list if isinstance(skill, str)]

        if "education" in raw_payload:
            edu_dicts = [e for e in raw_payload["education"] if e and isinstance(e, dict)]
            current_employee.education = [Education(**d) for d in edu_dicts]
            db_force_updates["education"] = edu_dicts

        if "work_experience" in raw_payload:
            exp_dicts = [e for e in raw_payload["work_experience"] if e and isinstance(e, dict)]
            current_employee.work_experience = [WorkExperience(**d) for d in exp_dicts]
            db_force_updates["work_experience"] = exp_dicts

    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Data format error. Please check your inputs: {e.errors()}")

    # ==========================================
    # 5. METADATA & SAVING (INTEGRATED HELPER)
    # ==========================================
    if not current_employee.metadata:
        current_employee.metadata = ProfileMetadata()
        
    current_employee.metadata.updated_at = datetime.now(timezone.utc)
    
    # Dynamically calculate the score based on the newly mapped object
    current_employee.metadata.profile_completion = calculate_profile_completion(current_employee)
    
    # 1. Save standard fields and metadata normally
    await current_employee.save()
    
    # 2. FORCE MongoDB to overwrite the arrays using the raw driver
    # Also aggressively save the calculated score just in case Pydantic misses it
    if db_force_updates:
        db_force_updates["metadata.profile_completion"] = current_employee.metadata.profile_completion
        await Employee.get_motor_collection().update_one(
            {"_id": current_employee.id},
            {"$set": db_force_updates}
        )

    # ==========================================
    # 6. TRANSLATION & WEBHOOK TRIGGERS
    # ==========================================
    translatable_db_fields = ["name", "title", "summary"]
    fields_to_translate = [field for field in update_dict.keys() if field in translatable_db_fields]
    
    if fields_to_translate:
        background_tasks.add_task(
            translate_document_fields,
            str(current_employee.id),
            Employee,
            fields_to_translate,
            "hi"
        )

    await WebhookService.trigger_event("employee_profile_updated", {
        "employee_id": str(current_employee.id),
        "name": getattr(current_employee, "name", "User"),
        "location": getattr(current_employee, "location_name", "Unspecified"),
        "updated_fields": list(update_dict.keys())
    })

    return {
        "status": "success",
        "message": "Profile updated successfully!",
        "updated_fields": list(update_dict.keys()),
        "profile_summary": {
            "name": getattr(current_employee, "name", "User"),
            "job_types": getattr(current_employee.preferences, "job_types", []) if current_employee.preferences else [],
            "skills_count": len(current_employee.skills or []),
            "experience_entries": len(current_employee.work_experience or []),
            "is_profile_complete": current_employee.metadata.profile_completion == 100
        }
    }

# --- Profile Photo Upload ---
@router.post("/profile_photo_upload", status_code=status.HTTP_200_OK)
async def update_profile_photo(
    file: UploadFile = File(...), 
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Uploads a profile picture to Cloudinary, deletes the old one (if it exists),
    updates the employee's record, and returns the URL for the frontend to display.
    """
    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid file type. Only JPG, PNG, and WEBP are allowed."
        )
        
    file.file.seek(0, 2) 
    file_size = file.file.tell() 
    file.file.seek(0) 
    
    if file_size > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="File size too large. Maximum size is 5MB."
        )

    try:
        # 1. UPLOAD THE NEW PHOTO FIRST (Protects the user if the upload crashes)
        new_url = await upload_file(file, folder_name="employees")
        
        # 2. SAFELY DELETE THE OLD PHOTO FROM CLOUDINARY
        old_url = getattr(current_employee, "profile_picture_url", None)
        if old_url:
            try:
                upload_str = "/upload/"
                if upload_str in old_url:
                    after_upload = old_url.split(upload_str)[1]
                    parts = after_upload.split('/')
                    
                    if parts[0].startswith('v') and parts[0][1:].isdigit():
                        parts.pop(0) 
                    
                    public_id = "/".join(parts).rsplit('.', 1)[0]
                    cloudinary.uploader.destroy(public_id)
            except Exception as e:
                # We catch the error but don't crash the API. 
                # The user still gets their new photo even if the old one fails to delete.
                print(f"Warning: Failed to delete old Cloudinary image: {str(e)}")
        
        # 3. SAVE THE NEW URL TO THE DATABASE
        current_employee.profile_picture_url = new_url
        
        # 4. RECALCULATE SCORE & TIMESTAMPS
        if not current_employee.metadata:
            current_employee.metadata = ProfileMetadata()
            
        current_employee.metadata.profile_completion = calculate_profile_completion(current_employee)
        current_employee.metadata.updated_at = datetime.now(timezone.utc)
        
        await current_employee.save()
        
        return {
            "message": "Profile photo updated successfully", 
            "profile_picture_url": new_url,
            "new_profile_score": current_employee.metadata.profile_completion
        }
        
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# --- Profile Photo Deletion ---
@router.delete("/profile_photo_delete", response_model=dict)
async def delete_profile_picture(current_employee: Employee = Depends(get_current_employee)):
    # 1. Check if the user even has a picture
    if not current_employee.profile_picture_url:
        return {
            "status": "success", 
            "message": "No profile picture to delete."
        }

    url = current_employee.profile_picture_url

    try:
        # 2. BULLETPROOF CLOUDINARY PUBLIC_ID EXTRACTION
        # This safely handles the URL whether it has a 'v12345' version number or not
        upload_str = "/upload/"
        if upload_str in url:
            after_upload = url.split(upload_str)[1] # Gets "v1786628218/employees/w89gjhp9..."
            
            parts = after_upload.split('/')
            # If the first part is a version number (starts with 'v' and is a number), remove it
            if parts[0].startswith('v') and parts[0][1:].isdigit():
                parts.pop(0) 
            
            # Rejoin the folder and filename, then strip the extension (.jpg, .png)
            public_id_with_ext = "/".join(parts)
            public_id = public_id_with_ext.rsplit('.', 1)[0]
            
            # 3. PERMANENTLY DELETE FROM CLOUDINARY SERVERS
            delete_response = cloudinary.uploader.destroy(public_id)
            
            # If result is 'not found', it was already deleted manually on Cloudinary, which is fine!
            if delete_response.get('result') not in ['ok', 'not found']:
                raise Exception(f"Cloudinary rejected the request: {delete_response}")
                
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to delete the image from Cloudinary. Error: {str(e)}"
        )

    # 4. REMOVE FROM MONGODB & RECALCULATE SCORE
    current_employee.profile_picture_url = None
    
    # Since we removed a photo, we must recalculate their profile completion score!
    current_employee.metadata.profile_completion = calculate_profile_completion(current_employee)
    current_employee.metadata.updated_at = datetime.now(timezone.utc)
    
    await current_employee.save()

    return {
        "status": "success", 
        "message": "Profile picture successfully deleted from Cloudinary and database.",
        "new_profile_score": current_employee.metadata.profile_completion
    }

# --- Profile Updates ---
# --- STEP 1: Request OTP for new phone number ---
@router.post("/profile/send_phone_no_update_otp", status_code=status.HTTP_200_OK)
async def send_phone_update_otp(
    data: SendUpdateOtpRequest,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Generates and sends an OTP to the new phone number the user wants to switch to.
    Checks if the number is available before sending.
    """
    clean_new_phone = data.new_phone[-10:]
    
    # 1. Check if the user is typing their current number
    if current_employee.phone == clean_new_phone:
        raise HTTPException(status_code=400, detail="This is already your current phone number.")
    
    # 2. Check if the new phone is already in use by someone else
    phone_taken = await Employer.find_one({"phone": clean_new_phone}) or await Employee.find_one({"phone": clean_new_phone})
    if phone_taken:
        raise HTTPException(status_code=409, detail="This phone number is already registered to another account.")

    # 3. Generate a 4-digit OTP (Changed from 100000, 999999)
    DEV_MODE = True
    otp_code = "1234" if DEV_MODE else str(random.randint(1000, 9999))
    
    # 4. Save it to the database
    otp_record = await OTP.find_one({"phone": clean_new_phone})
    if otp_record:
        otp_record.code = otp_code
        await otp_record.save()
    else:
        await OTP(phone=clean_new_phone, code=otp_code, user_type="employee").insert()
        
    # 5. Send the SMS using your SMS service (Uncomment when ready)
    # await SmsService.send_otp(clean_new_phone, otp_code)
    
    return {
            "status": "success",
            "message": f"An OTP has been sent to {clean_new_phone}. Please verify to update your phone number."
        }


# --- STEP 2: Verify OTP and apply the update ---
@router.patch("/profile/phone_no_update", status_code=status.HTTP_200_OK)
async def update_employee_phone(
    data: UpdatePhoneRequest,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Verifies the 4-digit OTP sent to the new phone number. 
    If successful, updates the database and issues a new JWT token.
    """
    clean_new_phone = data.new_phone[-10:]

    # 1. VERIFY THE OTP FIRST
    otp_record = await OTP.find_one({"phone": clean_new_phone})
    if not otp_record or not otp_record.code or otp_record.code != data.otp_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid or expired OTP for the new phone number."
        )

    # 2. Double-check if the new phone is already registered to someone else (Safety Net)
    phone_taken = await Employer.find_one({"phone": clean_new_phone}) or await Employee.find_one({"phone": clean_new_phone})
    if phone_taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="This phone number is already registered to another account."
        )
    
    # 3. Consume/Invalidate the OTP so it cannot be reused
    otp_record.code = None 
    await otp_record.save()

    # 4. Apply the new phone number to the employee record
    current_employee.phone = clean_new_phone
    await current_employee.save()

    # 5. Generate a fresh token with the new phone number
    new_access_token = create_access_token({"sub": current_employee.phone}, user_type="employee")

    return {
        "status": "success", 
        "message": "Phone number successfully updated.", 
        "new_phone": current_employee.phone,
    }

# --- STEP 1: Request OTP for new email address ---
from datetime import datetime, timezone
import random
from fastapi import HTTPException, Depends

@router.post("/profile/email/send_otp", response_model=dict)
async def request_email_update_otp(
    data: EmailUpdateRequest,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Step 1: Takes the new email, validates it isn't in use, and sends an OTP to it.
    """
    clean_email = data.email.lower().strip()

    # 1. Check if the email is already in use by another account
    existing_user = await Employee.find_one(Employee.email == clean_email)
    if existing_user:
        raise HTTPException(status_code=400, detail="This email is already associated with another account.")

    # 2. Generate OTP (or use 1234 if in DEV_MODE)
    DEV_MODE = True
    otp_code = "1234" if DEV_MODE else str(random.randint(1000, 9999))

    # 3. THE FIX: Map the email to the 'phone' field to satisfy the OTP model requirement
    await OTP(
        phone=clean_email, 
        code=otp_code, 
        user_type="employee"
    ).insert()

    return {
        "status": "success",
        "message": f"An OTP has been sent to {clean_email}. Please verify to update your email."
    }


@router.patch("/profile/email/verify_and_update", response_model=dict)
async def verify_and_update_email(
    data: VerifyEmailUpdateRequest,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Step 2: Verifies the OTP. If valid, updates the user's email and marks it as verified.
    """
    clean_email = data.email.lower().strip()

    # ==========================================
    # 1. VERIFY OTP (Manual check to avoid OTPService phone-slicing logic)
    # ==========================================
    DEV_MODE = True
    MASTER_OTP = "1234"

    if DEV_MODE and data.otp_code == MASTER_OTP:
        print(f"⚠️ DEV BYPASS USED: Email updated to {clean_email}")
    else:
        # Check the OTP collection where we stored the email in the phone field
        otp_record = await OTP.find_one(OTP.phone == clean_email)
        
        if not otp_record or otp_record.code != data.otp_code:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP.")
            
        # Consume (delete) the OTP
        await otp_record.delete()
    # ==========================================

    # 2. UPDATE THE EMPLOYEE FIELDS
    current_employee.email = clean_email
    current_employee.email_verified = True 

    # 3. RECALCULATE PROFILE SCORE
    if not current_employee.metadata:
        from app.models.employee import ProfileMetadata # Adjust import if needed
        current_employee.metadata = ProfileMetadata()
        
    current_employee.metadata.profile_completion = calculate_profile_completion(current_employee)
    current_employee.metadata.updated_at = datetime.now(timezone.utc)

    # 4. SAVE TO DATABASE
    await current_employee.save()

    return {
        "status": "success",
        "message": "Email successfully updated and verified!",
        "updated_data": {
            "email": current_employee.email,
            "email_verified": current_employee.email_verified,
            "profile_completion": current_employee.metadata.profile_completion
        }
    }

# --- Resume Upload & Parsing ---
@router.post("/profile/upload_resume", status_code=status.HTTP_200_OK)
async def upload_and_parse_resume(
    file: UploadFile = File(...),
    current_employee = Depends(get_current_employee) 
):
    """
    Uploads the resume to Cloudinary, stores the URL, parses the data,
    and returns the URL to the frontend. Prevents duplicate storage.
    """
    if file.content_type != "application/pdf" and not file.filename.endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid file type. Only PDF resumes are accepted."
        )

    try:
        # === NEW: Check for and delete the old resume ===
        if getattr(current_employee, "resume_url", None):
            await delete_file(current_employee.resume_url)

        # Upload the new resume to Cloudinary
        resume_url = await upload_file(file, folder_name="resumes")
        current_employee.resume_url = resume_url
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    await file.seek(0)
    file_bytes = await file.read()

    try:
        raw_text = await ResumeParserService.extract_text_from_pdf(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    parsed_data = await ResumeParserService.parse_resume_to_json(raw_text)

    # --- Database Auto-Fill Logic ---
    if parsed_data.get("skills"):
        if current_employee.skills is None:
            current_employee.skills = []
            
        existing_skill_names = {skill.name.lower() for skill in current_employee.skills if hasattr(skill, "name")}
        
        for new_skill in parsed_data["skills"]:
            if isinstance(new_skill, str):
                if new_skill.lower() not in existing_skill_names:
                    current_employee.skills.append(Skill(name=new_skill))
                    existing_skill_names.add(new_skill.lower())
            elif isinstance(new_skill, dict) and "name" in new_skill:
                skill_name = new_skill["name"]
                if skill_name.lower() not in existing_skill_names:
                    current_employee.skills.append(Skill(**new_skill))
                    existing_skill_names.add(skill_name.lower())

    # Safely mapped education logic
    if parsed_data.get("education_level"):
        extracted_level = parsed_data["education_level"]
        
        # If they already have education entries, update the first one's degree
        if current_employee.education and len(current_employee.education) > 0:
            current_employee.education[0].degree = extracted_level
        else:
            # Otherwise, create a new education entry for it
            current_employee.education = [
                Education(institute="Not Specified", degree=extracted_level)
            ]                
        
    # Safely mapped experience logic
    if parsed_data.get("experience_years"):
        try:
            # Map it strictly to 'total_experience' as a float
            current_employee.total_experience = float(parsed_data["experience_years"])
        except (ValueError, TypeError):
            # Fail silently if the AI returned text instead of a number
            pass
        
    if parsed_data.get("languages"):
        current_employee.languages = parsed_data["languages"]

    # Uses the strictly matched fallback logic for WorkExperience schema
    if parsed_data.get("work_experience"):
        new_experiences = []
        for exp in parsed_data["work_experience"]:
            new_experiences.append(
                WorkExperience(
                    company_name=exp.get("company_name"),
                    job_title=exp.get("job_title"),
                    job_role=exp.get("job_role")
                )
            )
        current_employee.work_experience = new_experiences
    
    # Save everything, including the new resume_url
    await current_employee.save()
    
    # Return the resume URL alongside the success message
    return {
        "message": "Resume uploaded and data extracted successfully!",
        "resume_url": resume_url
    }

# --- Resume Download ---
@router.get("/resume/download/{employee_id}")
async def get_employee_resume_link(
    employee_id: str,
    current_user = Depends(get_any_current_user)
):
    """
    Returns the Cloudinary URL of the employee's resume.
    If no resume exists, it auto-generates one, uploads it to Cloudinary, 
    saves the link to MongoDB, and returns the URL for the frontend to handle.
    """
    # 1. Authorization and Quota Checks
    if current_user.role == "employee":
        if str(current_user.id) != employee_id:
            raise HTTPException(status_code=403, detail="You do not have permission to view this resume.")
    elif current_user.role == "employer":
        await SubscriptionService.check_quota(str(current_user.id), "download_resume")
    else:
        raise HTTPException(status_code=403, detail="Unauthorized role.")

    # 2. Fetch Employee
    employee = await Employee.get(ObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    # 3. Generate & Upload if no resume exists in the database
    if not getattr(employee, "resume_url", None):
        
        # A. Generate PDF (returns BytesIO)
        pdf_content = ResumeService.generate_pdf(employee)
        employee_name = getattr(employee, "name", "Candidate") or "Candidate"
        safe_name = "".join([c for c in employee_name if c.isalnum() or c == ' ']).rstrip().replace(" ", "_")
        
        try:
            # B. Upload the raw bytes directly to Cloudinary
            upload_result = cloudinary.uploader.upload(
                pdf_content.getvalue(), 
                resource_type="raw", 
                public_id=f"resumes/{employee_id}_{safe_name}",
                format="pdf"
            )
            
            # C. Save the new Cloudinary URL permanently to MongoDB
            employee.resume_url = upload_result.get("secure_url")
            await employee.save()
            
        except Exception as e:
            print(f"Cloudinary Upload Error: {str(e)}") # Useful for server logs
            raise HTTPException(status_code=500, detail="Failed to upload auto-generated resume to cloud storage.")

    # 4. Consume the Quota (Only executes if URL existed or upload succeeded)
    if current_user.role == "employer":
        await SubscriptionService.increment_usage(str(current_user.id), "download_resume")

    # 5. Return ONLY the JSON data, leaving the actual file download to the frontend
    return {
        "status": "success",
        "message": "Resume link retrieved successfully.",
        "resume_url": employee.resume_url
    }

# --- Job Applications ---
@router.post("/jobs/apply/{job_id}", status_code=status.HTTP_201_CREATED)
async def apply_for_job(job_id: str, current_employee = Depends(get_current_employee)):
    try:
        job = await Job.get(ObjectId(job_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Job ID format")

    if not job or job.status != "published":
        raise HTTPException(status_code=404, detail="Job not found or inactive.")

    existing_application = await JobApplication.find_one({
        "employee_id": current_employee.id,
        "job_id": job.id
    })
    
    if existing_application:
        raise HTTPException(status_code=400, detail="You have already applied for this job.")

    new_app = JobApplication(
        job_id=job.id,
        employee_id=current_employee.id,
        employer_id=PydanticObjectId(job.employer_id), 
        status=ApplicationStatus.APPLIED
    )
    
    await new_app.insert()

    job.applicants_count += 1
    await job.save()

    await NotificationService.notify_user(
        user_id=str(job.employer_id),
        title="New Application Received!",
        message=f"{current_employee.name} just applied for your {getattr(job, 'job_title', 'Job')} role.", 
        notif_type=NotificationType.NEW_APPLICANT,
        related_entity_id=str(job.id)
    )

    return {"message": "Application submitted successfully!", "status": new_app.status}

# --- Applied Jobs ---
@router.get("/jobs/applied", response_model=List[dict])
async def get_applied_jobs(
    current_employee: Employee = Depends(get_current_employee),
    lang: str = Depends(get_user_language)
):
    """
    Retrieves a list of all jobs the current employee has applied for.
    Translates job titles and employer names.
    """
    applications = await JobApplication.find(
        JobApplication.employee_id == current_employee.id
    ).to_list()

    if not applications:
        return []

    applied_jobs_data = []

    for app in applications:
        job = await Job.get(app.job_id)
        if job:
            employer = await Employer.get(job.employer_id)
            
            # Localize database records securely
            loc_job = job.localize(lang_code=lang)
            if employer:
                if hasattr(employer, "localize"):
                    loc_employer = employer.localize(lang_code=lang)
                else:
                    loc_employer = employer.model_dump()
            else:
                loc_employer = {}

            applied_jobs_data.append({
                "application_id": str(app.id),
                "job_id": str(job.id),
                "job_title": loc_job.get("job_title", "Unknown Title"),
                "company_name": loc_employer.get("company_name", "Unknown Company"),
                "status": getattr(app, "status", "pending"),
                "applied_at": getattr(app, "applied_at", app.id.generation_time)
            })

    return applied_jobs_data

# --- GET ALL SAVED JOBS ---
@router.get("/jobs/saved", response_model=List[dict])
async def get_saved_jobs(
    current_employee = Depends(get_current_employee),
    lang: str = Depends(get_user_language) 
):
    """
    Retrieves the full details of all jobs the employee has saved, translated.
    """
    if not current_employee.saved_job_ids:
        return []

    saved_jobs_data = []
    
    for job_id in current_employee.saved_job_ids:
        job = await Job.get(job_id)

        if job:
            employer = await Employer.get(job.employer_id)
            
            # Localize database records
            loc_job = job.localize(lang_code=lang)
            if employer:
                if hasattr(employer, "localize"):
                    loc_employer = employer.localize(lang_code=lang)
                else:
                    loc_employer = employer.model_dump()
            else:
                loc_employer = {}

            salary_str = "Not specified"
            if job.min_fixed_salary and job.max_fixed_salary:
                salary_str = f"₹{job.min_fixed_salary} - ₹{job.max_fixed_salary}"
            elif job.min_fixed_salary:
                salary_str = f"From ₹{job.min_fixed_salary}"

            saved_jobs_data.append({
                "job_id": str(job.id),
                "job_title": loc_job.get("job_title", "Unknown Title"),
                "company_name": loc_employer.get("company_name", "Unknown Company"),
                "location": loc_job.get("job_city", "Not specified"),
                "expected_salary": salary_str,
                "created_at": getattr(job, "created_at", None)
            })
        else:
            current_employee.saved_job_ids.remove(job_id)
            await current_employee.save()

    return saved_jobs_data

# --- SAVE A JOB ---
@router.post("/jobs/{job_id}/save")
async def save_job_for_later(job_id: str, current_employee: Employee = Depends(get_current_employee)):
    job = await Job.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    if job_id not in current_employee.saved_job_ids:
        current_employee.saved_job_ids.append(job_id)
        await current_employee.save()
        return {"message": "Job saved successfully", "saved": True}
        
    return {"message": "Job is already saved", "saved": True}

# --- UNSAVE A JOB ---
@router.delete("/jobs/{job_id}/unsave")
async def unsave_job(job_id: str, current_employee: Employee = Depends(get_current_employee)):
    if job_id in current_employee.saved_job_ids:
        current_employee.saved_job_ids.remove(job_id)
        await current_employee.save()
        return {"message": "Job removed from saved list", "saved": False}
        
    return {"message": "Job was not in saved list", "saved": False}

# --- to view the company profile ---
@router.get("/company_profile/{employer_id}", response_model=dict, status_code=status.HTTP_200_OK)
async def get_public_company_profile(
    employer_id: str,
    lang: str = Depends(get_user_language)
):
    """
    Retrieves the translated basic, public-facing profile of a company/employer.
    """
    try:
        parsed_id = PydanticObjectId(employer_id)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Employer ID format.")

    employer = await Employer.get(parsed_id)
    
    if not employer:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company profile not found.")
        
    loc_emp = employer.localize(lang_code=lang)
        
    return {
        "employer_id": str(employer.id),
        "recruiter_name": loc_emp.get("name", "Not Provided"),
        "company_name": loc_emp.get("company_name", "Not Provided"),
        "gstin": loc_emp.get("gstin", "Not Provided"),
        "logo_url": loc_emp.get("logo_url", None),
        "founded_year": loc_emp.get("founded_year", "Not Provided"),
        "website": loc_emp.get("website", "Not Provided"),
        "company_size": loc_emp.get("company_size", "Not Provided"),
        "company_type": loc_emp.get("company_type", "Not Provided"),
        "industry": loc_emp.get("industry", "Not Provided"),
        "description": loc_emp.get("description", "Not Provided"),
        "social_profiles": loc_emp.get("social_profiles", {}),
        "address": loc_emp.get("address", "Not Provided")
    }

# --- Category of jobs ---
@router.get("/categories", response_model=List[str])
async def get_all_categories(lang: str = Depends(get_user_language)):
    """
    Returns a simple list of translated category names.
    """
    categories = await Category.find(Category.is_active == True).to_list()
    # Assuming the Category model is also a TranslatableDocument
    return [c.localize(lang_code=lang).get("name", c.name) for c in categories]

# --- Reviews & Feedback ---
@router.get("/{employee_id}/reviews", response_model=List[dict])
async def get_employee_reviews(
    employee_id: str,
    lang: str = Depends(get_user_language)
):
    """
    Returns all reviews and comments for a specific employee, translated.
    """
    try:
        reviews = await Review.find(
            Review.employee_id == ObjectId(employee_id)
        ).sort("-created_at").to_list()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid employee ID format")

    return [{
        "rating": r.rating,
        "comment": r.localize(lang_code=lang).get("comment", r.comment),
        "date": r.created_at
    } for r in reviews]