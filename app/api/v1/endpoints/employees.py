# --- IMPORTS ---
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, Response
from fastapi.responses import StreamingResponse, RedirectResponse
from pydantic import BaseModel, EmailStr, Field, ConfigDict, ValidationError
from typing import List, Optional
from datetime import datetime, date
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
    get_current_user
    )

# --- Schema Imports ---
from app.schemas.employee import ( # There is a duplicate EmployeeProfileUpdate import
    EmployeeProfileUpdate,
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
    Employee,
    GeoLocation,
    Skill,
    WorkExperience,
    Education,
    SalaryExpectation,
    ProfileDocument
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

# --- Utilities Imports ---
from app.utils.storage import StorageService
from app.utils.maps import MapService


router = APIRouter()

# --- PYDANTIC SCHEMAS FOR PROFILE COMPLETION & UPDATES ---
class LocationInput(BaseModel):
    latitude: float
    longitude: float

class WorkExperienceInput(BaseModel):
    job_title: str
    company_name: Optional[str] = None
    duration_months: Optional[int] = None

class CompleteEmployeeProfileRequest(BaseModel):
    category: str
    preferred_roles: Optional[List[str]] = []
    location_name: str
    location: Optional[LocationInput] = Field(default=None, description="Backend will auto-fill this using Ola Maps")
    preferred_locations: List[str] = []
    
    # Professional Details
    experience: int = 0
    work_experience: Optional[List[WorkExperienceInput]] = []
    skills: Optional[List[str]] = []
    
    # Basic Details
    languages: List[str] = []
    current_salary: Optional[str] = None
    expected_salary: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    education_level: Optional[str] = None
    email: Optional[EmailStr] = None
    referred_by_id: Optional[str] = None

class UpdatePhoneRequest(BaseModel):
    new_phone: str = Field(..., description="The new 10-digit mobile number")
    otp_code: str = Field(..., description="The 6-digit OTP sent to the NEW number")

# --- Profile Completion ---
@router.patch("/profile/complete", response_model=dict, status_code=status.HTTP_200_OK)
async def complete_employee_profile(
    data: CompleteEmployeeProfileRequest,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Completes the employee's profile after they have verified their phone number.
    Requires the access_token received from the Auth endpoint.
    """
    # OLA MAPS MAGIC: Auto-Geocode the typed location
    if data.location_name and not getattr(data, "location", None):
        coords = await MapService.get_coordinates(data.location_name)
        
        if coords:
            # Assuming LocationInput is imported and available
            data.location = LocationInput(
                latitude=coords["latitude"],
                longitude=coords["longitude"]
            )
            data.location_name = coords["formatted_address"]
        else:
            raise HTTPException(
                status_code=400, 
                detail=f"Could not find coordinates for '{data.location_name}'. Please try a more specific area."
            )
    elif not getattr(data, "location", None) and not data.location_name:
        raise HTTPException(status_code=400, detail="A location name must be provided.")

    # UPDATE DATABASE RECORD (Safe Unpacking & Conversion)    
    # Location Data
    if getattr(data, "location", None):
        geo_location = GeoLocation(
            type="Point",
            coordinates=[data.location.longitude, data.location.latitude]
        )
        # The field in the model is 'location', not 'current_location'
        current_employee.location = geo_location
    
    if data.location_name:
        if current_employee.location:
            current_employee.location.city = data.location_name

    if getattr(data, "preferred_locations", None):
        current_employee.preferred_locations = data.preferred_locations

    # Basic & Demographic Details
    if getattr(data, "email", None):
        current_employee.email = data.email
    if getattr(data, "current_salary", None):
        current_employee.current_salary = data.current_salary
    if getattr(data, "expected_salary", None):
        current_employee.expected_salary = data.expected_salary
    if getattr(data, "age", None) is not None:
        current_employee.age = data.age
    if getattr(data, "gender", None):
        current_employee.gender = data.gender
    if getattr(data, "languages", None):
        current_employee.languages = data.languages
    if getattr(data, "education_level", None):
        # Using the Education sub-model from the Ultimate Schema
        from app.models.employee import Education
        if not current_employee.education:
            current_employee.education = []
        current_employee.education.append(Education(institution="Not Specified", degree=data.education_level))

    # The Employee model does not have 'category' or 'trade_category' fields.
    # This data should be stored in a different field, e.g., preferences.job_types.
    if getattr(data, "category", None):
        if not current_employee.preferences:
            from app.models.employee import Preferences
            current_employee.preferences = Preferences()
        if data.category not in current_employee.preferences.job_types:
            current_employee.preferences.job_types.insert(0, data.category)

    if getattr(data, "preferred_roles", None):
        if not current_employee.preferences:
            from app.models.employee import Preferences
            current_employee.preferences = Preferences()
        for role in data.preferred_roles:
            if role not in current_employee.preferences.job_types:
                current_employee.preferences.job_types.append(role)

    # Skills Array (Model Conversion)
    # The input `data.skills` is a list of strings, not objects.
    if getattr(data, "skills", None):
        current_employee.skills = [Skill(name=s) for s in data.skills]

    # Work Experience Array (Model Conversion)
    # The input `WorkExperienceInput` is simpler than the `WorkExperience` model.
    if getattr(data, "work_experience", None):
        current_employee.work_experience = [
            WorkExperience(
                company=exp.company_name,
                title=exp.job_title,
                start_date=date.today() # Placeholder, as input schema is missing this
            ) for exp in data.work_experience
        ]
        
    if getattr(data, "referred_by_id", None):
        current_employee.referred_by_id = data.referred_by_id

    # Save to MongoDB
    await current_employee.save()

    # TRIGGER WEBHOOK & RETURN
    await WebhookService.trigger_event("employee_registered", {
        "employee_id": str(current_employee.id),
        "name": getattr(current_employee, "name", "User"),
        "category": getattr(current_employee, "category", "Unspecified"),
        "location": getattr(current_employee, "location_name", "Unspecified")
    })
    
    return {
        "status": "success",
        "message": "Profile completed successfully!",
        "profile_summary": {
            "name": getattr(current_employee, "name", "User"),
            "roles": getattr(current_employee, "preferred_roles", []),
            "skills_count": len(getattr(current_employee, "skills", []) or []),
            "experience_entries": len(getattr(current_employee, "work_experience", []) or [])
        },
        "employee": EmployeeResponse(
            id=str(current_employee.id),
            phone=current_employee.phone,
            name=getattr(current_employee, "name", "User"),
            category=getattr(current_employee, "category", None),
            availability_status=getattr(current_employee, "availability_status", True),
            rating=getattr(current_employee, "rating", 0.0),
            created_at=current_employee.created_at
        )
    }

# --- Employee Dashboard & Stats ---
@router.get("/dashboard", response_model=EmployeeDashboardResponse)
async def get_employee_dashboard(
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Returns real-time stats of how many employers have 'unlocked' this employee.
    Safely handles incomplete profiles by providing fallback strings.
    """
    unlock_count = await ContactUnlock.find(
        ContactUnlock.employee_id == current_employee.id
    ).count()

    # Safely access nested properties that are part of the new model structure
    category_display = (current_employee.preferences.job_types[0]
                        if current_employee.preferences and current_employee.preferences.job_types
                        else "Profile Incomplete")
    
    is_available = current_employee.availability.is_available if current_employee.availability else False
    location_display = current_employee.location.city if current_employee.location and current_employee.location.city else "Location pending"
    daily_rate = current_employee.salary_expectation.min if current_employee.salary_expectation else None

    return EmployeeDashboardResponse(
        name=current_employee.name or "User",
        category=category_display,
        is_available=is_available,
        total_unlocks=unlock_count,
        location=location_display,
        daily_rate=daily_rate,
        rating=getattr(current_employee, "rating", 0.0)
    )

# --- Profile Management ---
@router.get("/profile")
async def read_employee_profile(current_employee: Employee = Depends(get_current_employee)):
    """
    Fetches the currently logged-in employee's full profile.
    """
    # Convert the Beanie/Pydantic document to a dictionary
    employee_data = current_employee.model_dump()
    
    # Explicitly convert the MongoDB ObjectId to a string
    employee_data["id"] = str(current_employee.id)
    
    # If nested objects are models, model_dump() handles them, 
    # but returning the dictionary directly bypasses strict validation mismatches.
    return employee_data

# --- Profile Photo Upload ---
@router.post("/profile_photo_upload", status_code=status.HTTP_200_OK)
async def update_profile_photo(
    file: UploadFile = File(...), 
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Uploads a profile picture to Cloudinary and updates the employee's record.
    """
    # 1. Validate file type
    allowed_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid file type. Only JPG, PNG, and WEBP are allowed."
        )
        
    # 2. Validate file size (e.g., max 5MB)
    file.file.seek(0, 2) # Go to the end of the file
    file_size = file.file.tell() # Get the size
    file.file.seek(0) # Reset the cursor back to the beginning for reading
    
    if file_size > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size too large. Maximum size is 5MB."
        )

    try:
        # 3. Upload to Cloudinary
        # Storing in the "employees" folder to match your previous setup
        url = await upload_file(file, folder_name="employees")
        
        # 4. Save the new URL to the user's database document
        current_employee.photo_url = url
        await current_employee.save()
        
        return {
            "message": "Profile photo updated successfully",
            "photo_url": url
        }
        
    except ValueError as e:
        # Catches the error thrown by our Cloudinary service
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=str(e)
        )

# --- Profile Photo Deletion ---
@router.delete("/profile_photo_delete", status_code=status.HTTP_200_OK)
async def delete_employee_profile_picture(
    current_employee = Depends(get_current_employee) # type: Employee
):
    """
    Deletes the logged-in employee's profile picture from Cloudinary and the database.
    """
    if not current_employee.profile_picture_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="You do not have a profile picture to delete."
        )

    # 1. Delete from Cloudinary
    deletion_successful = delete_image_from_cloudinary(current_employee.profile_picture_url)
    
    if not deletion_successful:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete the image from the cloud provider."
        )

    # 2. Remove the URL from the database and save
    current_employee.profile_picture_url = None
    await current_employee.save()

    return {"message": "Profile picture deleted successfully."}

# --- Profile Update ---
@router.put("/profile_update", response_model=dict, status_code=status.HTTP_200_OK)
async def update_employee_profile(
    profile_data: EmployeeProfileUpdate,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Updates the candidate's personal and professional details safely by 
    mapping flat frontend fields to rich database objects.
    """
    update_dict = profile_data.model_dump(exclude_unset=True)
    
    # Handle Email Changes
    if update_dict.get("email") and update_dict["email"] != getattr(current_employee, "email", None):
        current_employee.email = update_dict["email"]
        current_employee.email_verified = False 

    # Map Direct/Simple Fields
    direct_fields = ["name", "title", "location_name", "total_experience"]
    for field in direct_fields:
        if field in update_dict:
            setattr(current_employee, field, update_dict[field])

    # --- EXPANDED SAFETY NET: Put object creation inside the try block ---
    try:
        # Map Complex Fields 
        if update_dict.get("skills"):
            current_employee.skills = [Skill(name=skill_name) for skill_name in update_dict["skills"]]

        if update_dict.get("expected_salary"):
            current_employee.salary_expectation = SalaryExpectation(
                min=update_dict["expected_salary"],
                max=update_dict["expected_salary"]
            )

        if update_dict.get("education"):
            current_employee.education = [Education(institution=update_dict["education"])]

        # If Swagger sends "string" here, the HttpUrl validation will safely trigger the except block!
        if update_dict.get("resume_url"):
            current_employee.documents = [ProfileDocument(type="resume", url=update_dict["resume_url"])]
            
        # Safely save the mapped data
        await current_employee.save()
        
    except ValidationError as e:
        # Now catches errors during BOTH object creation and database saving!
        raise HTTPException(
            status_code=422, 
            detail=f"Data format error. Please check your inputs (like ensuring URLs are actually URLs): {e.errors()}"
        )
    
    return {
        "message": "Profile updated successfully",
        "name": getattr(current_employee, "name", None),
        "title": getattr(current_employee, "title", None),
        "is_profile_complete": bool(
            getattr(current_employee, "name", None) and 
            getattr(current_employee, "skills", None) and 
            getattr(current_employee, "total_experience", None)
        )
    }

# --- Profile Updates ---
@router.patch("/profile/phone_no_update", status_code=status.HTTP_200_OK)
async def update_employee_phone(
    data: UpdatePhoneRequest,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Securely updates the employee's phone number after verifying an OTP sent to the NEW number.
    """
    clean_new_phone = data.new_phone[-10:]

    if current_employee.phone == clean_new_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="This is already your current phone number."
        )

    phone_taken = await Employer.find_one({"phone": clean_new_phone}) or \
                  await Employee.find_one({"phone": clean_new_phone})
    
    if phone_taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="This phone number is already registered to another account."
        )

    otp_record = await OTP.find_one({"phone": clean_new_phone})
    # Compare the provided OTP with the one stored in the database
    if not otp_record or not otp_record.code or otp_record.code != data.otp_code:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid or expired OTP for the new phone number."
        )
    
    # Consume the OTP after successful verification
    otp_record.code = None 
    await otp_record.save()

    current_employee.phone = clean_new_phone
    await current_employee.save()

    return {
        "status": "success", 
        "message": "Phone number successfully updated.", 
        "new_phone": current_employee.phone
    }

# --- Resume Upload & Parsing ---
from fastapi import APIRouter, UploadFile, File, HTTPException, status, Depends
# Ensure Employee, WorkExperienceInput, get_current_employee, ResumeParserService are imported
from app.services.cloudinary_service import upload_file

@router.post("/profile/upload_resume", status_code=status.HTTP_200_OK)
async def upload_and_parse_resume(
    resume_file: UploadFile = File(...),
    current_employee: Employee = Depends(get_current_employee)
):
    """
    1. Uploads the PDF to Cloudinary.
    2. Extracts text from the PDF.
    3. Uses AI to parse the text into structured data.
    4. Auto-fills the employee's profile in the database.
    """
    # 1. Strict Validation for PDFs
    if resume_file.content_type != "application/pdf" and not resume_file.filename.endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid file type. Only PDF resumes are accepted."
        )

    # 2. Upload to Cloudinary
    try:
        resume_url = await upload_file(resume_file, folder_name="candidate_resumes")
        current_employee.resume_url = resume_url
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # 3. Read bytes for PDF text extraction
    # CRITICAL: Reset the file pointer to the beginning because Cloudinary just read it!
    await resume_file.seek(0)
    file_bytes = await resume_file.read()

    # 4. Extract Text from the PDF
    try:
        raw_text = await ResumeParserService.extract_text_from_pdf(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # 5. Ask the AI to parse the text into a structured dictionary
    parsed_data = await ResumeParserService.parse_resume_to_json(raw_text)

    # 6. Auto-Fill the Database Profile!
    if parsed_data.get("skills"):
        existing_skills = set(current_employee.skills or [])
        existing_skills.update(parsed_data["skills"])
        current_employee.skills = list(existing_skills)
        
    if parsed_data.get("education_level"):
        current_employee.education_level = parsed_data["education_level"]
        
    if parsed_data.get("experience_years"):
        current_employee.experience_years = parsed_data["experience_years"]
        current_employee.experience = parsed_data["experience_years"]
        
    if parsed_data.get("languages"):
        current_employee.languages = parsed_data["languages"]

    if parsed_data.get("work_experience"):
        new_experiences = []
        for exp in parsed_data["work_experience"]:
            new_experiences.append(WorkExperienceInput(**exp))
        current_employee.work_experience = new_experiences

    # 7. Save the fully updated profile to MongoDB
    await current_employee.save()

    return {
        "status": "success",
        "message": "Resume uploaded to Cloudinary and profile successfully auto-filled!",
        "resume_url": resume_url,
        "extracted_data": parsed_data,
        "profile": {
            "skills": current_employee.skills,
            "experience": current_employee.experience_years,
            "education": current_employee.education_level
        }
    }

# --- Resume Download ---
@router.get("/resume/download/{employee_id}")
async def download_employee_resume(
    employee_id: str, 
    current_user = Depends(get_any_current_user)
):
    """
    Downloads an employee's resume.
    - Employees can freely download their own resume.
    - Employers use a 'download_resume' quota to download.
    Generates a PDF dynamically if the employee hasn't uploaded a static file.
    """
    
    # --- AUTHORIZATION & QUOTA CHECKS ---
    if current_user.role == "employee":
        # Employees cannot snoop on other employees' resumes
        if str(current_user.id) != employee_id:
            raise HTTPException(
                status_code=403, 
                detail="You do not have permission to download this resume."
            )
            
    elif current_user.role == "employer":
        # Check subscription quota for employers before allowing download
        await SubscriptionService.check_quota(str(current_user.id), "download_resume")
        
    else:
        raise HTTPException(status_code=403, detail="Unauthorized role.")

    # --- FETCH THE EMPLOYEE ---
    employee = await Employee.get(ObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    # --- RETURN OR GENERATE THE RESUME ---
    # Scenario A: If a static resume is uploaded (e.g., S3/Cloudinary URL exists)
    if getattr(employee, "resume_url", None):
        return RedirectResponse(url=employee.resume_url)
        
    # Generate the PDF synchronously on the fly
    pdf_content = ResumeService.generate_pdf(employee)
    
    # Sanitize the filename to prevent header injection errors
    employee_name = getattr(employee, "name", "Candidate") or "Candidate"
    safe_name = "".join([c for c in employee_name if c.isalpha() or c.isdigit() or c==' ']).rstrip()
    
    return Response(
        content=pdf_content.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}_Resume.pdf"'}
    )

# --- Status & Visibility ---
@router.patch("/availability", status_code=status.HTTP_200_OK)
async def update_availability_status(
    data: AvailabilityUpdate,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Updates the employee's availability status.
    """
    current_employee.availability_status = data.is_available
    await current_employee.save()
    status_label = "Available" if data.is_available else "Not Available" 
    
    return {
        "message": f"Your status has been updated to {status_label}.",
        "availability_status": current_employee.availability_status
    }

# --- Job Applications ---
@router.post("/jobs/apply/{job_id}", status_code=status.HTTP_201_CREATED)
async def apply_for_job(
    job_id: str,
    current_employee = Depends(get_current_employee)
):
    """
    Employee expresses interest in a specific job post and notifies the employer.
    """
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

    # Create the application
    new_app = JobApplication(
        job_id=job.id,
        employee_id=current_employee.id,
        employer_id=PydanticObjectId(job.employer_id), 
        status=ApplicationStatus.APPLIED
    )
    
    # SAVE THE APPLICATION TO THE DATABASE
    await new_app.insert()

    # We already fetched 'job' at the top, so we just increment and save!
    job.applicants_count += 1
    await job.save()

    # Send the notification to the employer. The service expects a 'user_id' parameter.
    await NotificationService.notify_user(
        user_id=str(job.employer_id),
        title="New Application Received!",
        message=f"{current_employee.name} just applied for your {getattr(job, 'job_title', 'Job')} role.", 
        notif_type=NotificationType.NEW_APPLICANT,
        related_entity_id=str(job.id)
    )

    return {"message": "Application submitted successfully!", "status": new_app.status}

# --- Applied Jobs ---
@router.get("/jobs/applied", response_model=List[AppliedJobResponse])
async def get_applied_jobs(
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Retrieves a list of all jobs the current employee (job seeker) has applied for,
    including the current status of the application and basic job details.
    """
    # Find all application documents belonging to the current employee
    applications = await JobApplication.find(
        JobApplication.employee_id == current_employee.id
    ).to_list()

    if not applications:
        return []

    applied_jobs_data = []

    # Loop through the applications to fetch the associated job details
    for app in applications:
        # Fetch the actual job document to get the title and company name
        job = await Job.get(app.job_id)
        
        if job:
            # Fetch the employer to get the company name
            employer = await Employer.get(job.employer_id)

            applied_jobs_data.append({
                "application_id": str(app.id),
                "job_id": str(job.id),
                "job_title": getattr(job, "job_title", "Unknown Title"),
                "company_name": getattr(employer, "company_name", "Unknown Company") if employer else "Unknown Company",
                "status": getattr(app, "status", "pending"),
                "applied_at": getattr(app, "applied_at", app.id.generation_time)
            })

    # Return the compiled list to the frontend
    return applied_jobs_data

# --- GET ALL SAVED JOBS ---
@router.get("/jobs/saved", response_model=List[SavedJobResponse])
async def get_saved_jobs(
    current_employee = Depends(get_current_employee) # Type hint: current_employee: Employee
):
    """
    Retrieves the full details of all jobs the employee has saved.
    """
    if not current_employee.saved_job_ids:
        return []

    saved_jobs_data = []
    
    # Loop through the saved IDs and fetch the actual job documents
    for job_id in current_employee.saved_job_ids:
        job = await Job.get(job_id)

        # Only append it if the job still exists (hasn't been deleted by the employer)
        if job:
            # Fetch employer to get company name
            employer = await Employer.get(job.employer_id)

            # Format salary into a readable string
            salary_str = "Not specified"
            if job.min_fixed_salary and job.max_fixed_salary:
                salary_str = f"₹{job.min_fixed_salary} - ₹{job.max_fixed_salary}"
            elif job.min_fixed_salary:
                salary_str = f"From ₹{job.min_fixed_salary}"

            saved_jobs_data.append({
                "job_id": str(job.id),
                "job_title": getattr(job, "job_title", "Unknown Title"),
                "company_name": getattr(employer, "company_name", "Unknown Company") if employer else "Unknown Company",
                "location": getattr(job, "job_city", "Not specified"),
                "expected_salary": salary_str,
                "created_at": getattr(job, "created_at", None) # <--- ADDED FIELD
            })
        else:
            # Optional: Clean up the database by removing IDs of jobs that no longer exist
            current_employee.saved_job_ids.remove(job_id)
            await current_employee.save()

    return saved_jobs_data

# --- SAVE A JOB ---
@router.post("/jobs/{job_id}/save")
async def save_job_for_later(
    job_id: str,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Adds a job ID to the employee's saved jobs list.
    """
    # Verify the job actually exists
    job = await Job.get(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    # Check if it's already saved to prevent duplicates
    if job_id not in current_employee.saved_job_ids:
        current_employee.saved_job_ids.append(job_id)
        await current_employee.save()
        return {"message": "Job saved successfully", "saved": True}
        
    return {"message": "Job is already saved", "saved": True}

# --- UNSAVE A JOB ---
@router.delete("/jobs/{job_id}/unsave")
async def unsave_job(
    job_id: str,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Removes a job ID from the employee's saved jobs list.
    """
    if job_id in current_employee.saved_job_ids:
        current_employee.saved_job_ids.remove(job_id)
        await current_employee.save()
        return {"message": "Job removed from saved list", "saved": False}
        
    return {"message": "Job was not in saved list", "saved": False}

# --- to view the company profile ---
@router.get("/companyprofile/{employer_id}", response_model=CompanyProfilePublicResponse, status_code=status.HTTP_200_OK)
async def get_public_company_profile(employer_id: str):
    """
    Retrieves the basic, public-facing profile of a company/employer.
    Useful for employees to view who is hiring them.
    """
    # Safely validate that the provided ID is a valid MongoDB ObjectId
    try:
        parsed_id = PydanticObjectId(employer_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid Employer ID format."
        )

    # Fetch the employer from the database
    employer = await Employer.get(parsed_id)
    
    # Handle cases where the employer doesn't exist
    if not employer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Company profile not found."
        )
        
    # Safely map and return ONLY the allowed public fields
    return CompanyProfilePublicResponse(
        employer_id=str(employer.id),
        recruiter_name=getattr(employer, "name", "Not Provided"),
        company_name=getattr(employer, "company_name", "Not Provided"),
        industry=getattr(employer, "industry", "Not Provided"),
        email=getattr(employer, "email", "Not Provided"),
        logo_url=getattr(employer, "logo_url", None)
    )

# --- Category of jobs ---

@router.get("/categories", response_model=List[str])
async def get_all_categories():
    """
    Returns a simple list of category names for the registration dropdown.
    """
    categories = await Category.find(Category.is_active == True).to_list()
    return [c.name for c in categories]

# --- Reviews & Feedback ---
@router.get("/{employee_id}/reviews", response_model=List[dict])
async def get_employee_reviews(employee_id: str):
    """
    Returns all reviews and comments for a specific employee.
    """
    try:
        reviews = await Review.find(
            Review.employee_id == ObjectId(employee_id)
        ).sort("-created_at").to_list()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid employee ID format")

    return [{
        "rating": r.rating,
        "comment": r.comment,
        "date": r.created_at
    } for r in reviews]