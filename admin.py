import asyncio
import os
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from dotenv import load_dotenv

# Import your app's models and security functions
from app.models.admin import Admin
from app.core.security import get_password_hash

# Load your .env variables so it can connect to MongoDB
load_dotenv()

async def create_superadmin():
    print("Connecting to MongoDB...")
    
    # 1. Initialize the database connection
    mongo_url = os.getenv("MONGODB_URL")
    db_name = os.getenv("DATABASE_NAME", "roji_roti_db")
    
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    # We only need to initialize the Admin model for this script
    await init_beanie(database=db, document_models=[Admin])

    # 2. Check if the admin already exists so we don't create duplicates
    admin_email = "admin@rojiroti.com"
    existing_admin = await Admin.find_one(Admin.email == admin_email)
    
    if existing_admin:
        print("Superadmin already exists! You can log in right now.")
        return

    # 3. Create the Superadmin
    print("🔨 Creating Superadmin account...")
    hashed_password = get_password_hash("Admin@1234") 
    
    new_admin = Admin(
        name="System Admin",
        email=admin_email,
        phone="9999999999",
        hashed_password=hashed_password,
        is_active=True,
        role="superadmin"
    )
    
    await new_admin.insert()

if __name__ == "__main__":
    # Run the async function
    asyncio.run(create_superadmin())