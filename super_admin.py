import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from dotenv import load_dotenv

# Load your .env variables so settings can initialize properly in a CLI context
load_dotenv()

# Import settings and models AFTER loading dotenv
from app.core.config import settings
from app.models.admin import Admin

async def create_root():
    print("Connecting to MongoDB...")
    
    # 1. Initialize the database connection
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DATABASE_NAME]
    
    # We only need to initialize the Admin model for this script
    await init_beanie(database=db, document_models=[Admin])

    # 2. Define root admin details
    root_phone = "9999999999" # <-- CHANGE THIS TO YOUR ACTUAL PHONE NUMBER
    root_email = "founder@rojiroti.com"
    
    # 3. Check if the admin already exists so we don't create duplicates
    existing_admin = await Admin.find_one({"phone": root_phone})
    
    if existing_admin:
        print("Super admin already exists! You can log in right now.")
        return

    # 4. Create the Superadmin (Passwordless - OTP Only!)
    print("Creating Superadmin account...")
    
    root_admin = Admin(
        name="System Founder",
        email=root_email,
        phone=root_phone,
        role="super_admin",
        is_active=True
    )
    
    await root_admin.insert()
    print(f"SUCCESS! Root Admin created with phone: {root_phone}")
    print("You can now log into the /api/v1/auth/admin/login endpoint using an OTP!")

if __name__ == "__main__":
    # Run the async function
    asyncio.run(create_root())