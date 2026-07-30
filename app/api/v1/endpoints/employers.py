# --- IMPORTS ---
from fastapi import APIRouter, File, HTTPException, UploadFile, status, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from beanie import PydanticObjectId
from bson import ObjectId
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import uuid
import math
import re

# --- Core Imports ---
from app.core.config import settings

# --- Dependencies Imports ---
from app.api.dependencies import get_current_employer

# --- Models Imports ---
from app.models.employer import Employer, EmployerType, SubscriptionTier, KYCStatus, VerificationSource
from app.models.employee import Employee
from app.models.subscriptions import Subscription
from app.models.transaction import Transaction
from app.models.notification import Notification, NotificationType
from app.models.contact import ContactUnlock
from app.models.payment import Payment
from app.models.review import Review 
from app.models.job import Job
from app.models.auth import OTP
from app.models.saved_search import SavedSearch
from app.models.application import JobApplication, ApplicationStatus

# --- Service Imports ---
from app.services.notification import NotificationService
from app.services.subscriptions import SubscriptionService
from app.services.resumes import ResumeService
from app.services.email import EmailService
from app.services.kyc import KYCService

# --- Utility Imports ---
from app.utils.referral import generate_referral_code
from app.utils.maps import MapService

# --- Schema Imports ---
from app.schemas.job import JobResponse
from app.schemas.search import CandidateSearchRequest
from app.schemas.search import SavedSearchCreate, SavedSearchResponse
from app.schemas.billing import (
    TransactionResponse,
    PaymentResponse,
    BillingProfileUpdateRequest
)
from app.schemas.employer import (
    EmployerDashboardResponse,
    EmployerCompleteProfileRequest,
    EmployerPersonalProfileResponse,
    EmployerCompanyProfileResponse,
    EmployerProfileUpdateRequest,
    ReferralDashboardResponse,
    KYCSubmitRequest
)

router = APIRouter()

# --- PYDANTIC SCHEMAS ---
class CompleteEmployerProfileRequest(BaseModel):
    company_name: str = Field(..., description="The name of the business")
    email: EmailStr
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

class RateEmployeeRequest(BaseModel):
    rating: float = Field(..., ge=1.0, le=5.0, description="Rating between 1 and 5")
    comment: str

class ApplicationStatusUpdate(BaseModel):
    new_status: ApplicationStatus | str

class UpdatePhoneRequest(BaseModel):
    new_phone: str = Field(..., description="The new 10-digit mobile number")
    otp_code: str = Field(..., description="The 4-digit OTP sent to the NEW number")

class EmployeeSearchFilter(BaseModel):
    category: Optional[str] = None
    skills: Optional[List[str]] = []
    city: Optional[str] = None
    max_distance_km: Optional[int] = 10
    min_experience_years: Optional[int] = 0

    # --- Pagination Variables ---
    page: int = Field(default=1, ge=1, description="Page number (starts at 1)")
    limit: int = Field(default=20, ge=1, le=100, description="Items per page (max 100)")

# --- PROFILE COMPLETION & DASHBOARD ---
@router.patch("/profile/complete", response_model=dict, status_code=status.HTTP_200_OK)
async def complete_employer_profile(
    data: CompleteEmployerProfileRequest, 
    background_tasks: BackgroundTasks,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Completes the employer's profile, including geocoding
    and subscription initialization via bulk dynamic updates.
    """
    # Convert the incoming data into a dictionary, ignoring any fields the frontend didn't send
    update_dict = data.model_dump(exclude_unset=True)

    # Check for email duplication (excluding current user) & Handle Verification
    if "email" in update_dict and update_dict["email"]:
        existing_employer = await Employer.find_one({"email": update_dict["email"], "_id": {"$ne": current_employer.id}})
        if existing_employer:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email already exists."
            )
        
        # If they update their email, reset the verification flag
        if update_dict["email"] != getattr(current_employer, "email", None):
            current_employer.email_verified = False

    # Geocode Address via Ola Maps
    # Check for either 'company_address' or 'address' depending on how your schema is defined
    address_field = update_dict.get("company_address") or update_dict.get("address")
    if address_field:
        coords = await MapService.get_coordinates(address_field)
        if coords:
            # Inject the formatted data back into the dictionary
            update_dict["address"] = coords["formatted_address"]
            update_dict["location"] = {
                "type": "Point",
                "coordinates": [coords["longitude"], coords["latitude"]]
            }
            # Clean up the old schema key if it was named 'company_address'
            if "company_address" in update_dict:
                del update_dict["company_address"]

    # Dynamically apply all finalized fields to the database model
    for field, value in update_dict.items():
        setattr(current_employer, field, value)
        
    await current_employer.save()

    # Initialize Free Tier Subscription & Welcome Email (Background Task)
    async def setup_new_employer(emp_id: str, email: str, name: str):
        # NOTE: You may want to add a check here to ensure a subscription doesn't already exist
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

    # Determine if the profile has enough data to be considered "complete"
    is_profile_complete = bool(
        getattr(current_employer, "name", None) and 
        getattr(current_employer, "company_name", None) and 
        getattr(current_employer, "email", None) and
        getattr(current_employer, "industry", None)
    )

    return {
        "status": "success",
        "message": "Employer profile completed successfully.",
        "is_profile_complete": is_profile_complete,
        "email_verified": getattr(current_employer, "email_verified", False),
        "employer": EmployerResponse(
            id=str(current_employer.id),
            owner_name=current_employer.name,
            company_name=current_employer.company_name,
            email=current_employer.email,
            phone=current_employer.phone,
            is_verified=current_employer.is_verified
        )
    }
# --- EMPLOYER DASHBOARD ---
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

# --- GET PERSONAL PROFILE ---
@router.get("/profile/personal", response_model=EmployerPersonalProfileResponse)
async def get_personal_profile(
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Retrieves the individual recruiter/owner's personal details.
    """
    return current_employer

# --- GET COMPANY PROFILE ---
@router.get("/profile/company", response_model=EmployerCompanyProfileResponse)
async def get_company_profile(
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Retrieves the business/company details.
    """
    return current_employer

# --- UPLOAD PROFILE PICTURE / LOGO ---
@router.post("/profile/upload_logo")
async def upload_company_logo(
    file: UploadFile = File(...),
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Uploads a company logo or profile picture for the employer.
    Accepts image files (JPEG, PNG).
    """
    # Validate the file type
    allowed_content_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Please upload a JPEG, PNG, or WEBP image."
        )
        
    # Validate file size - e.g., max 5MB
    # Note: FastAPI loads large files to disk automatically, but checking size is good practice
    file.file.seek(0, 2) # Go to the end of the file
    file_size = file.file.tell() # Get the size
    file.file.seek(0) # Reset the cursor back to the beginning for reading
    
    if file_size > 5 * 1024 * 1024: # 5 Megabytes
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size too large. Maximum size is 5MB."
        )

    # Generate a unique filename to prevent overwrites
    file_extension = file.filename.split(".")[-1]
    unique_filename = f"employer_{current_employer.id}_{uuid.uuid4().hex[:8]}.{file_extension}"
    
    # UPLOAD TO CLOUD STORAGE (AWS S3, Cloudinary, etc.)
    # Replace this block with your actual cloud upload logic.
    # Example:
    # uploaded_url = await s3_client.upload(file.file, unique_filename)
    
    # --- MOCK UPLOAD (For demonstration purposes) ---
    uploaded_url = f"https://your-cloud-storage.com/uploads/logos/{unique_filename}"
    # ---------------------------------------------------------

    # Update the employer's profile in the database
    current_employer.logo_url = uploaded_url
    await current_employer.save()
    
    return {
        "message": "Logo uploaded successfully",
        "logo_url": current_employer.logo_url
    }

# --- UPDATE PERSONAL & COMPANY PROFILE ---
@router.put("/profile_update")
async def update_employer_profile(
    profile_data: EmployerProfileUpdateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Updates both the individual recruiter/owner's personal details 
    and the business/company details safely.
    """
    update_dict = profile_data.model_dump(exclude_unset=True)
    
    # 1. Check for email duplication (excluding current user)
    if update_dict.get("email"):
        existing_employer = await Employer.find_one(
            {"email": update_dict["email"], "_id": {"$ne": current_employer.id}}
        )
        if existing_employer:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An account with this email already exists."
            )
            
        # If they change their email, we must mark it as unverified again
        if update_dict["email"] != current_employer.email:
            current_employer.email_verified = False
            
    # 2. Apply changes dynamically and SAFELY
    for field, value in update_dict.items():
        # Handle email separately since we validated it above
        if field == "email":
            current_employer.email = value
            continue
            
        # THE FIX: Only update fields that actually exist in the database model
        if hasattr(current_employer, field):
            setattr(current_employer, field, value)
        else:
            print(f"DEBUG: Ignored unknown field '{field}' from frontend payload.")
            
    # 3. Gracefully catch Pydantic validation errors (The Swagger UI "string" fix)
    try:
        await current_employer.save()
    except ValidationError as e:
        raise HTTPException(
            status_code=422, 
            detail=f"Data format error. Please check your payload: {e.errors()}"
        )
    
    # 4. Safely return values using getattr to prevent missing field crashes
    return {
        "message": "Profile updated successfully",
        "name": getattr(current_employer, "name", None),
        "company_name": getattr(current_employer, "company_name", None),
        "email": getattr(current_employer, "email", None),
        "email_verified": getattr(current_employer, "email_verified", False),
        "gstin": getattr(current_employer, "gstin", None)
    }

# --- PROFILE & ACCOUNT MANAGEMENT (SECURE) ---
@router.patch("/profile/phone_no_update", status_code=status.HTTP_200_OK)
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
    
    # Compare the provided OTP with the one stored in the database
    if not otp_record or not otp_record.code or otp_record.code != data.otp_code:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid or expired OTP for the new phone number."
        )

    otp_record.code = None # Consume the OTP after successful verification
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

# --- KYC SUBMISSION ---
@router.post("/kyc/submit")
async def submit_kyc(data: KYCSubmitRequest, current_user_id: str = Depends(get_current_employer)):
    employer = await Employer.get(current_user_id)
    if not employer:
        raise HTTPException(status_code=404, detail="Employer not found")

    # Save the documents
    employer.kyc_documents = data.model_dump()

    # Trigger Automated Verification
    is_verified, remarks = await KYCService.automated_verify(employer.kyc_documents)

    if is_verified:
        employer.kyc_status = KYCStatus.VERIFIED
        employer.verified_by = VerificationSource.SYSTEM
        employer.verified_at = datetime.now(timezone.utc)
        employer.kyc_remarks = remarks
        employer.is_verified = True 
    else:
        # Automated failed -> Send to Admin Queue
        employer.kyc_status = KYCStatus.PENDING
        employer.kyc_remarks = f"Auto-verify failed: {remarks}. Awaiting manual admin review."
        employer.is_verified = False

    await employer.save()

    await NotificationService.notify_user(
    user_id="ADMIN_BROADCAST",
    title="Action Required: New KYC Submission",
    message=f"Employer '{employer.company_name}' has submitted their KYC documents for review.",
    notif_type=NotificationType.KYC_SUBMITTED,
    related_entity_id=str(employer.id)
)
    
    return {
        "status": "success", 
        "kyc_status": employer.kyc_status,
        "message": "KYC submitted. " + ("Verified automatically!" if is_verified else "Under admin review.")
    }

# --- My Posted Job List ---
@router.get("/my_jobs", response_model=List[JobResponse])
async def get_my_jobs(
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Retrieves a list of all job posts created by the currently logged-in employer.
    This includes published jobs, drafts, and expired jobs so the employer can manage them.
    """
    employer_id_str = str(current_employer.id)
    
    # Query the database for jobs matching this specific employer
    # We use $or to safely catch the ID whether it was saved as a string or an ObjectId
    my_jobs = await Job.find({
        "$or": [
            {"employer_id": employer_id_str},
            {"employer_id": ObjectId(employer_id_str)}
        ]
    }).sort("-created_at").to_list() # Sorts so the newest jobs appear at the top

    # Return the list (FastAPI will automatically format it to your JobResponse schema)
    return my_jobs

# --- CORE RECRUITMENT FLOW (ATS) ---
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
        employee = await Employee.get(PydanticObjectId(app.employee_id))
        
        results.append({
            "application_id": str(app.id),
            "employee_id": str(app.employee_id),
            "employee_name": employee.name if employee else "Deleted Employee",
            
            # --- FIXED CRASH SAFEGUARD ---
            "employee_category": getattr(employee, "job_category", "N/A") if employee else "N/A",
            
            "employee_phone": employee.phone if employee else "N/A", 
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
    # Validate and fetch the application
    try:
        app_record = await JobApplication.get(PydanticObjectId(application_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid application ID format")
    
    if not app_record:
        raise HTTPException(status_code=404, detail="Application not found")

    # Fetch the job and verify ownership
    job = await Job.get(PydanticObjectId(app_record.job_id))
    if not job or str(job.employer_id) != str(current_employer.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Unauthorized to update this application."
        )

    # Update application status
    app_record.status = request.new_status
    if hasattr(app_record, "updated_at"):
        app_record.updated_at = datetime.utcnow()
    await app_record.save()

    # Safely get the string value of the status (handles both Enum and standard string)
    status_str = request.new_status.value if hasattr(request.new_status, 'value') else request.new_status
    job_closed = False

    # Handle "HIRED" logic (closing the job)
    if status_str.lower() == "hired":
        job.is_active = False
        if hasattr(job, "status"):
            job.status = "closed"
        await job.save()
        job_closed = True

    # Determine the notification title and message dynamically
    job_title = job.title if job else "a recent job"
    
    if status_str.lower() == "hired":
        title = "Hired! 🎉"
        message = f"Congratulations! You have been hired for the '{job_title}' role."
    elif status_str.lower() == "accepted":
        title = "Great News! Application Accepted 🎉"
        message = f"Your application for '{job_title}' has been accepted. The employer will contact you soon."
    elif status_str.lower() == "rejected":
        title = "Application Update"
        message = f"Unfortunately, the employer has decided to move forward with other candidates for '{job_title}'."
    else:
        title = "Application Status Updated"
        message = f"Your application for '{job_title}' is now marked as {status_str}."

    # Fire the unified notification via the Service
    await NotificationService.notify_user(
        user_id=str(app_record.employee_id),
        title=title,
        message=message,
        notif_type=NotificationType.APPLICATION_UPDATE,
        related_entity_id=str(app_record.id)
    )

    return {
        "message": f"Candidate status updated to {status_str}",
        "application_id": application_id,
        "job_closed": job_closed
    }

# --- CONTACT UNLOCKS ---
@router.post("/unlock-employee/{employee_id}")
async def unlock_employee_contact(employee_id: str, current_employer: Employer = Depends(get_current_employer)):
    await SubscriptionService.check_quota(str(current_employer.id), action_type="contact_view")
    employee = await Employee.get(PydanticObjectId(employee_id))
    if not employee: raise HTTPException(status_code=404, detail="Employee not found")

    await SubscriptionService.increment_usage(str(current_employer.id), action_type="contact_view")
    unlock_record = ContactUnlock(employer_id=current_employer.id, employee_id=ObjectId(employee_id))
    await unlock_record.insert()

    return {"name": employee.name, "phone": employee.phone, "message": "Contact unlocked!"}

# --- DISCOVERY, SEARCH & ACTIONS ---
@router.post("/employee-search")
async def search_employees(filters: EmployeeSearchFilter):
    """
    Advanced Paginated Employee Search.
    """
    # Build the query
    query = {"is_looking_for_job": True}
    
    if filters.category:
        query["category"] = filters.category
    if filters.skills:
        query["skills"] = {"$in": filters.skills} 
    if filters.city:
        query["location_name"] = filters.city
        
    # Count TOTAL matching documents first (before limiting)
    # The frontend needs this to calculate how many pages exist
    total_matches = await Employee.find(query).count()
    
    # Calculate how many documents to skip
    # Example: Page 1 skips 0. Page 2 skips 20. Page 3 skips 40.
    skip_count = (filters.page - 1) * filters.limit
    
    # Fetch ONLY the requested chunk using .skip() and .limit()
    matching_employees = await Employee.find(query).skip(skip_count).limit(filters.limit).to_list()
    
    # Calculate total pages
    total_pages = math.ceil(total_matches / filters.limit) if total_matches > 0 else 1
    
    # Return standard paginated response
    return {
        "pagination": {
            "current_page": filters.page,
            "limit": filters.limit,
            "total_matches": total_matches,
            "total_pages": total_pages,
            "has_next_page": filters.page < total_pages,
            "has_prev_page": filters.page > 1
        },
        "results": matching_employees
    }

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

# --- SAVED SEARCHES ---
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

# --- RATINGS & REVIEWS ---
@router.post("/rate-employee/{employee_id}")
async def rate_employee(
    employee_id: str, 
    request: RateEmployeeRequest, 
    current_employer: Employer = Depends(get_current_employer)
):
    has_unlocked = await ContactUnlock.find_one({
        "employer_id": current_employer.id,
        "employee_id": ObjectId(employee_id)
    })
    
    if not has_unlocked:
        raise HTTPException(
            status_code=403, 
            detail="You can only leave a review for employees whose contact you have unlocked."
        )

    employee = await Employee.get(ObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    current_rating = getattr(employee, 'rating', 0.0)
    
    if current_rating == 0.0:
        employee.rating = request.rating
    else:
        employee.rating = round((current_rating + request.rating) / 2, 1) 
        
    await employee.save()
    
    new_alert = Notification(
        user_id=current_employer.id,
        title="Review Submitted",
        message=f"You successfully gave {employee.name} a {request.rating}-star review.",
        is_read=False
    )
    await new_alert.save()

    return {
        "message": "Review submitted successfully!",
        "employee": employee.name,
        "new_rating": employee.rating
    }

# --- UPDATE BILLING PROFILE (GSTIN) ---
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

# --- CREDITS & USAGE (Virtual Coins Ledger) ---
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

# --- BILLING HISTORY (Real Money Purchases) ---
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

# --- REFERRAL DASHBOARD ---
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