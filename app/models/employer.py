from beanie import Document, Indexed
from pydantic import Field, EmailStr, ConfigDict
from typing import Optional
from datetime import datetime
from enum import Enum

class EmployerType(str, Enum):
    INDIVIDUAL = "individual"
    COMPANY = "company"

class SubscriptionTier(str, Enum):
    FREE = "Free"
    BASIC = "Basic"
    PREMIUM = "Premium"
    ENTERPRISE = "Enterprise"

class Employer(Document):
    # --- Core Identity ---
    employer_type: EmployerType
    phone: Indexed(str, unique=True)
    location: Optional[str] = None
    profile_picture_url: Optional[str] = None
    hashed_password: Optional[str] = None
    
    # --- Profile Details ---
    name: Optional[str] = None  # For Individuals
    company_name: Optional[str] = None  # For Companies
    contact_name: Optional[str] = None
    email: Optional[EmailStr] = None
    
    # --- OTP & Verification ---
    otp_code: Optional[str] = None
    otp_expires_at: Optional[datetime] = None
    last_otp_requested_at: Optional[datetime] = None

    # --- Verification & Trust ---
    gst_number: Optional[str] = None
    is_gst_verified: bool = False  # Controlled by Admin
    
    # --- Subscription Logic (Integrated) ---
    # We use the Enum for plan_type to prevent typos
    subscription_tier: SubscriptionTier = SubscriptionTier.FREE
    subscription_end_date: Optional[datetime] = None
    
    # --- Platform Status ---
    # is_active controls if the employer can log in or post jobs
    is_active: bool = True 
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True
    )

    class Settings:
        name = "employers"