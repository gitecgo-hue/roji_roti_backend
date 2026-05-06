import asyncio
from fastapi import APIRouter, Depends, HTTPException, status
from bson import ObjectId
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

# Models & Services
from app.services.admin import AdminService
from app.services.reports import ReportService 
from app.services.audit import AuditService 
from app.models.employee import Employee
from app.models.employer import Employer, EmployerType, SubscriptionTier
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

# --- Reporting & Insights ---

@router.get("/stats", response_model=AdminDashboardStats)
async def get_system_stats(admin: Employer = Depends(get_current_admin)):
    """ Aggregates high-level platform data and real revenue. """
    employer_count = await Employer.count()
    worker_count = await Employee.count()
    active_jobs = await Job.find(Job.is_active == True).count()
    
    all_payments = await Payment.find(Payment.status == "captured").to_list()
    total_revenue = sum(p.amount for p in all_payments)
    
    pending_v = await Employer.find(
        Employer.employer_type == EmployerType.COMPANY,
        Employer.is_gst_verified == False
    ).count()

    return AdminDashboardStats(
        total_employers=employer_count,
        total_workers=worker_count,
        active_jobs=active_jobs,
        pending_verifications=pending_v,
        revenue_stats={
            "Total Revenue": float(total_revenue),
            "Currency": "INR",
            "Payment_Count": len(all_payments)
        }
    )

@router.get("/dashboard/reports", response_model=ComprehensiveReport)
async def get_detailed_reports(admin=Depends(get_current_admin)):
    """ Generates granular growth and referral metrics. """
    daily_stats, referral_stats, sub_stats = await asyncio.gather(
        ReportService.get_daily_worker_stats(),
        ReportService.get_referral_stats(),
        ReportService.get_subscription_stats()
    )
    
    return ComprehensiveReport(
        daily_workers=daily_stats,
        referrals=referral_stats,
        subscriptions=sub_stats
    )

# --- Approval Workflows ---

@router.patch("/verify-employer/{employer_id}")
async def verify_employer_gst(employer_id: str, admin: Employer = Depends(get_current_admin)):
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

@router.put("/approve-worker/{worker_id}")
async def approve_worker_profile(worker_id: str, admin: Employer = Depends(get_current_admin)):
    """ Approves a worker, making them visible in searches. """
    worker = await Employee.get(ObjectId(worker_id))
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    worker.is_approved = True
    await worker.save()
    
    await AuditService.log_action(
        admin=admin,
        action="APPROVE_WORKER",
        target_id=worker_id,
        target_type="employee",
        details=f"Approved worker profile for {worker.name}"
    )
    
    return {"message": f"Worker {worker.name} approved."}

# --- System Configuration & Moderation ---

@router.delete("/suspend-user/{user_type}/{user_id}")
async def suspend_user(user_type: str, user_id: str, admin: Employer = Depends(get_current_admin)):
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

# --- Remaining Admin Endpoints ---

@router.get("/verification-queue", response_model=List[dict])
async def get_verification_queue(admin: Employer = Depends(get_current_admin)):
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
    admin: Employer = Depends(get_current_admin)
):
    """
    Returns the most recent administrative actions.
    """
    logs = await AuditLog.find_all().sort("-created_at").limit(limit).to_list()
    return logs