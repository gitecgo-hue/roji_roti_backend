import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Import models for the Enforcer
from app.models.subscriptions import Subscription
from app.models.job import Job

# Remaining imports for other tasks (Keep these if you have them, or remove if you build them here later)
from app.tasks.subscriptions import (
    dispatch_expiry_reminders,
    reset_free_tier_quotas
)

logger = logging.getLogger(__name__)

# Initialize the Async scheduler
scheduler = AsyncIOScheduler()

# --- THE SUBSCRIPTION ENFORCER ---
async def check_expired_subscriptions():
    """
    Finds all active subscriptions that have expired and deactivates them.
    Also hides any jobs associated with that employer.
    """
    now = datetime.utcnow()
    
    # 1. Find expired but still 'active' subscriptions
    expired_subs = await Subscription.find(
        Subscription.is_active == True,
        Subscription.expiry_date < now
    ).to_list()

    for sub in expired_subs:
        # 2. Deactivate the subscription
        sub.is_active = False
        await sub.save()

        # 3. Deactivate all jobs posted by this employer (Enforcement)
        await Job.find(Job.employer_id == sub.employer_id).update(
            {"$set": {"is_active": False}}
        )
        
        # Using logger instead of standard print for better server logs
        logger.info(f"Enforcement: Deactivated plan and jobs for Employer {sub.employer_id}")

# --- SCHEDULER ENGINE ---
def start_scheduler():
    """Attaches jobs to the scheduler and starts the engine."""
    
    # Task 1: Downgrade expired PAID accounts (Midnight + 1 min)
    # Now pointing directly to the function we defined above!
    scheduler.add_job(
        check_expired_subscriptions, 
        trigger=CronTrigger(hour=0, minute=1), 
        id="sweep_expired_subs", 
        replace_existing=True
    )

    # Task 2: Reset FREE tier usage for the next 30-day cycle (Midnight + 5 mins)
    scheduler.add_job(
        reset_free_tier_quotas, 
        trigger=CronTrigger(hour=0, minute=5), 
        id="reset_free_quotas", 
        replace_existing=True
    )
    
    # Task 3: Send SMS reminders (9:00 AM)
    scheduler.add_job(
        dispatch_expiry_reminders, 
        trigger=CronTrigger(hour=9, minute=0), 
        id="send_sms_reminders", 
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("Background Task Scheduler successfully started.")

def stop_scheduler():
    """Safely shuts down the scheduler."""
    scheduler.shutdown()
    logger.info("Background Task Scheduler shutdown.")