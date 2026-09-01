import asyncio
from fastapi import APIRouter, Depends, HTTPException, status, Body
from bson import ObjectId
from beanie import PydanticObjectId
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, EmailStr, Field

# --- Dependency Imports ---
from app.api.dependencies import get_current_admin

# --- Services Imports ---
from app.services.notification import NotificationService
from app.services.admin import AdminService
from app.services.reports import ReportService 
from app.services.audit import AuditService

# --- Model Imports ---
from app.models.admin import Admin  
from app.models.job import Job
from app.models.announcement import Announcement
from app.models.subscriptions import Subscription
from app.models.category import Category
from app.models.webhook import WebhookSubscription
from app.models.promo import PromoCode
from app.models.payment import Payment 
from app.models.audit import AuditLog
from app.models.notification import Notification, NotificationType
from app.models.employee import Employee
from app.models.employer import (
    Employer,
    EmployerType,
    SubscriptionTier,
    VerificationSource,
    KYCStatus
)

# --- Schema Imports ---
from app.schemas.admin import AdminDashboardStats
from app.schemas.employer import AdminKYCStatusUpdate
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

class SubscriptionPlanCreate(BaseModel):
    name: str
    price: float
    duration_days: int
    features: List[str]

class DiscountApply(BaseModel):
    plan_id: str
    discount_percentage: float

class EmployeeApproval(BaseModel):
    approve: bool
    admin_notes: Optional[str] = None

class CategoryCreate(BaseModel):
    name: str
    description: Optional[str] = None

class AnnouncementCreate(BaseModel):
    title: str
    message: str
    send_sms: bool = False

class ManualSubscriptionAssign(BaseModel):
    plan_type: str
    duration_days: int

class PromoCodeCreate(BaseModel):
    code: str = Field(..., description="e.g., DIWALI50")
    discount_percentage: float = Field(..., ge=1, le=100)
    valid_until: datetime
    max_uses: Optional[int] = None

class PlanDiscountUpdate(BaseModel):
    discount_percentage: float = Field(..., ge=0, le=100)


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
        Employer.gstin_verified == False
    ).count()

    return AdminDashboardStats(
        total_employers=employer_count,
        total_employees=employee_count,
        active_jobs=active_jobs,
        pending_verifications=pending_v,
        revenue_stats={
            "Total Revenue": float(total_revenue),
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
# Employee MANAGEMENT
# =====================================================================

@router.get("/employees/pending", summary="View employee registrations")
async def get_pending_employees(current_admin = Depends(get_current_admin)):
    """Fetches all employees whose status is pending approval."""
    employees = await Employee.find({"status": "pending"}).to_list()
    return [w.model_dump() for w in employees]

@router.patch("/employees/{employee_id}/approval", summary="Approve or reject profiles")
async def approve_reject_employees(
    employee_id: str,
    data: EmployeeApproval,
    current_admin = Depends(get_current_admin)
):
    employee = await Employee.get(PydanticObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
        
    employee.status = "active" if data.approve else "rejected"
    if data.admin_notes:
        employee.admin_notes = data.admin_notes
        
    await employee.save()
    return {"message": f"Employee successfully {'approved' if data.approve else 'rejected'}"}


# =====================================================================
# KYC MANAGEMENT
# =====================================================================

@router.patch("/kyc/{employer_id}/status")
async def admin_update_kyc_status(
    employer_id: str, 
    data: AdminKYCStatusUpdate,
    admin_user = Depends(get_current_admin)
):
    employer = await Employer.get(employer_id)
    if not employer:
        raise HTTPException(status_code=404, detail="Employer not found")

    # Apply the Admin's absolute decision
    employer.kyc_status = data.status
    employer.kyc_remarks = data.remarks
    employer.verified_by = VerificationSource.ADMIN
    employer.verified_at = datetime.now(timezone.utc)
    
    # Sync with global boolean flag
    employer.is_verified = (data.status == KYCStatus.VERIFIED)

    # Save the update to the database first
    await employer.save()

    # Fire the notification to the employer
    await NotificationService.notify_user(
        employer_id=employer_id,
        title="KYC Status Updated",
        message=f"Your KYC document has been {data.status.value if hasattr(data.status, 'value') else data.status} by our team.",
        notif_type=NotificationType.KYC_UPDATE
    )

    return {
        "status": "success",
        "message": f"Employer KYC status forcefully updated to {data.status} by Admin.",
        "employer_id": employer_id
    }

@router.get("/kyc/pending")
async def get_pending_kyc_applications(admin_user = Depends(get_current_admin)):
    """Fetches all employers who failed auto-verify and need manual review."""
    pending_employers = await Employer.find({"kyc_status": KYCStatus.PENDING}).to_list()
    return pending_employers


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

    employer.gstin_verified = True
    await employer.save()

    await AuditService.log_action(
        admin=admin,
        action="VERIFY_GST",
        target_id=employer_id,
        target_type="employer",
        details=f"Verified GST for {employer.company_name}"
    )
    
    return {"message": f"Employer {employer.company_name} verified successfully."}


# =====================================================================
# SUBSCRIPTION MANAGEMENT
# =====================================================================

@router.post("/subscriptions/plans", summary="Create or configure plans")
async def create_subscription_plan(
    plan_data: SubscriptionPlanCreate,
    current_admin: Admin = Depends(get_current_admin)
):
    """Creates a new pricing plan for employers."""
    existing_plan = await Subscription.find_one({"name": plan_data.name})
    if existing_plan:
        raise HTTPException(status_code=400, detail="A plan with this name already exists.")

    new_plan = Subscription(**plan_data.model_dump())
    await new_plan.insert()
    return {"message": f"Subscription Plan '{plan_data.name}' created successfully."}

@router.patch("/employers/{employer_id}/subscription", summary="Manually assign subscription")
async def assign_subscription_manually(
    employer_id: str,
    data: ManualSubscriptionAssign,
    current_admin: Admin = Depends(get_current_admin)
):
    """Allows admin to manually upgrade an employer's subscription (e.g., for offline payments)."""
    employer = await Employer.get(PydanticObjectId(employer_id))
    if not employer:
        raise HTTPException(status_code=404, detail="Employer not found")
        
    employer.subscription_status = data.plan_type
    employer.subscription_end_date = datetime.now(timezone.utc) + timedelta(days=data.duration_days)
    await employer.save()
    
    await AuditService.log_action(
        admin=current_admin,
        action="MANUAL_SUBSCRIPTION",
        target_id=employer_id,
        target_type="employer",
        details=f"Manually assigned {data.plan_type} plan for {data.duration_days} days."
    )
    
    return {"message": f"Successfully assigned {data.plan_type} plan to {employer.company_name or employer.name}."}


# ==========================================
# DISCOUNTS & PROMOTIONAL CAMPAIGNS
# ==========================================

@router.post("/subscriptions/promo-codes", summary="Create promotional campaigns")
async def create_promotional_campaign(
    data: PromoCodeCreate,
    current_admin: Admin = Depends(get_current_admin)
):
    """Creates a new promo code for employers to use during checkout."""
    existing_code = await PromoCode.find_one({"code": data.code.upper()})
    if existing_code:
        raise HTTPException(status_code=400, detail="This promo code already exists.")
        
    new_promo = PromoCode(
        code=data.code.upper(),
        discount_percentage=data.discount_percentage,
        valid_until=data.valid_until,
        max_uses=data.max_uses,
        created_by=current_admin.name
    )
    await new_promo.insert()
    
    return {"message": f"Promo campaign '{new_promo.code}' successfully created."}

@router.patch("/subscriptions/plans/{plan_id}/discount", summary="Apply discounts to plans")
async def apply_plan_discount(
    plan_id: str,
    data: PlanDiscountUpdate,
    current_admin: Admin = Depends(get_current_admin)
):
    """Directly applies a base discount to an existing subscription plan."""
    try:
        plan = await Subscription.get(ObjectId(plan_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Plan ID format")
        
    if not plan:
        raise HTTPException(status_code=404, detail="Subscription plan not found")
        
    # Assuming your Subscription model has an active_discount field
    plan.active_discount = data.discount_percentage
    await plan.save()
    
    await AuditService.log_action(
        admin=current_admin,
        action="APPLY_DISCOUNT",
        target_id=plan_id,
        target_type="subscription_plan",
        details=f"Applied {data.discount_percentage}% discount to plan {plan.name}."
    )
    
    return {"message": f"Successfully updated discount for {plan.name} to {data.discount_percentage}%."}


# =====================================================================
# SYSTEM CONFIGURATION & COMMUNICATION
# =====================================================================

@router.post("/announcements", summary="Publish home feed announcements")
async def create_announcement(
    data: AnnouncementCreate,
    current_admin: Admin = Depends(get_current_admin)
):
    """Posts an announcement to the Home Feed and optionally triggers an SMS blast."""
    new_announcement = Announcement(
        title=data.title,
        message=data.message,
        send_sms=data.send_sms,
        created_by=current_admin.name
    )
    await new_announcement.insert()
    
    if data.send_sms:
        # Trigger your SMS Gateway (Twilio/MSG91/AWS SNS) background task here
        # await SmsService.broadcast_to_all_employees(data.message)
        pass
        
    return {"message": "Announcement published to Home Feed!"}

@router.post("/categories", summary="Configure job categories")
async def add_new_category(
    category_data: CategoryCreate,
    current_admin: Admin = Depends(get_current_admin)
):
    """Dynamically adds a new job category to the platform."""
    existing_cat = await Category.find_one({"name": category_data.name})
    if existing_cat:
        raise HTTPException(status_code=400, detail="Category already exists.")
        
    new_cat = Category(
        name=category_data.name, 
        description=category_data.description
    )
    await new_cat.insert()
    return {"message": f"Category '{category_data.name}' added to the system."}


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
        Employer.gstin_verified == False
    ).to_list()
    return [{
        "id": str(e.id),
        "company_name": e.company_name,
        "gst_number": getattr(e, "gstin", None),
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
    
    # Convert the returned Pydantic models into standard dictionaries
    return [log.model_dump() for log in logs]

@router.get("/employees", summary="View all employees")
async def get_all_employees(
    limit: int = 50, 
    skip: int = 0, 
    current_admin: Admin = Depends(get_current_admin)
):
    """Fetches a paginated list of all registered employees."""
    employees = await Employee.find_all().skip(skip).limit(limit).to_list()
    return [e.model_dump() for e in employees]

@router.get("/employers", summary="View all employers")
async def get_all_employers(
    limit: int = 50, 
    skip: int = 0, 
    current_admin: Admin = Depends(get_current_admin)
):
    """Fetches a paginated list of all registered employers."""
    employers = await Employer.find_all().skip(skip).limit(limit).to_list()
    return [e.model_dump() for e in employers]