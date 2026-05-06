from fastapi import APIRouter, HTTPException, status, Depends
from fastapi.responses import StreamingResponse
from httpcore import request
from pydantic import BaseModel, EmailStr, Field
from bson import ObjectId
from datetime import datetime, timedelta
from typing import List, Optional
from requests import request
from beanie import PydanticObjectId

# --- Model & Service Imports ---
from app.models.employer import Employer, EmployerType, SubscriptionTier 
from app.models.employee import Employee, Application, ApplicationStatus
from app.models.subscriptions import Subscription
from app.models.notification import Notification
from app.models.contact import ContactUnlock
from app.models.review import Review 
from app.models.job import Job  
from app.models.notification import Notification
from app.core.security import create_access_token, get_password_hash
from app.api.dependencies import get_current_employer
from app.services.subscriptions import SubscriptionService
from app.services.resumes import ResumeService
from app.schemas.employer import EmployerDashboardResponse
from app.models.application import JobApplication

router = APIRouter()

# --- Pydantic Schemas ---

class IndividualRegistration(BaseModel):
    name: str
    phone: str
    password: str  
    location: str

class CompanyRegistration(BaseModel):
    company_name: str
    contact_name: str
    phone: str
    email: EmailStr
    password: str  
    location: str
    gst_number: str

class RateWorkerRequest(BaseModel):
    rating: float = Field(..., ge=1.0, le=5.0, description="Rating between 1 and 5")
    comment: str

class ApplicationStatusUpdate(BaseModel):
    new_status: ApplicationStatus

# --- 1. CORE RECRUITMENT FLOW (ATS) ---

@router.get("/my-jobs", response_model=List[Job]) # <--- Change dict to Job
async def list_employer_jobs(current_employer: Employer = Depends(get_current_employer)):
    """Aman sees every job he has ever posted."""
    # This returns a list of Job objects
    jobs = await Job.find(Job.employer_id == str(current_employer.id)).to_list()
    return jobs

@router.get("/jobs/{job_id}/applicants", response_model=List[dict])
async def list_job_applicants(
    job_id: str, 
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Returns a hydrated list of all workers who applied for a specific job.
    Includes flexible search to prevent String/ObjectId type mismatches.
    """
    # 1. Verification: Does the Employer own this job?
    try:
        job = await Job.get(PydanticObjectId(job_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Job ID format")
        
    if not job or str(job.employer_id) != str(current_employer.id):
        raise HTTPException(status_code=403, detail="Unauthorized or job not found.")

    # 2. Flexible Search: Support both String and ObjectId formats in the job_id field
    applications = await JobApplication.find({
        "$or": [
            {"job_id": job_id},
            {"job_id": ObjectId(job_id)}
        ]
    }).to_list()

    # 3. Hydrate the Results (Merging application status with worker profile data)
    results = []
    for app in applications:
        worker = await Employee.get(PydanticObjectId(app.employee_id))
        
        results.append({
            "application_id": str(app.id),
            "worker_id": str(app.employee_id),
            "worker_name": worker.name if worker else "Deleted Worker",
            "worker_category": worker.category if worker else "N/A",
            "worker_phone": worker.phone if worker else "N/A", # Critical for contact
            "status": getattr(app, "status", "applied"),
            "applied_at": getattr(app, "applied_at", datetime.utcnow())
        })
    
    return results

@router.patch("/applications/{application_id}/status")
async def update_application_status(
    application_id: str,
    request: ApplicationStatusUpdate, # <--- Now FastAPI knows to expect a JSON body!
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Updates application status (Shortlist/Hire/Reject).
    If status is HIRED, it automatically closes the job and notifies the worker.
    """
    
    # 1. Safely fetch the application record
    try:
        app_record = await JobApplication.get(PydanticObjectId(application_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid application ID format")
    
    if not app_record:
        raise HTTPException(status_code=404, detail="Application not found")

    # 2. Security Check: Verify employer ownership via the parent Job
    job = await Job.get(PydanticObjectId(app_record.job_id))
    if not job or str(job.employer_id) != str(current_employer.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Unauthorized to update this application."
        )

    # 3. Update status and timestamp
    app_record.status = request.new_status # <--- Updated to use request payload
    if hasattr(app_record, "updated_at"):
        app_record.updated_at = datetime.utcnow()
    await app_record.save()

    # 4. Automation: If 'HIRED', close the job and notify the worker
    job_closed = False
    if request.new_status == ApplicationStatus.HIRED: # <--- Updated
        # Close the job posting
        job.is_active = False
        if hasattr(job, "status"):
            job.status = "closed"
        await job.save()
        job_closed = True

        # Send Notification to the Worker (Raju)
        new_notif = Notification(
            user_id=app_record.employee_id,
            title="Hired!",
            message=f"Congratulations! You have been hired for the '{job.title}' role.",
            is_read=False
        )
        await new_notif.save()

    return {
        "message": f"Candidate status updated to {request.new_status.value if hasattr(request.new_status, 'value') else request.new_status}",
        "application_id": application_id,
        "job_closed": job_closed
    }

# --- 2. REGISTRATION & DASHBOARD ---

@router.post("/register/individual", status_code=status.HTTP_201_CREATED)
async def register_individual(data: IndividualRegistration):
    existing_employer = await Employer.find_one(Employer.phone == data.phone)
    if existing_employer:
        raise HTTPException(status_code=400, detail="Phone number already registered.")
    
    hashed_pw = get_password_hash(data.password)
    employer = Employer(
        employer_type=EmployerType.INDIVIDUAL,
        name=data.name,
        phone=data.phone,
        location=data.location,
        hashed_password=hashed_pw,
        subscription_tier=SubscriptionTier.FREE 
    )
    await employer.insert()
    
    free_sub = Subscription(
        employer_id=str(employer.id),
        plan_type="free",
        is_active=True,
        start_date=datetime.utcnow(),
        expiry_date=datetime.utcnow() + timedelta(days=30),
        contacts_checked=0,
        resumes_downloaded=0,
        jobs_posted=0,
        india_level_jobs_posted=0
    )
    await free_sub.insert()
    access_token = create_access_token(subject=str(employer.id), user_type="employer")
    return {"access_token": access_token, "employer_id": str(employer.id)}

@router.post("/register/company", status_code=status.HTTP_201_CREATED)
async def register_company(data: CompanyRegistration):
    existing_employer = await Employer.find_one(Employer.phone == data.phone)
    if existing_employer:
        raise HTTPException(status_code=400, detail="Phone number already registered.")
        
    hashed_pw = get_password_hash(data.password)
    employer = Employer(
        employer_type=EmployerType.COMPANY,
        company_name=data.company_name,
        contact_name=data.contact_name,
        phone=data.phone,
        email=data.email,
        location=data.location,
        gst_number=data.gst_number,
        hashed_password=hashed_pw,
        is_gst_verified=False 
    )
    await employer.insert()
    
    base_sub = Subscription(
        employer_id=str(employer.id),
        plan_type="free",
        is_active=True,
        start_date=datetime.utcnow(),
        expiry_date=datetime.utcnow() + timedelta(days=30),
        contacts_checked=0,
        resumes_downloaded=0,
        jobs_posted=0,
        india_level_jobs_posted=0
    )
    await base_sub.insert()
    access_token = create_access_token(subject=str(employer.id), user_type="employer")
    return {"message": "Company registered successfully.", "access_token": access_token}

@router.get("/dashboard", response_model=EmployerDashboardResponse)
async def get_employer_dashboard(current_employer: Employer = Depends(get_current_employer)):
    sub = await SubscriptionService.get_active_subscription(str(current_employer.id))
    active_jobs = await Job.find(Job.employer_id == str(current_employer.id), Job.is_active == True).count()
    total_apps = await Application.find(Application.job_id == {"$in": [str(j.id) for j in await Job.find(Job.employer_id == str(current_employer.id)).to_list()]}).count()
    shortlisted = await Application.find(Application.status == ApplicationStatus.SHORTLISTED).count() # Simplified for brevity

    days_left = max(0, (sub.expiry_date - datetime.utcnow()).days) if sub.expiry_date else 0
    return EmployerDashboardResponse(
        company_name=current_employer.company_name or current_employer.name,
        subscription_tier=sub.plan_type.capitalize(),        is_active=sub.is_active,
        days_left=days_left,
        expiry_date=sub.expiry_date,
        active_jobs_count=active_jobs,
        total_applicants_count=total_apps,
        shortlisted_count=shortlisted,
        job_posts_used=sub.jobs_posted, 
        contacts_viewed=sub.contacts_checked
    )

# --- 3. DISCOVERY, SEARCH & ACTIONS ---

@router.get("/search-workers", response_model=List[dict])
async def search_workers(
    category: Optional[str] = None,
    location: Optional[str] = None,
    min_experience: int = 0,
    current_employer: Employer = Depends(get_current_employer)
):
    query = {"is_available": True}
    if category: query["category"] = category
    if location: query["location"] = {"$regex": location, "$options": "i"}
    if min_experience > 0: query["experience_years"] = {"$gte": min_experience}

    workers = await Employee.find(query).to_list()
    return [{
        "id": str(w.id), "name": w.name, "category": w.category, 
        "location": w.location, "experience": getattr(w, "experience_years", 0),
        "rate": getattr(w, "daily_rate", None), "rating": getattr(w, "rating", 0.0) 
    } for w in workers]

@router.post("/unlock-worker/{worker_id}")
async def unlock_worker_contact(worker_id: str, current_employer: Employer = Depends(get_current_employer)):
    await SubscriptionService.check_quota(str(current_employer.id), action_type="contact_view")
    worker = await Employee.get(PydanticObjectId(worker_id))
    if not worker: raise HTTPException(status_code=404, detail="Worker not found")

    await SubscriptionService.increment_usage(str(current_employer.id), action_type="contact_view")
    unlock_record = ContactUnlock(employer_id=current_employer.id, worker_id=ObjectId(worker_id))
    await unlock_record.insert()

    return {"name": worker.name, "phone": worker.phone, "message": "Contact unlocked!"}

# --- 4. NOTIFICATIONS & RESUMES ---

@router.get("/notifications")
async def get_employer_notifications(current_employer: Employer = Depends(get_current_employer)):
    """Fetches the latest 20 notifications for the logged-in employer."""
    
    # 1. Match the field name (user_id)
    # 2. NO str() wrap around current_employer.id!
    notifications = await Notification.find(
        Notification.user_id == current_employer.id 
    ).sort("-created_at").limit(20).to_list()
    
    return notifications

@router.get("/download-resume/{worker_id}")
async def download_worker_resume(
    worker_id: str, 
    current_employer: Employer = Depends(get_current_employer)
):
    # 1. Check if Employer has enough quota
    await SubscriptionService.check_quota(str(current_employer.id), action_type="download_resume")
    
    # 2. Fetch the Worker from the database
    worker = await Employee.get(PydanticObjectId(worker_id))
    if not worker: 
        raise HTTPException(status_code=404, detail="Worker not found")

    # 3. Generate the PDF (Synchronously - no 'await')
    pdf_buffer = ResumeService.generate_pdf(worker)
    
    # 4. Deduct the quota AFTER successful generation
    await SubscriptionService.increment_usage(str(current_employer.id), action_type="download_resume")
    
    # 5. Stream the file back to the client
    # Added a safe filename replacement just in case the worker has spaces in their name
    safe_name = getattr(worker, 'name', 'Worker').replace(" ", "_")
    
    return StreamingResponse(
        pdf_buffer, 
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Resume_{safe_name}.pdf"}
    )

# --- 5. RATINGS & REVIEWS ---
from app.models.contact import ContactUnlock

@router.post("/rate-worker/{worker_id}")
async def rate_worker(
    worker_id: str, 
    request: RateWorkerRequest, 
    current_employer: Employer = Depends(get_current_employer)
):
    """Allows an employer to rate a worker ONLY if they have unlocked their contact."""
    
    # 1. Security Check: Did Aman actually unlock Raju?
    # We use ContactUnlock to verify they had a business transaction.
    has_unlocked = await ContactUnlock.find_one({
        "employer_id": current_employer.id,
        "worker_id": ObjectId(worker_id)
    })
    
    if not has_unlocked:
        raise HTTPException(
            status_code=403, 
            detail="You can only leave a review for workers whose contact you have unlocked."
        )

    # 2. Fetch the Worker
    worker = await Employee.get(ObjectId(worker_id))
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    # 3. Update the Rating (Simple Moving Average for testing)
    current_rating = getattr(worker, 'rating', 0.0)
    
    if current_rating == 0.0:
        worker.rating = request.rating # First review!
    else:
        # Average the old rating with the new rating
        worker.rating = round((current_rating + request.rating) / 2, 1) 
        
    await worker.save()
    
    # Create a notification for the employer
    new_alert = Notification(
        user_id=current_employer.id,
        title="Review Submitted",
        message=f"You successfully gave {worker.name} a {request.rating}-star review.",
        is_read=False
    )
    await new_alert.save()

    return {
        "message": "Review submitted successfully!",
        "worker": worker.name,
        "new_rating": worker.rating
    }