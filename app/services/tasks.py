import logging
from datetime import datetime
from app.models.subscriptions import Subscription

logger = logging.getLogger(__name__)

async def reset_monthly_quotas():
    """
    Resets usage counters for all employers at the start of their billing cycle.
    Ensures individual employers get their 20 free contacts refreshed .
    """
    logger.info("Starting monthly quota reset task...")
    
    try:
        # Reset counters for all subscriptions in the database 
        await Subscription.find_all().update({
            "$set": {
                "resumes_downloaded": 0,
                "contacts_checked": 0,
                "jobs_posted": 0,
                "india_level_jobs_posted": 0
            }
        })
        logger.info("Successfully reset all employer quotas for the new month.")
    except Exception as e:
        logger.error(f"Failed to reset quotas: {e}")