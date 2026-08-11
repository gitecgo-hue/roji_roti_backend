# --- IMPORTS ---
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, Response, BackgroundTasks
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field, ConfigDict, ValidationError
from typing import List, Optional, Dict, Any, Union
from datetime import datetime, date, timezone
from bson import ObjectId
from beanie import PydanticObjectId
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
    EmployeeProfileUpdate,
    EmployeeResponse, 
    AvailabilityUpdate,
    EmployeeDashboardResponse,
    WorkExperienceInput,
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
    SalaryExpectation,
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
from app.services.webhooks import WebhookService 
from app.services.resumes import ResumeService
from app.services.subscriptions import SubscriptionService
from app.services.recommendation import RecommendationService
from app.services.parser import ResumeParserService
from app.services.cloudinary_service import upload_file
from app.services.cloudinary_service import delete_file
from app.services.location import OlaMapsService

# --- Utilities Imports ---
from app.utils.storage import StorageService
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
    job_title: str
    company_name: Optional[str] = None
    duration_months: Optional[int] = None

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
    daily_rate = current_employee.salary_expectation.min if current_employee.salary_expectation else None

    return EmployeeDashboardResponse(
        name=loc_emp.get("name", "User"),
        category=category_display,
        is_available=is_available,
        total_unlocks=unlock_count,
        location=location_display,
        daily_rate=daily_rate,
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
def parse_salary_string(salary_input: Union[str, Dict[str, Any], Any]) -> SalaryExpectation:
    """
    Safely parses salary expectations whether the frontend sends a raw string ('50k')
    or a JSON dictionary ({"min": 50000, "max": 60000}).
    """
    if not salary_input:
        return SalaryExpectation()

    # SCENARIO A: The frontend sent a dictionary (JSON object)
    if isinstance(salary_input, dict):
        return SalaryExpectation(
            min=salary_input.get("min"),
            max=salary_input.get("max"),
            currency=salary_input.get("currency", "INR")
        )

    # SCENARIO B: The frontend sent a raw string
    if isinstance(salary_input, str):
        clean_str = salary_input.replace(",", "").replace(" ", "").lower()
        if "k" in clean_str:
            clean_str = clean_str.replace("k", "000")
        
        numbers = re.findall(r'\d+', clean_str)
        
        if not numbers:
            return SalaryExpectation()
            
        if len(numbers) == 1:
            return SalaryExpectation(min=float(numbers[0]), max=None)
            
        min_val = float(numbers[0])
        max_val = float(numbers[1])
        
        # Swap them if the user accidentally typed "60000-50000"
        if min_val > max_val:
            min_val, max_val = max_val, min_val
            
        return SalaryExpectation(min=min_val, max=max_val)

    # Fallback for unexpected data types
    return SalaryExpectation()

#--- Profile Update ---
@router.patch("/profile_update", response_model=dict, status_code=status.HTTP_200_OK)
async def update_employee_profile(
    profile_data: EmployeeProfileUpdate,
    background_tasks: BackgroundTasks, # Added for translation
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Unified endpoint to update or complete an employee profile.
    - Auto-geocodes locations via Ola Maps.
    - Safely maps flat frontend fields to rich database nested objects.
    - Triggers translation & webhooks in the background.
    """
    update_dict = profile_data.model_dump(exclude_unset=True)
    
    if not update_dict:
        return {
            "status": "success", 
            "message": "No changes were provided.",
            "updated_fields": []
        }

    # ==========================================
    # 2. OLA MAPS AUTO-GEOCODING & LOCATION
    # ==========================================
    # The frontend payload sends the city string as "location"
    if "location" in update_dict and isinstance(update_dict["location"], str):
        city_name = update_dict["location"]
        
        try:
            # Try to get coordinates for the rich GeoLocation object
            coords = await MapService.get_coordinates(city_name)
            if coords:
                current_employee.location = GeoLocation(
                    type="Point",
                    coordinates=[coords["longitude"], coords["latitude"]],
                    city=city_name
                )
                # Save the formatted address or fallback to the typed name
                current_employee.location_name = coords.get("formatted_address", city_name)
            else:
                # Fallback if map service returns nothing
                current_employee.location_name = city_name
        except Exception:
            # Fallback if map service is down
            current_employee.location_name = city_name
            
        # Remove 'location' from update_dict so the basic field mapper below doesn't get confused
        del update_dict["location"]

    # ==========================================
    # 3. DIRECT FIELD MAPPING
    # ==========================================
    if update_dict.get("email") and update_dict["email"] != getattr(current_employee, "email", None):
        current_employee.email = update_dict["email"]
        current_employee.email_verified = False 

    field_mapping = {
        "full_name": "name",
        "job_title": "title",
        "about_you": "summary",
        "phone": "phone",
        "languages": "languages",
        "current_salary": "current_salary",
        "age": "age",
        "gender": "gender",
        "referred_by_id": "referred_by_id",
        "experience_years": "experience_years",
        "total_experience": "total_experience"
    }
    
    for flat_field, db_field in field_mapping.items():
        if flat_field in update_dict:
            setattr(current_employee, db_field, update_dict[flat_field])

    # ==========================================
    # 4. COMPLEX NESTED OBJECT MAPPING
    # ==========================================
    try:
        # --- Availability & Preferences ---
        if any(k in update_dict for k in ["notice_period_days"]):
            if not current_employee.availability:
                current_employee.availability = Availability()
            if "notice_period_days" in update_dict:
                current_employee.availability.notice_period_days = update_dict["notice_period_days"]

        pref_keys = ["preferred_job_types", "category", "preferred_roles", "preferred_locations", "remote_work"]
        if any(k in update_dict for k in pref_keys):
            if not current_employee.preferences:
                current_employee.preferences = Preferences()
            
            # Start with existing job types
            job_types = set(current_employee.preferences.job_types or [])
            
            # FIXED: If the frontend sends a new list, OVERWRITE the old one (allows deleting tags)
            if "preferred_job_types" in update_dict:
                job_types = set(update_dict["preferred_job_types"])
                
            # Add category or roles if provided
            if update_dict.get("category"):
                job_types.add(update_dict["category"])
            if update_dict.get("preferred_roles"):
                job_types.update(update_dict["preferred_roles"])
            
            # FIXED: Save the list, but explicitly filter out empty values and Swagger's "string"
            current_employee.preferences.job_types = [
                jt for jt in list(job_types) if jt and jt.lower() != "string"
            ]

            # FIXED: Apply the same "string" filter to locations
            if "preferred_locations" in update_dict:
                current_employee.preferences.locations = [
                    loc for loc in update_dict["preferred_locations"] 
                    if loc and loc.lower() != "string"
                ]
                
            if "remote_work" in update_dict:
                current_employee.preferences.remote_ok = update_dict["remote_work"]

        # --- Salary Expectations ---
        if update_dict.get("salary_expectation"):
            current_employee.salary_expectation = parse_salary_string(update_dict["salary_expectation"])

        # --- Arrays (Skills, Education, Work Experience) ---
        if update_dict.get("skills"):
            current_employee.skills = [Skill(name=skill_name) for skill_name in update_dict["skills"]]

        # FIXED: Overwrite the education array completely instead of appending
        if "education" in update_dict:
            new_education = []
            for edu in update_dict["education"]:
                if isinstance(edu, dict):
                    new_education.append(
                        Education(
                            institution=edu.get("institute_school", "Not Specified"),
                            degree=edu.get("education_level"),
                            end_year=int(edu.get("year")) if str(edu.get("year") or "").isdigit() else None
                        )
                    )
            current_employee.education = new_education
            
        # FIXED: Only update the top-level education string, don't blindly append to the array!
        if update_dict.get("education_level"):
            current_employee.education_level = update_dict["education_level"]
            if current_employee.education and len(current_employee.education) > 0:
                current_employee.education[0].degree = update_dict["education_level"]
            else:
                current_employee.education = [Education(institution="Not Specified", degree=update_dict["education_level"])]

        # FIXED: Matched exact DB schema keys (company_name, job_title) so they don't save as null
        if "work_experience" in update_dict:
            new_exp = []
            for exp in update_dict["work_experience"]:
                if isinstance(exp, dict):
                    new_exp.append(
                        WorkExperience(
                            job_title=exp.get("job_title"),
                            job_role=exp.get("job_role"),
                            company_name=exp.get("company_name"),
                            experience_years=exp.get("experience_years"),
                            experience_months=exp.get("experience_months"),
                            currently_working_here=exp.get("currently_working_here")
                        )
                    )
            current_employee.work_experience = new_exp
            
        # ==========================================
        # 5. METADATA & SAVING
        # ==========================================
        # Force the updated_at timestamp to refresh right before saving
        if not current_employee.metadata:
            current_employee.metadata = ProfileMetadata()
        current_employee.metadata.updated_at = datetime.now(timezone.utc)
        
        # Calculate Profile Completion %
        if getattr(current_employee, "name", None) and getattr(current_employee, "skills", None):
            current_employee.metadata.profile_completion = 100
            
        await current_employee.save()
        
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Data format error. Please check your inputs: {e.errors()}")

    # ==========================================
    # 6. TRANSLATION & WEBHOOK TRIGGERS
    # ==========================================
    # Check if translatable text fields were modified
    translatable_db_fields = ["name", "title", "summary"]
    fields_to_translate = [db_field for flat_field, db_field in field_mapping.items() 
                           if flat_field in update_dict and db_field in translatable_db_fields]
    
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
            "is_profile_complete": bool(getattr(current_employee, "name", None) and getattr(current_employee, "skills", None))
        }
    }

# --- Profile Photo Upload ---
@router.post("/profile_photo_upload", status_code=status.HTTP_200_OK)
async def update_profile_photo(
    file: UploadFile = File(...), 
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Uploads a profile picture to Cloudinary, updates the employee's record,
    and returns the URL for the frontend to display.
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
        url = await upload_file(file, folder_name="employees")
        
        # Standardized to 'profile_picture_url' to match the delete endpoint
        current_employee.profile_picture_url = url
        await current_employee.save()
        
        # Now returns the URL directly to the frontend
        return {
            "message": "Profile photo updated successfully", 
            "profile_picture_url": url
        }
        
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

# --- Profile Photo Deletion ---
@router.delete("/profile_photo_delete", status_code=status.HTTP_200_OK)
async def delete_employee_profile_picture(current_employee = Depends(get_current_employee)):
    if not current_employee.profile_picture_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You do not have a profile picture to delete.")

    deletion_successful = delete_file(current_employee.profile_picture_url)

    if not deletion_successful:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete the image from the cloud provider.")

    current_employee.profile_picture_url = None
    await current_employee.save()

    return {"message": "Profile picture deleted successfully."}

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
    otp_code = str(random.randint(1000, 9999))
    
    # 4. Save it to the database
    otp_record = await OTP.find_one({"phone": clean_new_phone})
    if otp_record:
        otp_record.code = otp_code
        await otp_record.save()
    else:
        await OTP(phone=clean_new_phone, code=otp_code).insert()
        
    # 5. Send the SMS using your SMS service (Uncomment when ready)
    # await SmsService.send_otp(clean_new_phone, otp_code)
    
    return {"status": "success", "message": "OTP sent to new phone number."}


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
            status_code=status.HTTP_401_UNAUTHORIZED, 
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
    new_access_token = create_access_token(data={"sub": current_employee.phone})

    return {
        "status": "success", 
        "message": "Phone number successfully updated.", 
        "new_phone": current_employee.phone,
        "access_token": new_access_token
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
                Education(institution="Not Specified", degree=extracted_level)
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
async def download_employee_resume(
    employee_id: str, 
    current_user = Depends(get_any_current_user)
):
    # 1. Authorization and Quota Checks
    if current_user.role == "employee":
        if str(current_user.id) != employee_id:
            raise HTTPException(status_code=403, detail="You do not have permission to download this resume.")
    elif current_user.role == "employer":
        await SubscriptionService.check_quota(str(current_user.id), "download_resume")
    else:
        raise HTTPException(status_code=403, detail="Unauthorized role.")

    # 2. Fetch Employee
    employee = await Employee.get(ObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    # 3. If they uploaded a custom resume, redirect them to the inline Cloudinary URL
    if getattr(employee, "resume_url", None):
        return RedirectResponse(url=employee.resume_url)
        
    # 4. If no custom resume exists, generate one on the fly
    pdf_content = ResumeService.generate_pdf(employee)
    employee_name = getattr(employee, "name", "Candidate") or "Candidate"
    safe_name = "".join([c for c in employee_name if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    
    # 5. Return the dynamically generated PDF inline
    return Response(
        content=pdf_content.getvalue(),
        media_type="application/pdf",
        # CHANGED "attachment" to "inline" so it opens in the browser!
        headers={"Content-Disposition": f'inline; filename="{safe_name}_Resume.pdf"'}
    )

# --- Status & Visibility ---
@router.patch("/availability", status_code=status.HTTP_200_OK)
async def update_availability_status(
    data: AvailabilityUpdate,
    current_employee: Employee = Depends(get_current_employee)
):
    current_employee.availability_status = data.is_available
    await current_employee.save()
    status_label = "Available" if data.is_available else "Not Available" 
    
    return {
        "message": f"Your status has been updated to {status_label}.",
        "availability_status": current_employee.availability_status
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
        "industry": loc_emp.get("industry", "Not Provided"),
        "email": loc_emp.get("email", "Not Provided"),
        "logo_url": loc_emp.get("logo_url", None)
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