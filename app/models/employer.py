from beanie import Document, Indexed, before_event, Replace, Save, Update
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List
from datetime import datetime, timezone
from enum import Enum

class EmployerType(str, Enum):
    COMPANY = "company"
    INDIVIDUAL = "individual"
    AGENCY = "agency"

class SubscriptionTier(str, Enum):
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"

class GeoLocation(BaseModel):
    type: str = "Point"
    coordinates: List[float]

class Employer(Document):
    # --- Core Identity & Auth ---
    phone: Indexed(str, unique=True)
    phone_verified: bool = False
    email: Optional[EmailStr] = None
    email_verified: bool = False
    
    # --- Company Details ---
    name: str = Field(..., description="Owner/Contact Name")    
    company_name: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    
    # --- Industry & Size ---
    industry: Optional[str] = None
    company_size: Optional[str] = Field(None, description="e.g., '1-10', '50-200'")
    founded_year: Optional[str] = None
    website: Optional[str] = None
    company_type: Optional[str] = None
    social_profiles: Optional[dict] = None
    
    # Add these inside your Employer class
    gstin: Optional[str] = None
    billing_address: Optional[str] = None
    available_credits: int = 0
    
    # --- Location Data ---
    address: Optional[str] = None
    location: Optional[GeoLocation] = None

    # --- Referral & Tracking ---
    referral_code: Optional[str] = None
    referred_by_code: Optional[str] = None
    
    # --- System & Status ---
    employer_type: EmployerType = EmployerType.COMPANY
    subscription_tier: SubscriptionTier = SubscriptionTier.FREE
    is_active: bool = True
    is_verified: bool = False # Verified by Admin

    last_otp_requested_at: Optional[datetime] = None
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    @before_event(Replace, Save, Update)
    def update_timestamp(self):
        self.updated_at = datetime.now(timezone.utc)

    class Settings:
        name = "employers"
        indexes = [
            "company_name",
            [("location.coordinates", "2dsphere")]
        ]