from fastapi import APIRouter, Depends, HTTPException, status
from app.models.employer import Employer
from app.models.subscriptions import Subscription
from app.services.subscriptions import SubscriptionService
from app.api.dependencies import get_current_employer

router = APIRouter()

@router.get("/status")
async def get_subscription_status(
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Returns the current usage and plan details for the employer.
    Used by the frontend to show 'Usage Bars' and the 'Upgrade' button.
    """
    # This uses our previously built Service to get the real OR virtual plan
    sub = await SubscriptionService.get_active_subscription(str(current_employer.id))
    
    # Get the limits for the current plan to calculate percentages
    plan_limits = SubscriptionService.QUOTA_LIMITS.get(
        sub.plan_type.lower(), 
        SubscriptionService.QUOTA_LIMITS["free"]
    )

    return {
        "plan_type": sub.plan_type.upper(),
        "is_active": sub.is_active,
        "expiry_date": sub.expiry_date,
        "usage": {
            "contacts": {
                "used": sub.contacts_checked,
                "limit": plan_limits["max_contacts"]
            },
            "resumes": {
                "used": sub.resumes_downloaded,
                "limit": plan_limits["max_resumes"]
            },
            "jobs": {
                "used": sub.jobs_posted,
                "limit": plan_limits["max_jobs"]
            },
            "india_jobs": {
                "used": sub.india_level_jobs_posted,
                "limit": plan_limits["max_india_jobs"]
            }
        }
    }