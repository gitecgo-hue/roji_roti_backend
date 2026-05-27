import re
import random
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Response
from fastapi.responses import StreamingResponse
from bson import ObjectId
from jose import jwt

# --- Core & Dependencies ---
from app.core.config import settings
from app.core.security import create_access_token, verify_password
from app.api.dependencies import get_current_employee, get_current_employer

# --- Schemas ---
from app.schemas.employee import (
    EmployeeResponse, 
    AvailabilityUpdate,
    EmployeeKYCUpdate,
    EmployeeDashboardResponse 
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

# --- Services ---
from app.services.webhooks import WebhookService 
from app.utils.storage import StorageService
from app.services.resumes import ResumeService
from app.services.subscriptions import SubscriptionService

router = APIRouter()

# =====================================================================
# PYDANTIC SCHEMAS FOR REGISTRATION & PROFILE UPDATES
# =====================================================================

class LocationInput(BaseModel):
    latitude: float
    longitude: float

class EmployeeRegistrationRequest(BaseModel):
    registration_token: str = Field(..., description="Token received from /verify-signup-otp")
    name: str
    category: str
    location: LocationInput
    location_name: str
    preferred_locations: List[str] = []
    experience: int = 0
    languages: List[str] = []
    expected_salary: Optional[str] = None
    gender: Optional[str] = None
    email: Optional[EmailStr] = None
    referred_by_id: Optional[str] = None

class UpdatePhoneRequest(BaseModel):
    new_phone: str = Field(..., description="The new 10-digit mobile number")
    otp_code: str = Field(..., description="The 6-digit OTP sent to the NEW number")


# =====================================================================
# EMPLOYEE ACTIONS
# =====================================================================

# --- Registration & Auth ---

@router.post("/register", response_model=dict, status_code=status.HTTP_201_CREATED)
async def register_employee(data: EmployeeRegistrationRequest):
    """
    Register a new blue-collar worker profile securely using a verified OTP token.
    """
    try:
        payload = jwt.decode(
            data.registration_token, 
            settings.SECRET_KEY, 
            algorithms=[settings.ALGORITHM]
        )
        if payload.get("user_type") != "registration_token":
            raise ValueError("Invalid token type")
            
        verified_phone = payload.get("sub")
    except Exception:
        raise HTTPException(
            status_code=401, 
            detail="Invalid or expired registration session. Please verify your OTP again."
        )

    existing_employee = await Employee.find_one(Employee.phone == verified_phone)
    if existing_employee:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this phone number already exists."
        )
    
    geo_location = GeoLocation(
        type="Point",
        coordinates=[data.location.longitude, data.location.latitude]
    )
    
    new_employee = Employee(
        phone=verified_phone,
        full_name=data.name,              
        trade_category=data.category,     
        location=f"{data.location.latitude}, {data.location.longitude}",        
        experience_years=data.experience, 
        name=data.name,
        category=data.category,
        location_name=data.location_name,
        current_location=geo_location,
        preferred_locations=data.preferred_locations,
        experience=data.experience,
        languages=data.languages,
        expected_salary=data.expected_salary,
        gender=data.gender,
        email=data.email,
        referred_by_id=data.referred_by_id
    )
    
    await new_employee.insert()

    await WebhookService.trigger_event("worker_registered", {
        "worker_id": str(new_employee.id),
        "name": new_employee.name,
        "category": new_employee.category,
        "location": new_employee.location_name
    })
    
    access_token = create_access_token(
        subject=str(new_employee.id), 
        user_type="employee"
    )
    
    return {
        "status": "success",
        "message": "Registration successful",
        "access_token": access_token,
        "token_type": "bearer",
        "employee": EmployeeResponse(
            id=str(new_employee.id),
            phone=new_employee.phone,
            name=new_employee.name,
            category=new_employee.category,
            availability_status=new_employee.availability_status,
            rating=new_employee.rating,
            created_at=new_employee.created_at
        )
    }

# --- Worker Dashboard & Stats ---

@router.get("/dashboard", response_model=EmployeeDashboardResponse)
async def get_employee_dashboard(
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Returns real-time stats of how many employers have 'unlocked' this worker.
    """
    unlock_count = await ContactUnlock.find(
        ContactUnlock.worker_id == current_employee.id
    ).count()

    return EmployeeDashboardResponse(
        name=current_employee.name,
        category=current_employee.category,
        is_available=current_employee.availability_status,
        total_unlocks=unlock_count,
        location=current_employee.location_name,
        daily_rate=float(current_employee.expected_salary) if current_employee.expected_salary and current_employee.expected_salary.isdigit() else None,
        rating=getattr(current_employee, "rating", 0.0)
    )

# --- Discovery & Recommendations ---

@router.get("/jobs", status_code=status.HTTP_200_OK)
async def get_job_feed():
    """
    The main feed for employees. 
    Only returns jobs that are active.
    """
    active_jobs = await Job.find({"is_active": True}).to_list()
    
    if not active_jobs:
        return {"message": "No active jobs found right now. Check back later!"}
        
    return active_jobs

@router.get("/recommendations")
async def get_job_recommendations(
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Finds the 10 most recent jobs that match the worker's category and city (case-insensitive).
    """
    # 1. Safely extract the worker's category
    worker_category = getattr(current_employee, "category", None) or getattr(current_employee, "trade_category", None)
    
    if not worker_category:
        return []

    # 2. Build a CASE-INSENSITIVE query for category
    query = {
        "category": re.compile(f"^{worker_category}$", re.IGNORECASE),
        "is_active": True
    }
    
    # 3. Bulletproof Location Match (Checks all possible field names)
    location_name = getattr(current_employee, "location_name", None)
    if location_name:
        query["$or"] = [
            {"location": {"$regex": location_name, "$options": "i"}},
            {"locations": {"$regex": location_name, "$options": "i"}},
            {"location_name": {"$regex": location_name, "$options": "i"}}
        ]

    # 4. Execute the search with sort and limit
    jobs = await Job.find(query).sort("-created_at").limit(10).to_list()

    # 5. Format the output safely
    return [{
        "job_id": str(j.id),
        "title": getattr(j, "title", "Job Posting"),
        "salary": getattr(j, "salary_range", getattr(j, "salary", "Not specified")), 
        "posted_at": getattr(j, "created_at", None)
    } for j in jobs]

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

    if not job or not job.is_active:
        raise HTTPException(status_code=404, detail="Job not found or inactive.")

    # Prevent Duplicate Applications
    existing_application = await JobApplication.find_one({
        "employee_id": current_worker.id,
        "job_id": job.id
    })
    if existing_application:
        raise HTTPException(status_code=400, detail="You have already applied for this job.")

    # Create Application using the new standardized JobApplication model
    new_app = JobApplication(
        job_id=job.id,
        employee_id=current_worker.id,
        employer_id=job.employer_id, 
        status=ApplicationStatus.APPLIED
    )

    await new_app.insert() 

    # Trigger Internal Notification for Employer
    new_notif = Notification(
        user_id=job.employer_id,
        title="New Applicant!",
        message=f"{current_worker.name} has applied for your '{job.title}' job."
    )
    await new_notif.insert()

    return {"message": "Application submitted successfully!", "status": new_app.status}

# --- Profile Management ---

@router.get("/me", response_model=EmployeeResponse)
async def read_employee_me(current_employee: Employee = Depends(get_current_employee)):
    """
    Get current logged-in employee profile.
    """
    return current_employee

@router.patch("/me/phone", status_code=status.HTTP_200_OK)
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

    # Cross-platform checking to prevent duplication conflicts
    phone_taken = await Employer.find_one({"phone": clean_new_phone}) or \
                  await Employee.find_one({"phone": clean_new_phone})
    
    if phone_taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="This phone number is already registered to another account."
        )

    # Verify OTP against tracker
    otp_record = await OTP.find_one({"phone": clean_new_phone})
    if not otp_record or not otp_record.hashed_code or not verify_password(data.otp_code, otp_record.hashed_code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid or expired OTP for the new phone number."
        )

    # Consume token but retain rate tracking records
    otp_record.hashed_code = None
    await otp_record.save()

    current_worker.phone = clean_new_phone
    await current_worker.save()

    return {
        "status": "success", 
        "message": "Phone number successfully updated.", 
        "new_phone": current_worker.phone
    }

@router.patch("/me/kyc", status_code=status.HTTP_200_OK)
async def update_worker_kyc(
    kyc_data: EmployeeKYCUpdate,
    current_worker: Employee = Depends(get_current_employee)
):
    """
    Allows a worker to update their KYC details securely.
    """
    updates_made = False

    if kyc_data.aadhar_number:
        existing = await Employee.find_one(Employee.aadhar_number == kyc_data.aadhar_number)
        if existing and str(existing.id) != str(current_worker.id):
             raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This ID is already linked to another account."
            )
        current_worker.aadhar_number = kyc_data.aadhar_number
        updates_made = True

    if kyc_data.pan_number:
        current_worker.pan_number = kyc_data.pan_number.upper()
        updates_made = True

    if not updates_made:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="No valid KYC data provided for update."
        )

    await current_worker.save()

    return {
        "message": "Profile KYC details updated successfully.",
        "kyc_status": {
            "aadhar_verified": bool(current_worker.aadhar_number),
            "pan_verified": bool(current_worker.pan_number)
        }
    }

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