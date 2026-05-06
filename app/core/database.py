import logging
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie

from app.core.config import settings

# Models
from app.models.employee import Employee
from app.models.employer import Employer
from app.models.application import JobApplication
from app.models.job import Job
from app.models.rating import Rating
from app.models.category import Category       
from app.models.subscriptions import Subscription
from app.models.contact import ContactUnlock
from app.models.auth import OTP                
from app.models.payment import Payment
from app.models.partner import Partner
from app.models.promo import PromoCode
from app.models.notification import Notification
from app.models.transaction import Transaction

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
    
    # 3. Initialize Beanie with the full suite of models
    await init_beanie(
        database=database,
        document_models=[
            Employee,
            Employer,         
            Job,
            JobApplication,
            Notification,         
            Rating,
            Category,         
            Subscription,     
            OTP,
            Payment,
            PromoCode,        
            Partner,
            ContactUnlock,
            Transaction
        ]
    )
    logger.info(f"Successfully connected to {settings.DATABASE_NAME} and initialized Beanie.")

    # 4. Seed initial categories if the collection is empty
    category_count = await Category.find_all().count()
    if category_count == 0:
        logger.info("Seeding initial categories...")
        initial_categories = [
            Category(name="Carpenter", description="Woodwork, furniture making, and repairs"),
            Category(name="Plumber", description="Pipe installation, drainage, and leak repairs"),
            Category(name="Electrician", description="Wiring, electrical maintenance, and installations"),
            Category(name="Mason", description="Bricklaying, concrete work, and construction"),
            Category(name="Driver", description="Commercial and private vehicle operation")
        ]
        # insert_many is faster for bulk operations
        await Category.insert_many(initial_categories)
        logger.info("Categories seeded successfully.")

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