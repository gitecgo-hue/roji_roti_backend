import logging
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from app.core.config import settings

# Models
from app.models.admin import Admin
from app.models.employee import JobApplication, Employee
from app.models.employer import Employer
from app.models.job import Job
from app.models.rating import Rating
from app.models.category import Category       
from app.models.subscriptions import Subscription
from app.models.contact import ContactUnlock
from app.models.auth import OTP, TokenBlacklist
from app.models.payment import Payment
from app.models.partner import Partner
from app.models.promo import PromoCode
from app.models.notification import Notification
from app.models.transaction import Transaction
from app.models.application import JobApplication
from app.models.saved_search import SavedSearch

logger = logging.getLogger(__name__)

class Database:
    client: AsyncIOMotorClient = None

db = Database()

async def connect_to_mongo():
    logger.info("Connecting to MongoDB...")
    
    # 1. Initialize the Async Client
    db.client = AsyncIOMotorClient(settings.MONGODB_URL)
    
    # 2. Get the specific database instance
    database = db.client[settings.DATABASE_NAME]

    # =================================================================
    # 2.5 FORCE DROP OLD CONFLICTING INDEXES
    # =================================================================
    try:
        logger.info("Checking for old conflicting 'phone_1' indexes...")
        await database.employees.drop_index("phone_1")
        await database.employers.drop_index("phone_1")
        logger.info("Successfully dropped old phone indexes!")
    except Exception:
        # If they don't exist, MongoDB throws an error. We just ignore it and move on!
        logger.info("No conflicting indexes found. Moving on.")
    
    # 3. Initialize Beanie with the full suite of models
    await init_beanie(
        database=database,
        document_models=[
            Admin,
            Employee,
            Employer,        
            Job,
            JobApplication,
            Notification,        
            Rating,
            Category,        
            Subscription,    
            OTP,
            TokenBlacklist,
            Payment,
            PromoCode,        
            Partner,
            ContactUnlock,
            Transaction,
            SavedSearch
        ]
    )
    logger.info(f"Successfully connected to {settings.DATABASE_NAME} and initialized Beanie.")

    # 4. Seed initial categories if the collection is empty
    category_count = await Category.find_all().count()
    if category_count == 0:
        logger.info("Scanning existing jobs for missing categories...")
        
        # Grab every unique category name currently used in the Job collection
        # (Requires importing the Job model at the top of this file!)
        used_job_categories = await Job.distinct("job_category")
        
        if used_job_categories:
            # Fetch all existing categories from the database to compare
            all_db_categories = await Category.find_all().to_list()
            db_category_names = {cat.name for cat in all_db_categories}
            
            missing_categories = []
            
            for cat_name in used_job_categories:
                # If an employer used a category that isn't in the DB yet, queue it up!
                if cat_name and cat_name not in db_category_names:
                    missing_categories.append(
                        Category(
                            name=cat_name,
                            description=f"All jobs related to {cat_name}",
                            is_active=True
                        )
                    )
            
            # Bulk insert the missing ones for maximum performance
            if missing_categories:
                await Category.insert_many(missing_categories)
                logger.info(f"Auto-created {len(missing_categories)} new categories based on active job posts!")
            else:
                logger.info("All job categories are perfectly synced.")

async def close_mongo_connection():
    logger.info("Closing MongoDB connection...")
    if db.client is not None:
        db.client.close()
        logger.info("MongoDB connection closed.")

def get_database():
    """
    Returns the raw motor database instance if needed for 
    complex aggregation or non-Beanie operations.
    """
    return db.client[settings.DATABASE_NAME]