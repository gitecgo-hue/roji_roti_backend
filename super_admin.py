import asyncio
import random
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from dotenv import load_dotenv

# Load your .env variables so settings can initialize properly
load_dotenv()

# Import settings and ALL user models
from app.core.config import settings
from app.models.admin import Admin
from app.models.employer import Employer
from app.models.employee import Employee

async def create_user():
    print("\n" + "="*50)
    print(" 🛠️  ROJI ROTI UNIVERSAL ACCOUNT GENERATOR  🛠️ ")
    print("="*50)
    
    # 1. Choose the Account Type
    print("\nWhich type of account do you want to create?")
    print("1. Root Admin (Platform Manager)")
    print("2. Employer (Job Poster / Company)")
    print("3. Employee (Blue-Collar Employee)")
    
    choice = input("\nEnter 1, 2, or 3: ").strip()
    
    if choice not in ["1", "2", "3"]:
        print("❌ Error: Invalid choice. Exiting.")
        return
        
    role_map = {"1": "Admin", "2": "Employer", "3": "Employee"}
    selected_role = role_map[choice]
    
    print(f"\n--- Initiating {selected_role} Setup ---")

    # 2. Ask for the Identifier (Mobile Number)
    phone = input(f"Enter the 10-digit mobile number for this {selected_role}: ").strip()
    
    if len(phone) != 10 or not phone.isdigit():
        print("❌ Error: Invalid phone number. It must be exactly 10 digits.")
        return

    # 3. Generate a simulated OTP (Unified for all roles)
    generated_otp = str(random.randint(1000, 9999))
    
    print("\n" + "-"*40)
    print(f"📱 SIMULATED SMS SENT TO {phone}:")
    print(f"Your Roji Roti Setup OTP is: {generated_otp}")
    print("-"*40 + "\n")

    entered_otp = input("Enter the 4-digit OTP you received: ").strip()

    if entered_otp != generated_otp:
        print("❌ Verification Failed: The OTP entered is incorrect. Setup aborted.")
        return
        
    print("✅ OTP Verified Successfully!\n")

    # 4. Gather Role-Specific Data
    name = input(f"Enter full name [Default: Test {selected_role}]: ").strip() or f"Test {selected_role}"
    
    # Dictionaries to hold extra data based on role
    extra_data = {}
    
    if selected_role == "Admin":
        extra_data["email"] = input("Enter admin email: ").strip()
        extra_data["role"] = "super_admin"
        
    elif selected_role == "Employer":
        extra_data["company_name"] = input("Enter Company Name [Default: Demo Corp]: ").strip() or "Demo Corp"
        # Assuming your Employer model uses 'employer_type'
        extra_data["employer_type"] = "company" 
        
    elif selected_role == "Employee":
        extra_data["category"] = input("Enter Trade Category (e.g., Plumber, Electrician): ").strip() or "General Employee"
        extra_data["location_name"] = input("Enter City/Location [Default: Indore]: ").strip() or "Indore"
        # Mocking a basic GeoLocation so Pydantic doesn't crash during setup
        extra_data["current_location"] = {"type": "Point", "coordinates": [75.8577, 22.7196]} 

    # 5. Initialize the database connection with ALL models
    print("\nConnecting to MongoDB...")
    client = AsyncIOMotorClient(settings.MONGODB_URL)
    db = client[settings.DATABASE_NAME]
    
    # We must register all models here so Beanie knows how to handle them
    await init_beanie(database=db, document_models=[Admin, Employer, Employee])

    # 6. Check for existing users and Insert
    if selected_role == "Admin":
        if await Admin.find_one({"phone": phone}):
            print(f"⚠️ An {selected_role} with phone {phone} already exists!")
            return
        new_user = Admin(name=name, phone=phone, is_active=True, **extra_data)
        
    elif selected_role == "Employer":
        if await Employer.find_one({"phone": phone}):
            print(f"⚠️ An {selected_role} with phone {phone} already exists!")
            return
        new_user = Employer(name=name, phone=phone, is_active=True, **extra_data)
        
    elif selected_role == "Employee":
        if await Employee.find_one({"phone": phone}):
            print(f"⚠️ An {selected_role} with phone {phone} already exists!")
            return
        # Bypassing KYC for test employee so they show up in searches immediately
        new_user = Employee(name=name, phone=phone, is_active=True, is_approved=True, kyc_status="VERIFIED", **extra_data)

    # Save to the database
    await new_user.insert()
    
    print("\n" + "*"*50)
    print(f"🎉 SUCCESS! {selected_role} '{name}' created!")
    print(f"Phone: {phone}")
    print("You can now log into the respective portal with this number.")
    print("*"*50 + "\n")

if __name__ == "__main__":
    try:
        asyncio.run(create_user())
    except KeyboardInterrupt:
        print("\nSetup cancelled by user.")