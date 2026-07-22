# --- IMPORTS ---
import re
import random
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status, Response
from fastapi.responses import StreamingResponse
from bson import ObjectId

# --- Core & Dependencies ---
from app.core.config import settings
from app.core.security import verify_password
from app.api.dependencies import get_current_employee, get_current_employer

# --- Schemas ---
from app.schemas.employee import (
    EmployeeProfileUpdate,
    EmployeeProfileUpdate,
    EmployeeResponse, 
    AvailabilityUpdate,
    EmployeeKYCUpdate,
    EmployeeDashboardResponse,
    WorkExperienceInput 
)

# --- Models ---
from app.models.employee import Employee, GeoLocation
from app.models.application import JobApplication, ApplicationStatus
from app.models.contact import ContactUnlock 
from app.models.job import Job 
from app.models.payment import Payment 
from app.models.review import Review 
from app.models.notification import Notification
from app.models.auth import OTP 
from app.models.category import Category

# --- Services ---
from app.services.email import EmailService
from app.services.webhooks import WebhookService 
from app.services.resumes import ResumeService
from app.services.subscriptions import SubscriptionService
from app.services.kyc import KYCService
from app.services.recommendation import RecommendationService
from app.services.parser import ResumeParserService

# --- Utilities ---
from app.utils.storage import StorageService
from app.utils.maps import MapService

router = APIRouter()

# =====================================================================
# PYDANTIC SCHEMAS FOR PROFILE COMPLETION & UPDATES
# =====================================================================

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


# =====================================================================
# EMPLOYEE ACTIONS
# =====================================================================

# --- Profile Completion ---

@router.patch("/profile/complete", response_model=dict, status_code=status.HTTP_200_OK)
async def complete_employee_profile(
    data: CompleteEmployeeProfileRequest,
    current_worker: Employee = Depends(get_current_employee)
):
    """
    Completes the employee's profile after they have verified their phone number.
    Requires the access_token received from the Auth endpoint.
    """
    # =================================================================
    # 1. OLA MAPS MAGIC: Auto-Geocode the typed location
    # =================================================================
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

    # =================================================================
    # 2. UPDATE DATABASE RECORD (Safe Unpacking & Conversion)
    # =================================================================
    
    # --- Location Data ---
    if getattr(data, "location", None):
        geo_location = GeoLocation(
            type="Point",
            coordinates=[data.location.longitude, data.location.latitude]
        )
        current_worker.current_location = geo_location
        # The ultimate model stores 'location' as an object, but if you have a string representation:
        # current_worker.location = f"{data.location.latitude}, {data.location.longitude}"
    
    if data.location_name:
        current_worker.location_name = data.location_name

    if getattr(data, "preferred_locations", None):
        current_worker.preferred_locations = data.preferred_locations

    # --- Basic & Demographic Details ---
    if getattr(data, "email", None):
        current_worker.email = data.email
    if getattr(data, "current_salary", None):
        current_worker.current_salary = data.current_salary
    if getattr(data, "expected_salary", None):
        current_worker.expected_salary = data.expected_salary
    if getattr(data, "age", None) is not None:
        current_worker.age = data.age
    if getattr(data, "gender", None):
        current_worker.gender = data.gender
    if getattr(data, "languages", None):
        current_worker.languages = data.languages
    if getattr(data, "education_level", None):
        # Using the Education sub-model from the Ultimate Schema
        from app.models.employee import Education
        current_worker.education = [Education(institution="Not Specified", degree=data.education_level)]

    # --- Category / Header Details ---
    if getattr(data, "category", None):
        current_worker.trade_category = data.category
        current_worker.category = data.category
    if getattr(data, "preferred_roles", None):
        current_worker.preferred_roles = data.preferred_roles
    if getattr(data, "experience", None) is not None:
        current_worker.experience = data.experience
        current_worker.experience_years = data.experience

    # --- Skills Array (Model Conversion) ---
    if getattr(data, "skills", None):
        current_worker.skills = [
            Skill(name=s.name, level=s.level, years=s.years) 
            for s in data.skills
        ]

    # --- Work Experience Array (Model Conversion) ---
    if getattr(data, "work_experience", None):
        current_worker.work_experience = [
            WorkExperience(
                company=exp.company,
                title=exp.title,
                start_date=exp.start_date,
                end_date=exp.end_date,
                description=exp.description
            ) for exp in data.work_experience
        ]
        
    if getattr(data, "referred_by_id", None):
        current_worker.referred_by_id = data.referred_by_id

    # 3. Save to MongoDB
    await current_worker.save()

    # =================================================================
    # 4. TRIGGER WEBHOOK & RETURN
    # =================================================================
    await WebhookService.trigger_event("worker_registered", {
        "worker_id": str(current_worker.id),
        "name": getattr(current_worker, "name", "User"),
        "category": getattr(current_worker, "category", "Unspecified"),
        "location": getattr(current_worker, "location_name", "Unspecified")
    })
    
    return {
        "status": "success",
        "message": "Profile completed successfully!",
        "profile_summary": {
            "name": getattr(current_worker, "name", "User"),
            "roles": getattr(current_worker, "preferred_roles", []),
            "skills_count": len(getattr(current_worker, "skills", []) or []),
            "experience_entries": len(getattr(current_worker, "work_experience", []) or [])
        },
        "employee": EmployeeResponse(
            id=str(current_worker.id),
            phone=current_worker.phone,
            name=getattr(current_worker, "name", "User"),
            category=getattr(current_worker, "category", None),
            availability_status=getattr(current_worker, "availability_status", True),
            rating=getattr(current_worker, "rating", 0.0),
            created_at=current_worker.created_at
        )
    }

# --- Worker Dashboard & Stats ---

@router.get("/dashboard", response_model=EmployeeDashboardResponse)
async def get_employee_dashboard(
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Returns real-time stats of how many employers have 'unlocked' this worker.
    Safely handles incomplete profiles by providing fallback strings.
    """
    unlock_count = await ContactUnlock.find(
        ContactUnlock.worker_id == current_employee.id
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

# --- Profile Management ---

@router.get("/me")
async def read_employee_me(current_employee: Employee = Depends(get_current_employee)):
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

# --- Discovery & Recommendations ---

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

# --- Smart Recommendations ---

@router.get("/recommendations")
async def get_smart_job_recommendations(
    radius_km: int = 15,
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Returns an AI-ranked list of jobs based on Category, Distance, and Experience.
    """
    if not current_employee.current_location or not current_employee.current_location.coordinates:
        return {"message": "Please update your location to see nearby job recommendations.", "jobs": []}

    ranked_jobs_data = await RecommendationService.get_best_jobs_for_worker(
        worker=current_employee, 
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

# --- Nearby Jobs & Geospatial Queries ---

@router.get("/nearby-jobs")
async def get_nearby_jobs(
    radius_km: float = 5.0,
    current_worker: Employee = Depends(get_current_employee)
):
    """
    Uses MongoDB Geo-Spatial queries to find jobs within a specific radius.
    """
    lon, lat = current_worker.current_location.coordinates
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
        "category": current_worker.category 
    }

    nearby_jobs = await Job.find(query).to_list()

    return [{
        "id": str(j.id),
        "title": j.title,
        "location_name": j.location_name,
        "salary": j.salary_range,
        "created_at": j.created_at
    } for j in nearby_jobs]

# --- Reviews & Feedback ---

@router.get("/{worker_id}/reviews", response_model=List[dict])
async def get_worker_reviews(worker_id: str):
    """
    Returns all reviews and comments for a specific worker.
    """
    try:
        reviews = await Review.find(
            Review.worker_id == ObjectId(worker_id)
        ).sort("-created_at").to_list()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid worker ID format")

    return [{
        "rating": r.rating,
        "comment": r.comment,
        "date": r.created_at
    } for r in reviews]

# --- Geocoding & Reverse Geocoding ---

@router.get("/reverse-geocode")
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

# --- Job Applications ---

@router.post("/apply/{job_id}", status_code=status.HTTP_201_CREATED)
async def apply_for_job(
    job_id: str,
    current_worker: Employee = Depends(get_current_employee)
):
    """
    Worker expresses interest in a specific job post and notifies the employer.
    """
    try:
        job = await Job.get(ObjectId(job_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Job ID format")

    if not job or job.status != "published":
        raise HTTPException(status_code=404, detail="Job not found or inactive.")

    existing_application = await JobApplication.find_one({
        "employee_id": current_worker.id,
        "job_id": job.id
    })
    if existing_application:
        raise HTTPException(status_code=400, detail="You have already applied for this job.")

    new_app = JobApplication(
        job_id=job.id,
        employee_id=current_worker.id,
        employer_id=job.employer_id, 
        status=ApplicationStatus.APPLIED
    )
    await new_app.insert() 

    new_notif = Notification(
        user_id=job.employer_id,
        title="New Applicant!",
        message=f"{current_worker.name} has applied for your '{job.title}' job."
    )
    await new_notif.insert()

    return {"message": "Application submitted successfully!", "status": new_app.status}

# --- Profile Updates ---

@router.patch("/phone_no_update", status_code=status.HTTP_200_OK)
async def update_employee_phone(
    data: UpdatePhoneRequest,
    current_worker: Employee = Depends(get_current_employee)
):
    """
    Securely updates the employee's phone number after verifying an OTP sent to the NEW number.
    """
    clean_new_phone = data.new_phone[-10:]

    if current_worker.phone == clean_new_phone:
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

    current_worker.phone = clean_new_phone
    await current_worker.save()

    return {
        "status": "success", 
        "message": "Phone number successfully updated.", 
        "new_phone": current_worker.phone
    }

# --- Resume Upload & Parsing ---

@router.post("/me/upload-resume", status_code=status.HTTP_200_OK)
async def upload_and_parse_resume(
    resume_file: UploadFile = File(...),
    current_worker: Employee = Depends(get_current_employee)
):
    """
    1. Uploads the PDF to cloud storage.
    2. Extracts text from the PDF.
    3. Uses AI to parse the text into structured data.
    4. Auto-fills the worker's profile in the database.
    """
    # 1. Validate File Type
    if not resume_file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF resumes are supported right now.")

    file_bytes = await resume_file.read()

    # 2. Upload the actual file to your cloud storage (AWS S3, Cloudinary, etc.)
    # resume_url = await StorageService.upload_document(file_bytes, folder="resumes")
    # current_worker.resume_url = resume_url

    # 3. Extract Text from the PDF
    try:
        raw_text = await ResumeParserService.extract_text_from_pdf(file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # 4. Ask the AI to parse the text into a structured dictionary
    parsed_data = await ResumeParserService.parse_resume_to_json(raw_text)

    # 5. Auto-Fill the Database Profile!
    # We use 'getattr' or '.get()' to safely handle missing data if the AI couldn't find it.
    
    if parsed_data.get("skills"):
        # Merge new skills with existing ones, avoiding duplicates
        existing_skills = set(current_worker.skills or [])
        existing_skills.update(parsed_data["skills"])
        current_worker.skills = list(existing_skills)
        
    if parsed_data.get("education_level"):
        current_worker.education_level = parsed_data["education_level"]
        
    if parsed_data.get("experience_years"):
        current_worker.experience_years = parsed_data["experience_years"]
        current_worker.experience = parsed_data["experience_years"]
        
    if parsed_data.get("languages"):
        current_worker.languages = parsed_data["languages"]

    if parsed_data.get("work_experience"):
        # Convert the AI's raw dictionaries into your Pydantic WorkExperience models
        new_experiences = []
        for exp in parsed_data["work_experience"]:
            new_experiences.append(WorkExperienceInput(**exp))
        current_worker.work_experience = new_experiences

    # 6. Save the fully updated profile to MongoDB
    await current_worker.save()

    return {
        "status": "success",
        "message": "Resume uploaded and profile successfully auto-filled!",
        "extracted_data": parsed_data,
        "profile": {
            "skills": current_worker.skills,
            "experience": current_worker.experience_years,
            "education": current_worker.education_level
        }
    }

# --- KYC Verification & Document Upload ---

@router.post("/me/verify-kyc")
async def verify_worker_kyc(
    id_type: str = Form(..., description="Must be 'AADHAAR' or 'PAN'"),
    document_image: UploadFile = File(..., description="Photo of the ID card"),
    current_worker: Employee = Depends(get_current_employee)
):
    """
    Hybrid KYC Verification: Attempts OCR first. If it fails 3 times, routes to manual Admin review.
    """
    if current_worker.kyc_status == "VERIFIED" or getattr(current_worker, "is_approved", False):
        return {"status": "success", "message": "Your account is already verified!"}
        
    if current_worker.kyc_status == "PENDING_REVIEW":
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
        current_worker.kyc_attempts += 1
        
        if current_worker.kyc_attempts >= 3:
            current_worker.kyc_status = "PENDING_REVIEW"
            await current_worker.save()
            return {
                "status": "manual_review",
                "message": "Automated verification failed. We have sent your document to our team for a manual review within 24 hours."
            }
        else:
            await current_worker.save()
            attempts_left = 3 - current_worker.kyc_attempts
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail=f"Verification failed. Please ensure the image is clear and well-lit. You have {attempts_left} automated attempts remaining."
            )

    if id_type == "AADHAAR":
        current_worker.adhar_card_number = verification_result["extracted_number"]
    else:
        current_worker.pan_card = verification_result["extracted_number"]
        
    current_worker.kyc_status = "VERIFIED"
    current_worker.is_approved = True
    
    await current_worker.save()

    return {
        "status": "success",
        "message": "Identity successfully verified! Your account is now active.",
        "verified_name": verification_result["extracted_name"]
    }

# - -- Profile Photo Upload ---

@router.post("/upload-photo", status_code=status.HTTP_200_OK)
async def update_profile_photo(
    file: UploadFile = File(...), 
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Uploads a profile picture and updates the worker's record.
    """
    if not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="File must be an image"
        )

    url = await StorageService.upload_image(file, folder="workers")
    
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

# --- Status & Visibility ---

@router.patch("/availability", status_code=status.HTTP_200_OK)
async def update_availability_status(
    data: AvailabilityUpdate,
    current_worker: Employee = Depends(get_current_employee)
):
    """
    Updates the worker's availability status.
    """
    current_worker.availability_status = data.is_available
    await current_worker.save()
    status_label = "Available" if data.is_available else "Not Available" 
    
    return {
        "message": f"Your status has been updated to {status_label}.",
        "availability_status": current_worker.availability_status
    }


# =====================================================================
# EMPLOYER ACTIONS
# =====================================================================

@router.get("/search")
async def search_employees(
    category: str,
    lat: float,
    lon: float,
    radius_km: int = 10 
):
    """
    Search for employees by category and GPS location.
    """
    query = {
        "category": category,
        "availability_status": True,  
        "is_approved": True,          
        "current_location": {         
            "$near": {
                "$geometry": {"type": "Point", "coordinates": [lon, lat]},
                "$maxDistance": radius_km * 1000
            }
        }
    }
    
    employees = await Employee.find(query).to_list()
    return employees

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

# --- Category of jobs ---

@router.get("/categories", response_model=List[str])
async def get_all_categories():
    """
    Returns a simple list of category names for the registration dropdown.
    """
    categories = await Category.find(Category.is_active == True).to_list()
    return [c.name for c in categories]