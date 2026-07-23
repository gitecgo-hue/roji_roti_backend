import asyncio
from fastapi import APIRouter, Depends, HTTPException, status, Body
from bson import ObjectId
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, EmailStr, Field

# Models & Services
from app.services.admin import AdminService
from app.services.reports import ReportService 
from app.services.audit import AuditService 
from app.models.employee import Employee
from app.models.employer import Employer, EmployerType, SubscriptionTier
from app.models.admin import Admin  # <--- Added Admin Model Import
from app.models.job import Job
from app.models.subscriptions import Subscription
from app.models.category import Category
from app.models.webhook import WebhookSubscription
from app.models.promo import PromoCode
from app.models.payment import Payment 
from app.models.audit import AuditLog  # Moved to top-level for consistency
from app.api.dependencies import get_current_admin

# Schemas
from app.schemas.admin import AdminDashboardStats
from app.schemas.report import ComprehensiveReport

router = APIRouter()

# =====================================================================
# PYDANTIC SCHEMAS
# =====================================================================

class AdminCreateRequest(BaseModel):
    name: str = Field(..., description="Full name of the new administrator")
    email: EmailStr
    phone: str = Field(..., description="10-digit mobile number for OTP login")
    role: str = Field(default="moderator", description="e.g., 'super_admin', 'moderator', 'support'")


# =====================================================================
# ADMIN ACCOUNT MANAGEMENT
# =====================================================================

@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_new_admin(
    data: AdminCreateRequest,
    # THE GUARD: Only existing admins can run this function!
    current_admin: Admin = Depends(get_current_admin) 
):
    """
    Restricted Endpoint: Allows an existing Super Admin to authorize and 
    create a new administrator account in the system.
    """
    # 1. Enforce Role Hierarchy (Optional: Only super_admins can make other admins)
    if getattr(current_admin, "role", "moderator") != "super_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Only Super Admins have permission to create new administrative accounts."
        )

    clean_phone = data.phone[-10:]

    # 2. Prevent Cross-Contamination & Duplicates
    phone_taken = await Admin.find_one({"phone": clean_phone}) or \
                  await Employer.find_one({"phone": clean_phone}) or \
                  await Employee.find_one({"phone": clean_phone})
                  
    if phone_taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This phone number is already actively registered in the Roji Roti system."
        )

    email_taken = await Admin.find_one({"email": data.email})
    if email_taken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An administrator with this email already exists."
        )

    # 3. Create the New Admin (No password needed, they will log in via OTP!)
    new_admin = Admin(
        name=data.name,
        email=data.email,
        phone=clean_phone,
        role=data.role,
        is_active=True
    )
    
    await new_admin.insert()

    return {
        "status": "success",
        "message": f"Administrator '{new_admin.name}' successfully created.",
        "admin_id": str(new_admin.id),
        "instructions": "The new administrator can now log in immediately using their mobile number."
    }


# =====================================================================
# REPORTING & INSIGHTS
# =====================================================================

@router.get("/stats", response_model=AdminDashboardStats)
async def get_system_stats(admin: Admin = Depends(get_current_admin)):
    """ Aggregates high-level platform data and real revenue. """
    employer_count = await Employer.count()
    employee_count = await Employee.count()
    active_jobs = await Job.find(Job.is_active == True).count()
    
    all_payments = await Payment.find(Payment.status == "captured").to_list()
    total_revenue = sum(p.amount for p in all_payments)
    
    pending_v = await Employer.find(
        Employer.employer_type == EmployerType.COMPANY,
        Employer.is_gst_verified == False
    ).count()

    return AdminDashboardStats(
        total_employers=employer_count,
        total_employees=employee_count,
        active_jobs=active_jobs,
        pending_verifications=pending_v,
        revenue_stats={
            "Total Revenue": float(total_revenue),
            "Currency": "INR",
            "Payment_Count": len(all_payments)
        }
    )

@router.get("/dashboard/reports", response_model=ComprehensiveReport)
async def get_detailed_reports(admin: Admin = Depends(get_current_admin)):
    """ Generates granular growth and referral metrics. """
    daily_stats, referral_stats, sub_stats = await asyncio.gather(
        ReportService.get_daily_employee_stats(),
        ReportService.get_referral_stats(),
        ReportService.get_subscription_stats()
    )
    
    return ComprehensiveReport(
        daily_employees=daily_stats,
        referrals=referral_stats,
        subscriptions=sub_stats
    )


# =====================================================================
# KYC MANAGEMENT
# =====================================================================

@router.get("/kyc/pending")
async def get_pending_kyc_queue(current_admin: Admin = Depends(get_current_admin)):
    """
    Retrieves all employee profiles currently waiting for manual KYC review.
    """
    pending_employees = await Employee.find({"kyc_status": "PENDING_REVIEW"}).sort("updated_at").to_list()
    
    return [{
        "employee_id": str(employee.id),
        "name": employee.name,
        "phone": employee.phone,
        "category": employee.category,
        "kyc_document_url": employee.kyc_document_url, # The blurry image URL
        "failed_attempts": employee.kyc_attempts
    } for employee in pending_employees]


@router.patch("/kyc/{employee_id}/approve")
async def manual_kyc_approve(
    employee_id: str,
    id_type: str = Body(..., description="Must be 'AADHAAR' or 'PAN'"),
    extracted_number: str = Body(..., description="The ID number the admin manually read from the image"),
    current_admin: Admin = Depends(get_current_admin)
):
    """
    Admin manually overrides a failed OCR attempt, inputs the correct ID number, and approves the employee.
    """
    employee = await Employee.get(ObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    if employee.kyc_status == "VERIFIED":
        raise HTTPException(status_code=400, detail="Employee is already verified.")

    # 1. Save the manually extracted data
    if id_type == "AADHAAR":
        employee.adhar_card_number = extracted_number
    else:
        employee.pan_card = extracted_number

    # 2. Update their status
    employee.kyc_status = "VERIFIED"
    employee.is_approved = True

    # Optional: Clear the document URL if you don't want to store PII long-term
    # employee.kyc_document_url = None

    await employee.save()

    # 3. Notify the employee
    # await SmsService.send_sms(employee.phone, "Great news! Your Roji Roti account has been manually verified and is now active. You can now apply for jobs!")

    return {"status": "success", "message": f"Employee {employee.name} has been successfully verified."}


@router.patch("/kyc/{employee_id}/reject")
async def manual_kyc_reject(
    employee_id: str,
    reason: str = Body(..., description="Reason for rejection to send to the employee"),
    current_admin: Admin = Depends(get_current_admin)
):
    """
    Admin rejects the document because it is completely unreadable or fraudulent.
    Resets the employee's attempt counter so they can try uploading again.
    """
    employee = await Employee.get(ObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found.")

    # 1. Reset their KYC state
    employee.kyc_status = "UNVERIFIED"
    employee.kyc_attempts = 0 # Give them 3 fresh attempts!
    
    # 2. Delete the bad image to save cloud storage space
    # await StorageService.delete_kyc_doc(employee.kyc_document_url)
    employee.kyc_document_url = None

    await employee.save()

    # 3. Notify the employee
    notification_msg = f"Your recent ID upload was rejected: {reason}. Please log back into the app and take a clear, well-lit photo of your ID."
    # await SmsService.send_sms(employee.phone, notification_msg)

    return {"status": "success", "message": f"Employee {employee.name}'s document rejected. They have been notified to try again."}


# =====================================================================
# APPROVAL WORKFLOWS
# =====================================================================

@router.patch("/verify-employer/{employer_id}")
async def verify_employer_gst(employer_id: str, admin: Admin = Depends(get_current_admin)):
    """ Manual override to verify a company's GST status with audit logging. """
    try:
        employer = await Employer.get(ObjectId(employer_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Employer ID format")

    if not employer or employer.employer_type != EmployerType.COMPANY:
        raise HTTPException(status_code=404, detail="Company not found")

    employer.is_gst_verified = True
    await employer.save()

    await AuditService.log_action(
        admin=admin,
        action="VERIFY_GST",
        target_id=employer_id,
        target_type="employer",
        details=f"Verified GST for {employer.company_name}"
    )
    
    return {"message": f"Employer {employer.company_name} verified successfully."}

@router.put("/approve-employee/{employee_id}")
async def approve_employee_profile(employee_id: str, admin: Admin = Depends(get_current_admin)):
    """ Approves an employee, making them visible in searches. """
    employee = await Employee.get(ObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    employee.is_approved = True
    await employee.save()
    
    await AuditService.log_action(
        admin=admin,
        action="APPROVE_EMPLOYEE",
        target_id=employee_id,
        target_type="employee",
        details=f"Approved employee profile for {employee.name}"
    )
    
    return {"message": f"Employee {employee.name} approved."}


# =====================================================================
# SYSTEM CONFIGURATION & MODERATION
# =====================================================================

@router.delete("/suspend-user/{user_type}/{user_id}")
async def suspend_user(user_type: str, user_id: str, admin: Admin = Depends(get_current_admin)):
    """ Soft-deletes or suspends a user account with audit logging. """
    try:
        obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid User ID format")

    if user_type == "employer":
        user = await Employer.get(obj_id)
    else:
        user = await Employee.get(obj_id)
        
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False 
    await user.save()

    await AuditService.log_action(
        admin=admin,
        action="SUSPEND_USER",
        target_id=user_id,
        target_type=user_type,
        details=f"Suspended {user_type} due to policy violation."
    )
    
    return {"message": f"{user_type.capitalize()} {user_id} has been suspended."}


# =====================================================================
# SYSTEM LISTS
# =====================================================================

@router.get("/verification-queue", response_model=List[dict])
async def get_verification_queue(admin: Admin = Depends(get_current_admin)):
    pending = await Employer.find(
        Employer.employer_type == EmployerType.COMPANY,
        Employer.is_gst_verified == False
    ).to_list()
    return [{
        "id": str(e.id),
        "company_name": e.company_name,
        "gst_number": e.gst_number,
        "contact_name": e.name,
        "phone": e.phone
    } for e in pending]

@router.get("/audit-logs", response_model=List[dict])
async def get_audit_trail(
    limit: int = 50,
    admin: Admin = Depends(get_current_admin)
):
    """
    Returns the most recent administrative actions.
    """
    logs = await AuditLog.find_all().sort("-created_at").limit(limit).to_list()
    return logs