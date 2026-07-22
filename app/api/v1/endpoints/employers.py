# --- IMPORTS ---
from fastapi import APIRouter, HTTPException, status, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from bson import ObjectId
from datetime import datetime, timedelta
from typing import List, Optional
from beanie import PydanticObjectId
import re

# --- Core & Settings ---
from app.core.config import settings
from app.core.security import verify_password, get_password_hash
from app.api.dependencies import get_current_employer

# --- Model & Service Imports ---
from app.models.employer import Employer, EmployerType, SubscriptionTier 
from app.models.employee import Employee
from app.models.subscriptions import Subscription
from app.models.transaction import Transaction
from app.models.notification import Notification
from app.models.contact import ContactUnlock
from app.models.payment import Payment
from app.models.review import Review 
from app.models.job import Job  
from app.models.auth import OTP
from app.models.saved_search import SavedSearch
from app.models.application import JobApplication, ApplicationStatus

# --- Service Imports ---
from app.services.subscriptions import SubscriptionService
from app.services.resumes import ResumeService
from app.services.email import EmailService
from app.schemas.employer import EmployerDashboardResponse
from app.schemas.search import CandidateSearchRequest
from app.schemas.search import SavedSearchCreate, SavedSearchResponse
from app.schemas.employer import EmployerPersonalProfileUpdate, EmployerCompanyProfileUpdate, ReferralDashboardResponse
from app.schemas.billing import TransactionResponse, PaymentResponse, BillingProfileUpdateRequest

# --- Utility Imports ---
from app.utils.referral import generate_referral_code
from app.utils.maps import MapService

router = APIRouter()

# =====================================================================
# PYDANTIC SCHEMAS
# =====================================================================

class CompleteEmployerProfileRequest(BaseModel):
    company_name: str = Field(..., description="The name of the business")
    email: EmailStr
    password: str = Field(..., min_length=6)
    company_address: str
    industry: Optional[str] = None
    company_size: Optional[str] = None
    gstin: str | None = None

class EmployerResponse(BaseModel):
    id: str
    owner_name: str
    company_name: str
    email: str
    phone: str
    is_verified: bool

class RateWorkerRequest(BaseModel):
    rating: float = Field(..., ge=1.0, le=5.0, description="Rating between 1 and 5")
    comment: str

class ApplicationStatusUpdate(BaseModel):
    new_status: ApplicationStatus

class UpdatePhoneRequest(BaseModel):
    new_phone: str = Field(..., description="The new 10-digit mobile number")
    otp_code: str = Field(..., description="The 6-digit OTP sent to the NEW number")


# =====================================================================
# PROFILE COMPLETION & DASHBOARD 
# =====================================================================

@router.patch("/profile/complete", response_model=dict, status_code=status.HTTP_200_OK)
async def complete_employer_profile(
    data: CompleteEmployerProfileRequest, 
    background_tasks: BackgroundTasks,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Completes the employer's profile after they have verified their phone number.
    Requires the access_token received from the Auth endpoint.
    """
    # 1. Check for email duplication (excluding current user)
    existing_employer = await Employer.find_one({"email": data.email, "_id": {"$ne": current_employer.id}})
    if existing_employer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists."
        )

    # 2. Geocode Address via Ola Maps
    coords = await MapService.get_coordinates(data.company_address)
    formatted_address = data.company_address
    location_geo = None

    if coords:
        formatted_address = coords["formatted_address"]
        location_geo = {
            "type": "Point",
            "coordinates": [coords["longitude"], coords["latitude"]]
        }

    # 3. Hash Password & Update Employer
    hashed_password = get_password_hash(data.password)
    
    current_employer.company_name = data.company_name
    current_employer.email = data.email
    current_employer.hashed_password = hashed_password
    current_employer.address = formatted_address
    current_employer.location = location_geo
    current_employer.gstin = data.gstin
    current_employer.industry = data.industry
    current_employer.company_size = data.company_size
    
    await current_employer.save()

    # 4. Initialize Free Tier Subscription (Background Task)
    async def setup_new_employer(emp_id: str, email: str, name: str):
        base_sub = Subscription(
            employer_id=emp_id,
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
        if email:
            await EmailService.send_welcome_email(to_email=email, user_name=name)

    background_tasks.add_task(setup_new_employer, str(current_employer.id), current_employer.email, current_employer.name)

    return {
        "status": "success",
        "message": "Employer profile completed successfully.",
        "employer": EmployerResponse(
            id=str(current_employer.id),
            owner_name=current_employer.name,
            company_name=current_employer.company_name,
            email=current_employer.email,
            phone=current_employer.phone,
            is_verified=current_employer.is_verified
        )
    }

@router.get("/dashboard", response_model=EmployerDashboardResponse)
async def get_employer_dashboard(current_employer: Employer = Depends(get_current_employer)):
    sub = await SubscriptionService.get_active_subscription(str(current_employer.id))
    
    my_jobs = await Job.find(Job.employer_id == str(current_employer.id)).to_list()
    my_job_ids = [str(job.id) for job in my_jobs]
    
    active_jobs = sum(1 for job in my_jobs if job.status == "published")
    
    if my_job_ids:
        total_apps = await JobApplication.find(
            {"job_id": {"$in": my_job_ids}}
        ).count()
        
        shortlisted = await JobApplication.find(
            {"job_id": {"$in": my_job_ids}, "status": ApplicationStatus.SHORTLISTED}
        ).count()
    else:
        total_apps = 0
        shortlisted = 0

    days_left = max(0, (sub.expiry_date - datetime.utcnow()).days) if sub.expiry_date else 0
    
    return EmployerDashboardResponse(
        company_name=current_employer.company_name or current_employer.name,
        subscription_tier=sub.plan_type.lower(),        
        is_active=sub.is_active,
        days_left=days_left,
        expiry_date=sub.expiry_date,
        active_jobs_count=active_jobs,
        total_applicants_count=total_apps,
        shortlisted_count=shortlisted,
        job_posts_used=sub.jobs_posted, 
        contacts_viewed=sub.contacts_checked
    )

# ==========================================
# 1. UPDATE PERSONAL PROFILE
# ==========================================

@router.put("/profile_personal_update")
async def update_personal_profile(
    profile_data: EmployerPersonalProfileUpdate,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Updates the individual recruiter/owner's personal details.
    """
    update_dict = profile_data.model_dump(exclude_unset=True)
    
    # If they change their email, we must mark it as unverified again
    if "email" in update_dict and update_dict["email"] != current_employer.email:
        current_employer.email_verified = False
        
    for field, value in update_dict.items():
        setattr(current_employer, field, value)
        
    await current_employer.save()
    
    return {
        "message": "Personal profile updated successfully",
        "name": current_employer.name,
        "email": current_employer.email,
        "email_verified": current_employer.email_verified,
        "gstin": getattr(current_employer, "gstin", None)
    }

# ==========================================
# 2. UPDATE COMPANY PROFILE
# ==========================================

@router.put("/profile_company_update")
async def update_company_profile(
    company_data: EmployerCompanyProfileUpdate,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Updates the business details.
    """
    update_dict = company_data.model_dump(exclude_unset=True)
    
    for field, value in update_dict.items():
        setattr(current_employer, field, value)
        
    await current_employer.save()
    
    return {
        "message": "Company profile updated successfully",
        "company_name": current_employer.company_name
    }

# ==========================================
# personal & company profile retrieval
# =========================================

@router.get("/me")
async def get_current_employer_profile(
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Returns the full employer document so the frontend can populate 
    both the Personal and Company profile screens.
    """
    employer_dict = current_employer.model_dump()
    employer_dict["id"] = str(current_employer.id)
    return employer_dict

# =====================================================================
# CORE RECRUITMENT FLOW (ATS)
# =====================================================================

@router.get("/my-jobs", response_model=List[Job]) 
async def list_employer_jobs(current_employer: Employer = Depends(get_current_employer)):
    """Sees every job the employer has ever posted."""
    jobs = await Job.find(Job.employer_id == str(current_employer.id)).to_list()
    return jobs

@router.get("/jobs/{job_id}/applicants", response_model=List[dict])
async def list_job_applicants(
    job_id: str, 
    current_employer: Employer = Depends(get_current_employer)
):
    try:
        job = await Job.get(PydanticObjectId(job_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Job ID format")
        
    if not job or str(job.employer_id) != str(current_employer.id):
        raise HTTPException(status_code=403, detail="Unauthorized or job not found.")

    applications = await JobApplication.find({
        "$or": [
            {"job_id": job_id},
            {"job_id": ObjectId(job_id)}
        ]
    }).to_list()

    results = []
    for app in applications:
        worker = await Employee.get(PydanticObjectId(app.employee_id))
        
        results.append({
            "application_id": str(app.id),
            "worker_id": str(app.employee_id),
            "worker_name": worker.name if worker else "Deleted Worker",
            "worker_category": worker.category if worker else "N/A",
            "worker_phone": worker.phone if worker else "N/A", 
            "status": getattr(app, "status", "applied"),
            "applied_at": getattr(app, "applied_at", datetime.utcnow())
        })
    
    return results

# --- Update Application Status ---
@router.patch("/applications/{application_id}/status")
async def update_application_status(
    application_id: str,
    request: ApplicationStatusUpdate, 
    current_employer: Employer = Depends(get_current_employer)
):
    try:
        app_record = await JobApplication.get(PydanticObjectId(application_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid application ID format")
    
    if not app_record:
        raise HTTPException(status_code=404, detail="Application not found")

    job = await Job.get(PydanticObjectId(app_record.job_id))
    if not job or str(job.employer_id) != str(current_employer.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Unauthorized to update this application."
        )

    app_record.status = request.new_status
    if hasattr(app_record, "updated_at"):
        app_record.updated_at = datetime.utcnow()
    await app_record.save()

    job_closed = False
    if request.new_status == ApplicationStatus.HIRED:
        job.is_active = False
        if hasattr(job, "status"):
            job.status = "closed"
        await job.save()
        job_closed = True

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


# =====================================================================
# DISCOVERY, SEARCH & ACTIONS
# =====================================================================

@router.post("/employee-search", response_model=List[dict])
async def search_candidates(
    search_data: CandidateSearchRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Advanced candidate search engine based on frontend filters.
    """
    query = {}
    
    # 1. Keyword Search (Matches against skills or job title)
    if search_data.keywords:
        # Using regex to make it case-insensitive
        regex_pattern = re.compile(search_data.keywords, re.IGNORECASE)
        query["$or"] = [
            {"skills": {"$regex": regex_pattern}},
            {"title": {"$regex": regex_pattern}}
        ]
        
    # 2. City / Region Search
    if search_data.city:
        query["location_name"] = {"$regex": re.compile(search_data.city, re.IGNORECASE)}
        
    # 3. Experience Range
    if search_data.min_experience is not None or search_data.max_experience is not None:
        query["total_experience"] = {}
        if search_data.min_experience is not None:
            query["total_experience"]["$gte"] = search_data.min_experience
        if search_data.max_experience is not None:
            query["total_experience"]["$lte"] = search_data.max_experience
            
    # 4. Salary Range (Assuming your Employee model tracks expected salary)
    if search_data.min_salary is not None or search_data.max_salary is not None:
        query["expected_salary"] = {}
        if search_data.min_salary is not None:
            query["expected_salary"]["$gte"] = search_data.min_salary
        if search_data.max_salary is not None:
            query["expected_salary"]["$lte"] = search_data.max_salary
            
    # 5. Education Levels
    if search_data.education_levels and len(search_data.education_levels) > 0:
        query["education"] = {"$in": search_data.education_levels}
        
    # Execute the query (limit to 50 results to prevent massive payloads)
    results = await Employee.find(query).limit(50).to_list()
    
    return results

# =====================================================================
# EMPLOYER-ONLY WORKER DISCOVERY
# =====================================================================

@router.get("/employee-search", response_model=List[dict])
async def search_employees(
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

#=====================================================================
# CONTACT UNLOCKS
#====================================================================

@router.post("/unlock-employee/{worker_id}")
async def unlock_worker_contact(worker_id: str, current_employer: Employer = Depends(get_current_employer)):
    await SubscriptionService.check_quota(str(current_employer.id), action_type="contact_view")
    worker = await Employee.get(PydanticObjectId(worker_id))
    if not worker: raise HTTPException(status_code=404, detail="Worker not found")

    await SubscriptionService.increment_usage(str(current_employer.id), action_type="contact_view")
    unlock_record = ContactUnlock(employer_id=current_employer.id, worker_id=ObjectId(worker_id))
    await unlock_record.insert()

    return {"name": worker.name, "phone": worker.phone, "message": "Contact unlocked!"}

#=====================================================================
# SAVED SEARCHES
#====================================================================

@router.post("/database/saved-searches", response_model=SavedSearchResponse)
async def save_candidate_search(
    save_data: SavedSearchCreate,
    current_employer: Employer = Depends(get_current_employer)
):
    """Saves a search query for later use."""
    new_saved_search = SavedSearch(
        employer_id=str(current_employer.id),
        title=save_data.title,
        filters=save_data.filters.model_dump(exclude_unset=True)
    )
    await new_saved_search.insert()
    
    # Map for response
    response_data = new_saved_search.model_dump()
    response_data["id"] = str(new_saved_search.id)
    return response_data

# --- Get Saved Searches ---
@router.get("/database/saved-searches", response_model=List[SavedSearchResponse])
async def get_saved_searches(
    current_employer: Employer = Depends(get_current_employer)
):
    """Retrieves all saved searches for the current employer."""
    searches = await SavedSearch.find({"employer_id": str(current_employer.id)}).sort("-created_at").to_list()
    
    response = []
    for search in searches:
        search_dict = search.model_dump()
        search_dict["id"] = str(search.id)
        response.append(search_dict)
        
    return response


# =====================================================================
# NOTIFICATIONS & RESUMES
# =====================================================================

@router.get("/notifications")
async def get_employer_notifications(current_employer: Employer = Depends(get_current_employer)):
    notifications = await Notification.find(
        Notification.user_id == current_employer.id 
    ).sort("-created_at").limit(20).to_list()
    
    return notifications

@router.get("/download-resume/{worker_id}")
async def download_worker_resume(
    worker_id: str, 
    current_employer: Employer = Depends(get_current_employer)
):
    await SubscriptionService.check_quota(str(current_employer.id), action_type="download_resume")
    
    worker = await Employee.get(PydanticObjectId(worker_id))
    if not worker: 
        raise HTTPException(status_code=404, detail="Worker not found")

    pdf_buffer = ResumeService.generate_pdf(worker)
    
    await SubscriptionService.increment_usage(str(current_employer.id), action_type="download_resume")
    
    safe_name = getattr(worker, 'name', 'Worker').replace(" ", "_")
    
    return StreamingResponse(
        pdf_buffer, 
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Resume_{safe_name}.pdf"}
    )


# =====================================================================
# RATINGS & REVIEWS
# =====================================================================

@router.post("/rate-worker/{worker_id}")
async def rate_worker(
    worker_id: str, 
    request: RateWorkerRequest, 
    current_employer: Employer = Depends(get_current_employer)
):
    has_unlocked = await ContactUnlock.find_one({
        "employer_id": current_employer.id,
        "worker_id": ObjectId(worker_id)
    })
    
    if not has_unlocked:
        raise HTTPException(
            status_code=403, 
            detail="You can only leave a review for workers whose contact you have unlocked."
        )

    worker = await Employee.get(ObjectId(worker_id))
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    current_rating = getattr(worker, 'rating', 0.0)
    
    if current_rating == 0.0:
        worker.rating = request.rating
    else:
        worker.rating = round((current_rating + request.rating) / 2, 1) 
        
    await worker.save()
    
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


# =====================================================================
# PROFILE & ACCOUNT MANAGEMENT (SECURE)
# =====================================================================

@router.patch("/me/phone", status_code=status.HTTP_200_OK)
async def update_employer_phone(
    data: UpdatePhoneRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    clean_new_phone = data.new_phone[-10:]

    if current_employer.phone == clean_new_phone:
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

    current_employer.phone = clean_new_phone
    if hasattr(current_employer, "updated_at"):
        current_employer.updated_at = datetime.utcnow()
        
    await current_employer.save()

    return {
        "status": "success", 
        "message": "Phone number successfully updated.", 
        "new_phone": current_employer.phone
    }

# ==========================================
# CREDITS & USAGE (Virtual Coins Ledger)
# ==========================================

@router.get("/credits/transactions", response_model=List[TransactionResponse])
async def get_credit_transactions(
    # Optional filter to match frontend tabs (Coins added, Coins spent, etc.)
    tab_filter: Optional[str] = None, 
    current_employer: Employer = Depends(get_current_employer)
):
    """Fetches the coin transaction history for the Credits & Usage tab."""
    query = {"employer_id": str(current_employer.id)}
    
    # Map frontend tab clicks to database transaction types
    if tab_filter == "added":
        query["transaction_type"] = "added"
    elif tab_filter == "spent":
        query["transaction_type"] = "spent"
    elif tab_filter == "returned":
        query["transaction_type"] = "returned"
        
    transactions = await Transaction.find(query).sort("-created_at").to_list()
    
    response = []
    for txn in transactions:
        txn_dict = txn.model_dump()
        txn_dict["id"] = str(txn.id)
        # Format the description string safely
        txn_dict["amount"] = f"+ {txn.amount}" if txn.amount > 0 else f"- {abs(txn.amount)}"
        response.append(txn_dict)
        
    return response

# ==========================================
# BILLING HISTORY (Real Money Purchases)
# ==========================================

@router.get("/billing/history", response_model=List[PaymentResponse])
async def get_billing_history(
    status_filter: Optional[str] = None, # "success", "pending", "failed"
    current_employer: Employer = Depends(get_current_employer)
):
    """Fetches the real-world purchase history for the Billing tab."""
    query = {"employer_id": str(current_employer.id)}
    
    if status_filter and status_filter.lower() != "all":
        query["status"] = status_filter.lower()
        
    payments = await Payment.find(query).sort("-created_at").to_list()
    
    response = []
    for payment in payments:
        pay_dict = payment.model_dump()
        pay_dict["id"] = str(payment.id)
        response.append(pay_dict)
        
    return response

# ==========================================
# UPDATE BILLING PROFILE (GSTIN)
# ==========================================

@router.put("/billing/profile")
async def update_billing_profile(
    profile_data: BillingProfileUpdateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """Updates the employer's GSTIN and billing address."""
    if profile_data.gstin is not None:
        current_employer.gstin = profile_data.gstin
    if profile_data.billing_address is not None:
        current_employer.billing_address = profile_data.billing_address
        
    await current_employer.save()
    
    return {"message": "Billing profile updated successfully", "gstin": current_employer.gstin}

# ==========================================
# REFERRAL DASHBOARD
# ==========================================

@router.get("/refer", response_model=ReferralDashboardResponse)
async def get_referral_dashboard(
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Provides data for the Refer & Earn frontend tab.
    """
    # Check if the field exists using getattr
    current_code = getattr(current_employer, "referral_code", None)

    if not current_code:
        # Generate the new code
        new_code = generate_referral_code()
        
        # 100% bypasses Pydantic by sending a raw PyMongo update command
        await current_employer.update({"$set": {"referral_code": new_code}})
        
        current_code = new_code

    # Count how many users have signed up using this employer's code
    total_referred = await Employer.find(
        {"referred_by_code": current_code}
    ).count()
    
    # Calculate total coins earned from referrals using the Transaction ledger
    referral_transactions = await Transaction.find(
        {"employer_id": str(current_employer.id), "title": "Referral Bonus"}
    ).to_list()
    
    total_coins = sum(txn.amount for txn in referral_transactions)
    
    return {
        "referral_code": current_code,
        "total_referred": total_referred,
        "total_coins_earned": total_coins
    }