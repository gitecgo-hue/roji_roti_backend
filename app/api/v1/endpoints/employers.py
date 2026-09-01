# --- IMPORTS ---
from PIL.ImagePalette import random
from apscheduler import job
from fastapi import APIRouter, File, HTTPException, UploadFile, status, Depends, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError
from beanie import PydanticObjectId
from bson import ObjectId
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import random as py_random
import uuid
import math
import re

# --- Core Imports ---
from app.core.config import settings

# --- Dependencies Imports ---
from app.api.dependencies import get_current_employer, get_current_employee
from app.api.v1.endpoints.employees import calculate_profile_completion

# --- Models Imports ---
from app.models.admin import Admin
from app.models.employer import Employer, EmployerType, SubscriptionTier, KYCStatus, VerificationSource, GeoLocation
from app.models.employee import Employee, ProfileMetadata, Skill, Education, ProfileDocument
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
from app.services.cloudinary_service import upload_file
from app.services.cloudinary_service import delete_file
from app.services.notification import NotificationService
from app.services.otp import OTPService
from app.services.subscriptions import SubscriptionService
from app.services.resumes import ResumeService
from app.services.email import EmailService
from app.services.kyc import KYCService

# --- Utility Imports ---
from app.utils.referral import generate_referral_code
from app.utils.maps import MapService
from app.utils.translator import translate_document_fields 
from app.utils.helpers import apply_translations

# --- Schema Imports ---
from app.schemas.job import JobResponse
from app.schemas.search import CandidateSearchRequest
from app.schemas.search import SavedSearchCreate, SavedSearchResponse
from app.schemas.billing import (
    TransactionResponse,
    PaymentResponse,
    BillingProfileUpdateRequest
)
from app.schemas.employee import EmployeeProfileUpdate
from app.schemas.employer import (
    EmployerDashboardResponse,
    EmployerPersonalProfileResponse,
    EmployerCompanyProfileResponse,
    EmployerProfileUpdate,
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

class EmployeeSearchFilter(BaseModel):
    category: Optional[str] = None
    skills: Optional[List[str]] = []
    city: Optional[str] = None
    max_distance_km: Optional[int] = 10
    min_experience_years: Optional[int] = 0

class RequestPhoneUpdate(BaseModel):
    new_phone: str

class VerifyPhoneUpdate(BaseModel):
    new_phone: str
    otp_code: str

class EmailUpdateRequest(BaseModel):
    email: EmailStr

class VerifyEmailUpdateRequest(BaseModel):
    email: EmailStr
    otp_code: str

    # --- Pagination Variables ---
    page: int = Field(default=1, ge=1, description="Page number (starts at 1)")
    limit: int = Field(default=20, ge=1, le=100, description="Items per page (max 100)")

# --- EMPLOYER DASHBOARD ---
@router.get("/dashboard", response_model=EmployerDashboardResponse)
async def get_employer_dashboard(
    lang: str = Query("en"),
    current_employer: Employer = Depends(get_current_employer)
):
    sub = await SubscriptionService.get_active_subscription(str(current_employer.id))
    
    # 1. Fetch all jobs belonging to this employer
    my_jobs = await Job.find(Job.employer_id == str(current_employer.id)).to_list()
    
    # 2. Calculate Active Jobs
    active_jobs = sum(1 for job in my_jobs if job.status == "published")
    
    # 3. Calculate Total Applicants
    total_applicants = sum(job.applicants_count for job in my_jobs if getattr(job, "applicants_count", 0))

    # 4. Calculate Total Jobs Posted 
    total_jobs_posted = len(my_jobs)
    
    # 5. SHORTLISTED COUNT
    native_job_ids = [job.id for job in my_jobs]
    
    # 1st Attempt: Use the Enum
    shortlisted = await JobApplication.find(
        {"job_id": {"$in": native_job_ids}, "status": ApplicationStatus.SHORTLISTED}
    ).count()
    
    # 2nd Attempt fallback
    if shortlisted == 0:
        shortlisted = await JobApplication.find(
            {"job_id": {"$in": native_job_ids}, "status": "shortlisted"}
        ).count()

    if shortlisted == 0 and native_job_ids:
        all_apps = await JobApplication.find({"job_id": {"$in": native_job_ids}}).to_list()

    # 6. Calculate Total Contacts Unlocked dynamically
    total_contacts = await ContactUnlock.find(
        ContactUnlock.employer_id == current_employer.id
    ).count()

    # 7. Calculate Subscription Days Left
    days_left = max(0, (sub.expiry_date - datetime.utcnow()).days) if sub.expiry_date else 0

    emp_dict = current_employer.model_dump()
    translated_emp = apply_translations(emp_dict, getattr(current_employer, "translations", {}), lang)
    
    return EmployerDashboardResponse(
        company_name=translated_emp.get("company_name", current_employer.name),
        subscription_tier=sub.plan_type.lower(),        
        is_active=sub.is_active,
        days_left=days_left,
        expiry_date=sub.expiry_date,
        active_jobs_count=total_jobs_posted,        
        total_applicants_count=total_applicants,
        shortlisted_count=shortlisted,
        job_posts_used=total_jobs_posted, 
        contacts_viewed=total_contacts 
    )

# --- GET PERSONAL PROFILE ---
@router.get("/profile/personal", response_model=EmployerPersonalProfileResponse)
async def get_personal_profile(
    lang: str = Query("en"),
    current_employer: Employer = Depends(get_current_employer)
):
    emp_dict = current_employer.model_dump()
    return apply_translations(emp_dict, getattr(current_employer, "translations", {}), lang)

# --- GET COMPANY PROFILE ---
@router.get("/profile/company", response_model=EmployerCompanyProfileResponse)
async def get_company_profile(
    lang: str = Query("en"),
    current_employer: Employer = Depends(get_current_employer)
):
    emp_dict = current_employer.model_dump()
    return apply_translations(emp_dict, getattr(current_employer, "translations", {}), lang)

# --- UPDATE EMPLOYER PROFILE ---
@router.patch("/profile_update", response_model=dict, status_code=status.HTTP_200_OK)
async def update_employer_profile(
    profile_data: EmployerProfileUpdate,
    background_tasks: BackgroundTasks,
    current_employer: Employer = Depends(get_current_employer)
):
    update_dict = profile_data.model_dump(exclude_unset=True)
    
    if not update_dict:
        return {
            "status": "success", 
            "message": "No changes were provided.",
            "updated_fields": []
        }

    # GSTIN VERIFICATION LOGIC
    if "gstin" in update_dict:
        if update_dict["gstin"] != getattr(current_employer, "gstin", None):
            current_employer.gstin_verified = False

    # OLA MAPS AUTO-GEOCODING
    address_field = update_dict.get("company_address") or update_dict.get("address")
    if address_field:
        try:
            coords = await MapService.get_coordinates(address_field)
            if coords:
                update_dict["address"] = coords.get("formatted_address", address_field)
                current_employer.location = GeoLocation(
                    type="Point",
                    coordinates=[coords["longitude"], coords["latitude"]],
                    city=coords.get("city")
                )
        except Exception as e:
            print(f"MapService Error: {e}")
            update_dict["address"] = address_field 
            
        if "company_address" in update_dict:
            del update_dict["company_address"]

    # DIRECT FIELD MAPPING & SAVE
    for field, value in update_dict.items():
        if hasattr(current_employer, field) and field != "location":
            setattr(current_employer, field, value)
            
    await current_employer.save()

    # SUBSCRIPTIONS & BACKGROUND TASKS
    async def setup_new_employer_if_needed(emp_id: str, email: str, name: str):
        existing_sub = await Subscription.find_one({"employer_id": emp_id})
        if not existing_sub:
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

    background_tasks.add_task(
        setup_new_employer_if_needed, 
        str(current_employer.id), 
        getattr(current_employer, "email", None), 
        getattr(current_employer, "name", "Employer")
    )

    # TRANSLATION LOGIC BLOCK FOR EMPLOYER PROFILE
    translatable_db_fields = [
        "name",
        "gender",
        "company_name",
        "company_type",
        "industry",
        "description",
        "company_address",
        "address"
    ]
    
    fields_to_translate = [field for field in update_dict.keys() if field in translatable_db_fields]
    
    if fields_to_translate:
        background_tasks.add_task(
            translate_document_fields,
            str(current_employer.id), 
            Employer,
            fields_to_translate,
            "hi"
        )

    return {
        "status": "success",
        "message": "Profile updated successfully.",
        "updated_fields": list(update_dict.keys()),
        "personal_profile": EmployerPersonalProfileResponse.model_validate(current_employer).model_dump(),
        "company_profile": EmployerCompanyProfileResponse.model_validate(current_employer).model_dump()
    }

# --- UPLOAD PROFILE PICTURE / LOGO ---
@router.post("/profile/upload_logo")
async def upload_company_logo(
    file: UploadFile = File(...),
    current_employer = Depends(get_current_employer)
):
    allowed_content_types = ["image/jpeg", "image/png", "image/webp"]
    if file.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid file type. Please upload a JPEG, PNG, or WEBP image."
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
        uploaded_url = await upload_file(file, folder_name="employer_logos")
        current_employer.logo_url = uploaded_url
        await current_employer.save()
        
        return {
            "message": "Logo uploaded successfully!",
            "logo_url": current_employer.logo_url
        }
        
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Profile Photo/Logo Deletion ---
@router.delete("/profile_delete_logo", status_code=status.HTTP_200_OK)
async def delete_employer_profile_picture(
    current_employer = Depends(get_current_employer)
):
    if not current_employer.profile_picture_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="You do not have a profile picture to delete."
        )

    deletion_successful = delete_file(current_employer.profile_picture_url)
    
    if not deletion_successful:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete the image from the cloud provider."
        )

    current_employer.profile_picture_url = None
    await current_employer.save()

    return {"message": "Profile picture deleted successfully."}

# --- Mobile Number Change ---
@router.post("/profile/send_phone_no_update_otp", response_model=dict)
async def request_phone_update_otp(
    data: RequestPhoneUpdate,
    current_employer: Employer = Depends(get_current_employer)
):
    clean_new_phone = data.new_phone[-10:]

    if current_employer.phone == clean_new_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="This is already your current phone number."
        )

    # Cross-collection uniqueness check including Admin
    phone_taken = (
        await Employer.find_one({"phone": clean_new_phone}) or 
        await Employee.find_one({"phone": clean_new_phone}) or
        await Admin.find_one({"phone": clean_new_phone})
    )
    if phone_taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="This phone number is already registered to another account."
        )

    DEV_MODE = True
    otp_code = "1234" if DEV_MODE else str(py_random.randint(1000, 9999))

    otp_record = await OTP.find_one({"phone": clean_new_phone})
    if otp_record:
        otp_record.code = otp_code
        await otp_record.save()
    else:
        await OTP(
            phone=clean_new_phone,
            code=otp_code,
            user_type="employer"
        ).insert()

    return {
        "status": "success",
        "message": f"An OTP has been sent to {clean_new_phone}. Please verify to update your phone number."
    }

@router.patch("/profile/phone_no_update", status_code=status.HTTP_200_OK)
async def update_employer_phone(
    data: VerifyPhoneUpdate,
    current_employer: Employer = Depends(get_current_employer)
):
    clean_new_phone = data.new_phone[-10:]

    if current_employer.phone == clean_new_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="This is already your current phone number."
        )

    otp_record = await OTP.find_one({"phone": clean_new_phone})
    
    if not otp_record or not otp_record.code or otp_record.code != data.otp_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid or expired OTP for the new phone number."
        )

    # Double-check database lock at the time of verification including Admin
    phone_taken = (
        await Employer.find_one({"phone": clean_new_phone}) or 
        await Employee.find_one({"phone": clean_new_phone}) or
        await Admin.find_one({"phone": clean_new_phone})
    )
    if phone_taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="This phone number was just registered by another account."
        )

    otp_record.code = None
    await otp_record.save()

    current_employer.phone = clean_new_phone
    if hasattr(current_employer, "updated_at"):
        current_employer.updated_at = datetime.now(timezone.utc)
        
    await current_employer.save()

    return {
        "status": "success", 
        "message": "Phone number successfully updated.", 
        "new_phone": current_employer.phone
    }

# --- EMAIL UPDATE & VERIFICATION FLOW ---
@router.post("/profile/email/send_otp", response_model=dict)
async def request_email_update_otp(
    data: EmailUpdateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    clean_email = data.email.lower().strip()

    # Prevent sending OTP if it's already their current email
    if current_employer.email and current_employer.email.lower() == clean_email:
        raise HTTPException(status_code=400, detail="This is already your current email address.")

    # Cross-collection uniqueness check including Admin
    existing_user = (
        await Employer.find_one({"email": clean_email}) or 
        await Employee.find_one({"email": clean_email}) or
        await Admin.find_one({"email": clean_email})
    )
    if existing_user:
        raise HTTPException(status_code=409, detail="This email is already associated with another account.")

    DEV_MODE = True
    otp_code = "1234" if DEV_MODE else str(py_random.randint(1000, 9999))

    await OTP(
        phone=clean_email,
        code=otp_code, 
        user_type="employer"
    ).insert()
    
    print(f"EMAIL OTP FOR {clean_email} IS: {otp_code}")

    return {
        "status": "success",
        "message": f"An OTP has been sent to {clean_email}. Please verify to update your email."
    }

@router.patch("/profile/email/verify_and_update", response_model=dict)
async def verify_and_update_email(
    data: VerifyEmailUpdateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    clean_email = data.email.lower().strip()

    DEV_MODE = True
    MASTER_OTP = "1234"

    if DEV_MODE and data.otp_code == MASTER_OTP:
        print(f"⚠️ DEV BYPASS USED: Email updated to {clean_email}")
    else:
        otp_record = await OTP.find_one(OTP.phone == clean_email)
        if not otp_record or otp_record.code != data.otp_code:
            raise HTTPException(status_code=400, detail="Invalid or expired OTP.")
        await otp_record.delete()

    # Double-check database lock at the time of verification including Admin
    email_taken = (
        await Employer.find_one({"email": clean_email}) or 
        await Employee.find_one({"email": clean_email}) or
        await Admin.find_one({"email": clean_email})
    )
    if email_taken:
        raise HTTPException(status_code=409, detail="This email was just registered by another account.")

    current_employer.email = clean_email
    current_employer.email_verified = True 
    await current_employer.save()

    return {
        "status": "success",
        "message": "Email successfully updated and verified!",
        "updated_data": {
            "email": current_employer.email,
            "email_verified": current_employer.email_verified
        }
    }

# --- KYC SUBMISSION ---
@router.post("/kyc/submit")
async def submit_kyc(data: KYCSubmitRequest, current_user_id: str = Depends(get_current_employer)):
    employer = await Employer.get(current_user_id)
    if not employer:
        raise HTTPException(status_code=404, detail="Employer not found")

    employer.kyc_documents = data.model_dump()
    is_verified, remarks = await KYCService.automated_verify(employer.kyc_documents)

    if is_verified:
        employer.kyc_status = KYCStatus.VERIFIED
        employer.verified_by = VerificationSource.SYSTEM
        employer.verified_at = datetime.now(timezone.utc)
        employer.kyc_remarks = remarks
        employer.is_verified = True 
    else:
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
@router.get("/my_jobs", response_model=List[dict])
async def get_my_jobs(
    lang: str = Query("en"),
    current_employer: Employer = Depends(get_current_employer)
):
    employer_id_str = str(current_employer.id)
    
    my_jobs = await Job.find({
        "$or": [
            {"employer_id": employer_id_str},
            {"employer_id": ObjectId(employer_id_str)}
        ]
    }).sort("-created_at").to_list()

    if not my_jobs:
        return []

    job_ids_str = [str(job.id) for job in my_jobs]
    job_ids_obj = [job.id for job in my_jobs]

    all_applications = await JobApplication.find({
        "$or": [
            {"job_id": {"$in": job_ids_str}},
            {"job_id": {"$in": job_ids_obj}}
        ]
    }).to_list()

    stats = {jid: {"applicants": 0, "shortlisted": 0, "hires": 0} for jid in job_ids_str}

    for app in all_applications:
        jid = str(app.job_id)
        if jid in stats:
            stats[jid]["applicants"] += 1
            status_str = str(getattr(app, "status", "")).lower()
            if "shortlisted" in status_str:
                stats[jid]["shortlisted"] += 1
            elif "hired" in status_str:
                stats[jid]["hires"] += 1

    response_list = []
    for job in my_jobs:
        job_dict = job.model_dump() if hasattr(job, "model_dump") else dict(job)
        
        # APPLY TRANSLATIONS TO THE JOB!
        job_dict = apply_translations(job_dict, getattr(job, "translations", {}), lang)
        
        jid = str(job.id)
        job_dict["applicants_count"] = stats[jid]["applicants"]
        job_dict["shortlisted_count"] = stats[jid]["shortlisted"]
        job_dict["hires_count"] = stats[jid]["hires"]
        job_dict["views_count"] = getattr(job, "views_count", 0) or 0
        
        response_list.append(job_dict)

    return response_list

# --- CORE RECRUITMENT FLOW (ATS) ---
@router.get("/jobs/{job_id}/applicants", response_model=List[dict])
async def list_job_applicants(
    job_id: str, 
    lang: str = Query("en"),
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
        
        # Extract and translate employee data safely
        emp_category = "N/A"
        emp_name = "Deleted Employee"
        
        if employee:
            emp_dict = employee.model_dump()
            translated_emp = apply_translations(emp_dict, getattr(employee, "translations", {}), lang)
            emp_name = translated_emp.get("name", employee.name)
            emp_category = translated_emp.get("job_category", getattr(employee, "job_category", "N/A"))
        
        results.append({
            "application_id": str(app.id),
            "employee_id": str(app.employee_id),
            "employee_name": emp_name,
            "employee_category": emp_category,
            "employee_email": employee.email if employee else "N/A",
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

    status_str = request.new_status.value if hasattr(request.new_status, 'value') else request.new_status
    job_closed = False

    if status_str.lower() == "hired":
        job.is_active = False
        if hasattr(job, "status"):
            job.status = "closed"
        await job.save()
        job_closed = True

    job_title = job.job_title if job else "a recent job" 
       
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
    query = {"is_looking_for_job": True}
    
    if filters.category:
        query["category"] = filters.category
    if filters.skills:
        query["skills"] = {"$in": filters.skills} 
    if filters.city:
        query["location_name"] = filters.city
        
    total_matches = await Employee.find(query).count()
    skip_count = (filters.page - 1) * filters.limit
    matching_employees = await Employee.find(query).skip(skip_count).limit(filters.limit).to_list()
    total_pages = math.ceil(total_matches / filters.limit) if total_matches > 0 else 1
    
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
    lang: str = Query("en"),
    current_employer: Employer = Depends(get_current_employer)
):
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
    new_saved_search = SavedSearch(
        employer_id=str(current_employer.id),
        title=save_data.title,
        filters=save_data.filters.model_dump(exclude_unset=True)
    )
    await new_saved_search.insert()
    
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
    if profile_data.gstin is not None:
        current_employer.gstin = profile_data.gstin
    if profile_data.billing_address is not None:
        current_employer.billing_address = profile_data.billing_address
        
    await current_employer.save()
    return {"message": "Billing profile updated successfully", "gstin": current_employer.gstin}

# --- CREDITS & USAGE (Virtual Coins Ledger) ---
@router.get("/credits/transactions", response_model=List[TransactionResponse])
async def get_credit_transactions(
    lang: str = Query("en"),
    tab_filter: Optional[str] = None, 
    current_employer: Employer = Depends(get_current_employer)
):
    query = {"employer_id": str(current_employer.id)}
    
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
        txn_dict["amount"] = f"+ {txn.amount}" if txn.amount > 0 else f"- {abs(txn.amount)}"
        response.append(txn_dict)
        
    return response

# --- BILLING HISTORY (Real Money Purchases) ---
@router.get("/billing/history", response_model=List[PaymentResponse])
async def get_billing_history(
    lang: str = Query("en"),
    status_filter: Optional[str] = None, 
    current_employer: Employer = Depends(get_current_employer)
):
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
    lang: str = Query("en"),
    current_employer: Employer = Depends(get_current_employer)
):
    current_code = getattr(current_employer, "referral_code", None)

    if not current_code:
        new_code = generate_referral_code()
        await current_employer.update({"$set": {"referral_code": new_code}})
        current_code = new_code

    total_referred = await Employer.find(
        {"referred_by_code": current_code}
    ).count()
    
    referral_transactions = await Transaction.find(
        {"employer_id": str(current_employer.id), "title": "Referral Bonus"}
    ).to_list()
    
    total_coins = sum(txn.amount for txn in referral_transactions)
    
    return {
        "referral_code": current_code,
        "total_referred": total_referred,
        "total_coins_earned": total_coins
    }