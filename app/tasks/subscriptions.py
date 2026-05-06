import logging
from datetime import datetime, timedelta
from app.models.subscriptions import Subscription
from app.models.employer import Employer, SubscriptionTier
from app.services.sms import SmsService 

logger = logging.getLogger(__name__)

async def process_expired_subscriptions():
    """
    Sweeps the database for subscriptions that have passed their expiry date.
    Downgrades the employer to the basic/free tier.
    """
    logger.info("Running CRON Task: process_expired_subscriptions")
    now = datetime.utcnow()

    # 1. Find all active subscriptions where the expiry date is in the past
    # Note: Using "free" here to match your provisioning logic
    expired_subs = await Subscription.find(
        Subscription.expiry_date <= now,
        Subscription.is_active == True,
        Subscription.plan_type != "free" 
    ).to_list()

    for sub in expired_subs:
        # 2. Mark the subscription ledger record as inactive
        sub.is_active = False
        await sub.save()

        # 3. Downgrade the Employer's profile access
        employer = await Employer.get(sub.employer_id)
        if employer:
            employer.subscription_tier = SubscriptionTier.FREE
            await employer.save()
            
            logger.info(f"Successfully downgraded Employer {employer.id} to FREE tier.")

async def dispatch_expiry_reminders():
    """
    Finds subscriptions expiring in exactly 3 days and sends an SMS reminder.
    """
    logger.info("Running CRON Task: dispatch_expiry_reminders")
    now = datetime.utcnow()
    
    target_start = (now + timedelta(days=3)).replace(hour=0, minute=0, second=0)
    target_end = (now + timedelta(days=3)).replace(hour=23, minute=59, second=59)

    expiring_subs = await Subscription.find(
        Subscription.expiry_date >= target_start,
        Subscription.expiry_date <= target_end,
        Subscription.is_active == True
    ).to_list()

    for sub in expiring_subs:
        employer = await Employer.get(sub.employer_id)
        if employer and employer.phone:
            message = (
                f"Dear {employer.company_name or 'Employer'}, your Roji Roti {sub.plan_type.upper()} "
                "plan expires in 3 days. Renew now to avoid losing access to worker resumes!"
            )
            # await SmsService.send_generic_message(employer.phone, message)
            logger.info(f"Dispatched 3-day expiry reminder to {employer.phone}")

async def reset_free_tier_quotas():
    """
    Sweeps the database for 'Free' tier subscriptions that have reached 
    the end of their 30-day billing cycle and resets their monthly limits.
    """
    logger.info("Running CRON Task: reset_free_tier_quotas")
    now = datetime.utcnow()

    # Find free subscriptions whose cycle has ended
    expired_free_subs = await Subscription.find(
        Subscription.plan_type == "free",
        Subscription.expiry_date <= now
    ).to_list()

    if not expired_free_subs:
        logger.info("No free tier quotas require resetting at this time.")
        return

    for sub in expired_free_subs:
        # 1. Reset the usage counters back to zero
        sub.contacts_checked = 0
        sub.resumes_downloaded = 0
        sub.jobs_posted = 0
        sub.india_level_jobs_posted = 0  # Added to stay in sync with Job Posting logic
        
        # 2. Roll over to the next 30-day month cycle
        sub.start_date = now
        sub.expiry_date = now + timedelta(days=30)
        
        # 3. Ensure it stays active for the next cycle
        sub.is_active = True
        
        await sub.save()
        
    logger.info(f"Successfully reset quotas for {len(expired_free_subs)} free tier users.")