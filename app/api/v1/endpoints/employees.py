# --- IMPORTS ---
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import List, Optional
from datetime import datetime
from bson import ObjectId
import random
import re

# --- Core Imports ---
from app.core.config import settings
from app.core.security import verify_password

# --- Dependencies Imports ---
from app.api.dependencies import get_current_employee, get_current_employer

# --- Schema Imports ---
from app.models.employer import Employer
from app.schemas.employee import (
    EmployeeProfileUpdate,
    EmployeeProfileUpdate,
    EmployeeResponse, 
    AvailabilityUpdate,
    EmployeeKYCUpdate,
    EmployeeDashboardResponse,
    WorkExperienceInput,
    AppliedJobResponse,
    SavedJobResponse
)

# --- Models Imports ---
from app.models.employee import Employee, GeoLocation
from app.models.application import JobApplication, ApplicationStatus
from app.models.contact import ContactUnlock 
from app.models.job import Job 
from app.models.payment import Payment 
from app.models.review import Review 
from app.models.notification import Notification
from app.models.auth import OTP 
from app.models.category import Category

# --- Services Imports ---
from app.services.email import EmailService
from app.services.webhooks import WebhookService 
from app.services.resumes import ResumeService
from app.services.subscriptions import SubscriptionService
from app.services.kyc import KYCService
from app.services.recommendation import RecommendationService
from app.services.parser import ResumeParserService

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
        current_employee.current_location = geo_location
        # The ultimate model stores 'location' as an object, but if you have a string representation:
        # current_employee.location = f"{data.location.latitude}, {data.location.longitude}"
    
    if data.location_name:
        current_employee.location_name = data.location_name

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
        current_employee.education = [Education(institution="Not Specified", degree=data.education_level)]

    # Category / Header Details
    if getattr(data, "category", None):
        current_employee.trade_category = data.category
        current_employee.category = data.category
    if getattr(data, "preferred_roles", None):
        current_employee.preferred_roles = data.preferred_roles
    if getattr(data, "experience", None) is not None:
        current_employee.experience = data.experience
        current_employee.experience_years = data.experience

    # Skills Array (Model Conversion)
    if getattr(data, "skills", None):
        current_employee.skills = [
            Skill(name=s.name, level=s.level, years=s.years) 
            for s in data.skills
        ]

    # Work Experience Array (Model Conversion)
    if getattr(data, "work_experience", None):
        current_employee.work_experience = [
            WorkExperience(
                company=exp.company,
                title=exp.title,
                start_date=exp.start_date,
                end_date=exp.end_date,
                description=exp.description
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

    return EmployeeDashboardResponse(
        name=current_employee.name or "User",
        category=getattr(current_employee, "category", getattr(current_employee, "trade_category", "Profile Incomplete")),
        is_available=getattr(current_employee, "availability_status", False),
        total_unlocks=unlock_count,
        location=current_employee.location.get("name", "Location pending") if current_employee.location else "Location pending",
        daily_rate=float(current_employee.expected_salary) if getattr(current_employee, "expected_salary", None) and current_employee.expected_salary.isdigit() else None,
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
    Uploads a profile picture and updates the employee's record.
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="File must be an image"
        )

    url = await StorageService.upload_image(file, folder="employees")
    
    if not url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Cloud upload failed"
        )
    
    current_employee.photo_url = url
    await current_employee.save()
    
    return {
        "message": "Profile photo updated successfully",
        "photo_url": url
    }

# --- Profile Update ---
@router.put("/profile_update", response_model=dict, status_code=status.HTTP_200_OK)
async def update_employee_profile(
    profile_data: EmployeeProfileUpdate,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Updates the candidate's personal and professional details in a single request.
    """
    update_dict = profile_data.model_dump(exclude_unset=True)
    
    # Check if the email is being changed to reset verification status
    if "email" in update_dict and update_dict["email"] != getattr(current_employee, "email", None):
        current_employee.email_verified = False 
        
    # Dynamically apply all provided fields to the database model
    for field, value in update_dict.items():
        setattr(current_employee, field, value)
        
    await current_employee.save()
    
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
    if not otp_record or not otp_record.hashed_code or not verify_password(data.otp_code, otp_record.hashed_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid or expired OTP for the new phone number."
        )

    otp_record.hashed_code = None
    await otp_record.save()

    current_employee.phone = clean_new_phone
    await current_employee.save()

    return {
        "status": "success", 
        "message": "Phone number successfully updated.", 
        "new_phone": current_employee.phone
    }

# --- KYC Verification & Document Upload ---
@router.post("/profile/verify_kyc")
async def verify_employee_kyc(
    id_type: str = Form(..., description="Must be 'AADHAAR' or 'PAN'"),
    document_image: UploadFile = File(..., description="Photo of the ID card"),
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Hybrid KYC Verification: Attempts OCR first. If it fails 3 times, routes to manual Admin review.
    """
    if current_employee.kyc_status == "VERIFIED" or getattr(current_employee, "is_approved", False):
        return {"status": "success", "message": "Your account is already verified!"}
        
    if current_employee.kyc_status == "PENDING_REVIEW":
        return {
            "status": "pending", 
            "message": "Your document is currently in the queue for manual review. Please check back later."
        }

    if document_image.content_type not in ["image/jpeg", "image/png", "image/jpg"]:
        raise HTTPException(status_code=400, detail="Only JPG and PNG images are supported.")

    file_bytes = await document_image.read()
    
    try:
        verification_result = await KYCService.verify_id_document(file_bytes, id_type)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, 
            detail="KYC Provider is currently unavailable. Please try again later."
        )

    if verification_result["status"] != "VERIFIED":
        current_employee.kyc_attempts += 1
        
        if current_employee.kyc_attempts >= 3:
            current_employee.kyc_status = "PENDING_REVIEW"
            await current_employee.save()
            return {
                "status": "manual_review",
                "message": "Automated verification failed. We have sent your document to our team for a manual review within 24 hours."
            }
        else:
            await current_employee.save()
            attempts_left = 3 - current_employee.kyc_attempts
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail=f"Verification failed. Please ensure the image is clear and well-lit. You have {attempts_left} automated attempts remaining."
            )

    if id_type == "AADHAAR":
        current_employee.adhar_card_number = verification_result["extracted_number"]
    else:
        current_employee.pan_card = verification_result["extracted_number"]
        
    current_employee.kyc_status = "VERIFIED"
    current_employee.is_approved = True
    
    await current_employee.save()

    return {
        "status": "success",
        "message": "Identity successfully verified! Your account is now active.",
        "verified_name": verification_result["extracted_name"]
    }

# --- Resume Upload & Parsing ---
@router.post("/profile/upload_resume", status_code=status.HTTP_200_OK)
async def upload_and_parse_resume(
    resume_file: UploadFile = File(...),
    current_employee: Employee = Depends(get_current_employee)
):
    """
    1. Uploads the PDF to cloud storage.
    2. Extracts text from the PDF.
    3. Uses AI to parse the text into structured data.
    4. Auto-fills the employee's profile in the database.
    """
    # Validate File Type
    if not resume_file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF resumes are supported right now.")

    file_bytes = await resume_file.read()

    # Upload the actual file to your cloud storage (AWS S3, Cloudinary, etc.)
    # resume_url = await StorageService.upload_document(file_bytes, folder="resumes")
    # current_employee.resume_url = resume_url

    # Extract Text from the PDF
    try:
        raw_text = await ResumeParserService.extract_text_from_pdf(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Ask the AI to parse the text into a structured dictionary
    parsed_data = await ResumeParserService.parse_resume_to_json(raw_text)

    # Auto-Fill the Database Profile!
    # We use 'getattr' or '.get()' to safely handle missing data if the AI couldn't find it.
    
    if parsed_data.get("skills"):
        # Merge new skills with existing ones, avoiding duplicates
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
        # Convert the AI's raw dictionaries into your Pydantic WorkExperience models
        new_experiences = []
        for exp in parsed_data["work_experience"]:
            new_experiences.append(WorkExperienceInput(**exp))
        current_employee.work_experience = new_experiences

    # Save the fully updated profile to MongoDB
    await current_employee.save()

    return {
        "status": "success",
        "message": "Resume uploaded and profile successfully auto-filled!",
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
    current_employer = Depends(get_current_employer)
):
    """
    Downloads the employee's resume. 
    """
    await SubscriptionService.check_quota(str(current_employer.id), "download_resume")
    
    employee = await Employee.get(ObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    if employee.resume_url:
        return {"redirect_url": employee.resume_url}
        
    pdf_content = await ResumeService.generate_pdf(employee)
    
    return Response(
        content=pdf_content.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={employee.name}_Resume.pdf"}
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

# --- Job Discovery ---
@router.get("/jobs", status_code=status.HTTP_200_OK)
async def get_job_feed():
    """
    The main feed for employees. 
    Only returns jobs that are active.
    """
    active_jobs = await Job.find(Job.status == "published").to_list()
    
    if not active_jobs:
        return {"message": "No active jobs found right now. Check back later!"}
        
    return active_jobs

# --- Job Applications ---
@router.post("/jobs/apply/{job_id}", status_code=status.HTTP_201_CREATED)
async def apply_for_job(
    job_id: str,
    current_employee: Employee = Depends(get_current_employee)
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

    new_app = JobApplication(
        job_id=job.id,
        employee_id=current_employee.id,
        employer_id=job.employer_id, 
        status=ApplicationStatus.APPLIED
    )
    await new_app.insert() 

    new_notif = Notification(
        user_id=job.employer_id,
        title="New Applicant!",
        message=f"{current_employee.name} has applied for your '{job.title}' job."
    )
    await new_notif.insert()

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
        {"employee_id": str(current_employee.id)}
    ).to_list()

    if not applications:
        return []

    applied_jobs_data = []
    
    # Loop through the applications to fetch the associated job details
    for app in applications:
        # Fetch the actual job document to get the title and company name
        job = await Job.get(app.job_id)
        
        if job:
            applied_jobs_data.append({
                "application_id": str(app.id),
                "job_id": str(job.id),
                "job_title": getattr(job, "title", "Unknown Title"),
                "company_name": getattr(job, "company_name", "Unknown Company"),
                "status": getattr(app, "status", "pending"), 
                "applied_at": getattr(app, "created_at", app.id.generation_time)
            })

    # Return the compiled list to the frontend
    return applied_jobs_data

# --- GET ALL SAVED JOBS ---
@router.get("/jobs/saved", response_model=List[SavedJobResponse])
async def get_saved_jobs(
    current_employee: Employee = Depends(get_current_employee)
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
            saved_jobs_data.append({
                "job_id": str(job.id),
                "job_title": getattr(job, "title", "Unknown Title"),
                "company_name": getattr(job, "company_name", "Unknown Company"),
                "location": getattr(job, "location", "Not specified"),
                "expected_salary": getattr(job, "salary", None) # Adjust field name to match your Job model
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

# --- Smart Recommendations ---
@router.get("/jobs/recommendations")
async def get_smart_job_recommendations(
    radius_km: int = 15,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Returns an AI-ranked list of jobs based on Category, Distance, and Experience.
    """
    if not current_employee.current_location or not current_employee.current_location.coordinates:
        return {"message": "Please update your location to see nearby job recommendations.", "jobs": []}

    ranked_jobs_data = await RecommendationService.get_best_jobs_for_employee(
        employee=current_employee, 
        max_distance_km=radius_km
    )

    if not ranked_jobs_data:
        return {"message": "No perfect matches found nearby. Try expanding your search radius!", "jobs": []}

    formatted_jobs = []
    for job in ranked_jobs_data:
        formatted_jobs.append({
            "job_id": str(job["_id"]),
            "title": job.get("title", "Job Posting"),
            "category": job.get("category"),
            "location_name": job.get("location_name"),
            "distance_km": round(job.get("distance_km", 0), 1),
            "salary": job.get("salary_range", job.get("salary", "Not specified")),
            "match_score": job.get("match_score")
        })

    return {
        "status": "success",
        "total_matches": len(formatted_jobs),
        "jobs": formatted_jobs
    }

# --- Category of jobs ---

@router.get("/categories", response_model=List[str])
async def get_all_categories():
    """
    Returns a simple list of category names for the registration dropdown.
    """
    categories = await Category.find(Category.is_active == True).to_list()
    return [c.name for c in categories]

# --- Nearby Jobs & Geospatial Queries ---
@router.get("/jobs/nearby_jobs")
async def get_nearby_jobs(
    radius_km: float = 5.0,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Uses MongoDB Geo-Spatial queries to find jobs within a specific radius.
    """
    lon, lat = current_employee.current_location.coordinates
    radius_meters = radius_km * 1000

    query = {
        "current_location": {
            "$near": {
                "$geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat]
                },
                "$maxDistance": radius_meters
            }
        },
        "is_active": True,
        "category": current_employee.category 
    }

    nearby_jobs = await Job.find(query).to_list()

    return [{
        "id": str(j.id),
        "title": j.title,
        "location_name": j.location_name,
        "salary": j.salary_range,
        "created_at": j.created_at
    } for j in nearby_jobs]

# --- Geocoding & Reverse Geocoding ---
@router.get("/reverse_geocode")
async def get_address_from_gps(lat: float, lng: float):
    """
    Takes GPS coordinates from the user's phone and returns a readable address.
    """
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        raise HTTPException(status_code=400, detail="Invalid GPS coordinates provided.")

    location_data = await MapService.reverse_geocode(lat, lng)
    
    if not location_data:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, 
            detail="Could not resolve these coordinates to an address right now."
        )

    return {
        "status": "success",
        "formatted_address": location_data["formatted_address"],
        "city": location_data["city"]
    }

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