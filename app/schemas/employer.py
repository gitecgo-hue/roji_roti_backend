from pydantic import BaseModel, EmailStr, Field, model_validator, ConfigDict
from typing import Dict, Optional, List
from datetime import datetime

# --- Model Imports ---
from app.models.employer import EmployerType, SubscriptionTier
from app.models.employer import KYCStatus

# --- Employer Registration & Profile Schemas ---
class EmployerCreate(BaseModel):
    """Schema for Employer Registration (Initial Sign-up)."""
    name: str = Field(..., min_length=2, max_length=50, description="Employer's personal name")
    phone: str = Field(..., min_length=10, max_length=15, description="Mobile number with country code")
    
    # Make these optional so they aren't strictly required at step 1
    employer_type: Optional[EmployerType] = None
    location: Optional[str] = Field(None, description="City or primary location")
    
    # Company fields (Filled out later during profile completion)
    company_name: Optional[str] = None
    email: Optional[EmailStr] = None
    gst_number: Optional[str] = None

class EmployerResponse(BaseModel):
    """Standard profile response for an Employer."""
    id: str
    phone: str
    employer_type: EmployerType
    name: str
    company_name: Optional[str]
    location: Optional[str] = None
    subscription_tier: SubscriptionTier
    is_verified: Optional[bool] = False
    email: Optional[EmailStr] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class EmployerDashboardResponse(BaseModel):
    """
    At-a-glance dashboard status for Employers.
    Includes subscription status and recruitment funnel metrics.
    """
    company_name: Optional[str]
    subscription_tier: SubscriptionTier 
    is_active: bool
    days_left: int
    expiry_date: Optional[datetime]
    
    # --- Recruitment Funnel Counters ---
    active_jobs_count: int = 0
    total_applicants_count: int = 0
    shortlisted_count: int = 0
    
    # --- Subscription Usage Statistics ---
    job_posts_used: int = 0
    contacts_viewed: int = 0

    model_config = ConfigDict(from_attributes=True)


# --- Unified Update Schema (Used in PATCH /profile_update) ---
class EmployerProfileUpdate(BaseModel):
    """Unified schema for updating or completing the employer's profile."""
    
    # --- Personal Details ---
    name: Optional[str] = Field(None, min_length=2, max_length=50)
    email: Optional[EmailStr] = None
    employer_type: Optional[EmployerType] = None
    
    # --- Company Details ---
    company_name: Optional[str] = None
    gstin: Optional[str] = None
    logo_url: Optional[str] = None
    founded_year: Optional[str] = None
    website: Optional[str] = None
    company_size: Optional[str] = None
    company_type: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    social_profiles: Optional[Dict[str, str]] = None
    
    # --- Location Details ---
    company_address: Optional[str] = None  # Used for Ola Maps Geocoding
    address: Optional[str] = None


# --- Employer Kyc Detils ---
class KYCSubmitRequest(BaseModel):
    document_type: str        # e.g., "PAN", "GSTIN", "AADHAAR"
    document_number: str
    document_url: str         # URL to uploaded image/pdf

class AdminKYCStatusUpdate(BaseModel):
    status: KYCStatus
    remarks: Optional[str] = None

# --- Specific Responses ---
class ReferralDashboardResponse(BaseModel):
    referral_code: str
    total_referred: int
    total_coins_earned: int

class EmployerPersonalProfileResponse(BaseModel):
    """Schema for returning only the personal details"""
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: str
    email_verified: bool
    gstin: Optional[str] = None

class EmployerCompanyProfileResponse(BaseModel):
    """Schema for returning only the business details"""
    company_name: Optional[str] = None
    logo_url: Optional[str] = None
    founded_year: Optional[str] = None
    website: Optional[str] = None
    company_size: Optional[str] = None
    company_type: Optional[str] = None
    industry: Optional[str] = None
    description: Optional[str] = None
    social_profiles: Optional[Dict[str, str]] = None
    address: Optional[str] = None

class CompanyProfilePublicResponse(BaseModel):
    employer_id: str
    recruiter_name: Optional[str] = None
    company_name: Optional[str] = None
    industry: Optional[str] = None
    email: Optional[str] = None
    logo_url: Optional[str] = None