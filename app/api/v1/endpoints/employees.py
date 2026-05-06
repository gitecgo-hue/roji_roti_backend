import re
import random
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status, Response
from fastapi.responses import StreamingResponse
from bson import ObjectId

# --- Schemas ---
from app.schemas.employee import (
    EmployeeCreate, 
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

# --- Core & Dependencies ---
from app.core.security import create_access_token, get_password_hash
from app.api.dependencies import get_current_employee, get_current_employer

# --- Services ---
from app.services.webhooks import WebhookService 
from app.utils.storage import StorageService
from app.services.resumes import ResumeService
from app.services.subscriptions import SubscriptionService

router = APIRouter()

# =====================================================================
# EMPLOYEE ACTIONS
# =====================================================================

# --- Registration & Auth ---

@router.post("/register", response_model=dict, status_code=status.HTTP_201_CREATED)
async def register_employee(employee_in: EmployeeCreate):
    """
    Register a new blue-collar worker profile.
    """
    existing_employee = await Employee.find_one(Employee.phone == employee_in.phone)
    if existing_employee:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An employee with this phone number is already registered."
        )
    
    geo_location = GeoLocation(
        type="Point",
        coordinates=[employee_in.location.longitude, employee_in.location.latitude]
    )
    
    new_employee = Employee(
        full_name=employee_in.name,              
        trade_category=employee_in.category,     
        location=f"{employee_in.location.latitude}, {employee_in.location.longitude}",        
        experience_years=0, # Fixed formatting bug here
        phone=employee_in.phone,
        name=employee_in.name,
        category=employee_in.category,
        location_name=employee_in.location_name,
        current_location=geo_location,
        preferred_locations=employee_in.preferred_locations,
        experience=employee_in.experience,
        languages=employee_in.languages,
        expected_salary=employee_in.expected_salary,
        gender=employee_in.gender,
        email=employee_in.email,
        referred_by_id=employee_in.referred_by_id,
        hashed_password=get_password_hash(employee_in.password)  
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
    Finds the 10 most recent jobs that match the worker's category and city.
    """
    query = {
        "category": current_employee.category,
        "location": {"$regex": current_employee.location_name, "$options": "i"},
        "is_active": True
    }

    jobs = await Job.find(query).sort("-created_at").limit(10).to_list()

    return [{
        "job_id": str(j.id),
        "title": j.title,
        "salary": j.salary_range,
        "posted_at": j.created_at
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
        employer_id=job.employer_id, # Added to match the model schema!
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

# --- Catgory of jobs ---

@router.get("/categories", response_model=List[str])
async def get_all_categories():
    """
    Returns a simple list of category names for the registration dropdown.
    """
    categories = await Category.find(Category.is_active == True).to_list()
    return [c.name for c in categories]