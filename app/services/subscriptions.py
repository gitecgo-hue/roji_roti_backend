from fastapi import HTTPException, status
from datetime import datetime, timedelta
from typing import Optional
from app.models.subscriptions import Subscription

class SubscriptionService:
    # --- Unified Quota Limits ---
    # The keys inside each plan MUST match the 'action_type' you pass in your endpoints!
    QUOTA_LIMITS = {
        "free": {
            "contact_view": 20,
            "download_resume": 0,    
            "jobs_posted": 5,
            "india_jobs_posted": 0
        },
        "standard": {
            "contact_view": 500,
            "download_resume": 300,
            "jobs_posted": 10,
            "india_jobs_posted": 2
        },
        "premium": {
            "contact_view": 1000,
            "download_resume": 500,  # Premium gets 500 resumes!
            "jobs_posted": 25,
            "india_jobs_posted": 5
        },
        "enterprise": {
            "contact_view": 5000,
            "download_resume": 1500,
            "jobs_posted": 50,
            "india_jobs_posted": 10
        }
    }

    @classmethod
    async def get_active_subscription(cls, employer_id: str) -> Subscription:
        """Fetches active subscription or returns a virtual 'free' plan."""
        sub = await Subscription.find_one(
            Subscription.employer_id == str(employer_id),
            Subscription.is_active == True,
            Subscription.expiry_date > datetime.utcnow()
        )
        
        if not sub:
            return Subscription(
                employer_id=employer_id, 
                plan_type="free", 
                contacts_checked=0, 
                resumes_downloaded=0, 
                jobs_posted=0,
                india_level_jobs_posted=0,
                expiry_date=datetime.utcnow() + timedelta(days=30) 
            )
        return sub

    @classmethod
    async def increment_usage(cls, employer_id: str, action_type: str, is_india_level: bool = False):
        """
        Increments the specific usage counter for the employer's active plan.
        Standalone incrementer: No quota validation performed here.
        """
        sub = await cls.get_active_subscription(employer_id)
        
        # We don't save usage for the "virtual free" fallback (it has no DB ID)
        if not hasattr(sub, 'id') or not sub.id: 
            return

        if action_type == "post_job":
            if is_india_level:
                sub.india_level_jobs_posted += 1
            else:
                sub.jobs_posted += 1
        elif action_type == "contact_view":
            sub.contacts_checked += 1
        elif action_type == "resume_download":
            sub.resumes_downloaded += 1
        
        await sub.save()

    @classmethod
    async def check_and_track_usage(cls, employer_id: str, action_type: str, is_india_level: bool = False):
        """Consolidated logic: Validates quota AND increments usage."""
        # Perform validation first
        await cls.check_quota(employer_id, action_type, is_india_level)
        
        # Then increment usage
        await cls.increment_usage(employer_id, action_type, is_india_level)
        return True

    @classmethod
    async def check_quota(cls, employer_id: str, action_type: str, is_india_level: bool = False):
        """Standalone check for UI validation or pre-flight checks."""
        sub = await cls.get_active_subscription(employer_id)
        limits = cls.QUOTA_LIMITS.get(sub.plan_type.lower(), cls.QUOTA_LIMITS["free"])
        
        limit_mapping = {
            "contact_view": limits["contact_view"],
            "download_resume": limits["download_resume"],
            "post_job": limits["india_jobs_posted"] if is_india_level else limits["jobs_posted"]
        }

        usage_mapping = {
            "contact_view": sub.contacts_checked,
            "download_resume": sub.resumes_downloaded,
            "post_job": sub.india_level_jobs_posted if is_india_level else sub.jobs_posted
        }

        allowed_limit = limit_mapping.get(action_type, 0)
        current_usage = usage_mapping.get(action_type, 0)

        if current_usage >= allowed_limit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Quota exceeded for {action_type}. Your {sub.plan_type.upper()} plan allows {allowed_limit} uses per month."
            )

    @classmethod
    async def track_usage(cls, employer_id: str, action_type: str, is_india_level: bool = False):
        """Alias for increment_usage to maintain backward compatibility."""
        await cls.increment_usage(employer_id, action_type, is_india_level)