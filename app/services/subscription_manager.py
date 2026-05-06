import asyncio
import logging
from datetime import datetime
from app.models.employer import Employer, SubscriptionTier  # Using the Enum for type-safety
from app.utils.sms import SMSService

logger = logging.getLogger(__name__)

async def check_and_downgrade_subscriptions():
    """
    Finds all employers whose subscription has expired and resets them to 'Free'.
    Runs once every 24 hours.
    """
    # 1. Startup Delay: Wait for MongoDB/Beanie to fully initialize indexes
    await asyncio.sleep(5) 

    while True:
        logger.info("Running daily subscription expiry check...")
        
        now = datetime.utcnow()
        
        try:
            # 2. Query for expired employers using model-consistent fields
            # We look for employers whose end_date is in the past and are NOT already 'Free'
            expired_employers = await Employer.find(
                Employer.subscription_end_date < now,
                Employer.subscription_tier != SubscriptionTier.FREE
            ).to_list()

            for employer in expired_employers:
                old_tier = employer.subscription_tier
                
                # 3. Update the database record
                employer.subscription_tier = SubscriptionTier.FREE
                employer.subscription_end_date = None
                await employer.save()
                
                # 4. Notify the employer via the SMS utility
                # We notify them that their access has expired (days_left=0)
                await SMSService.send_subscription_reminder(
                    phone=employer.phone, 
                    days_left=0 
                )
                
                logger.info(f"Downgraded Employer {employer.phone} from {old_tier} to Free tier.")
        
        except Exception as e:
            # 5. Error Shield: Catch errors so the infinite loop doesn't crash the background task
            logger.error(f"Error in subscription manager loop: {e}")

        # 6. Wait for 24 hours (86400 seconds) before the next sweep
        await asyncio.sleep(86400)