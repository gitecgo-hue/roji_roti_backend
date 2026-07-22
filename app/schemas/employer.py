from pydantic import BaseModel, EmailStr, Field, model_validator, ConfigDict
from typing import Optional, List
from datetime import datetime
from app.models.employer import EmployerType, SubscriptionTier

#==========================================
# EMPLOYER SCHEMAS
#=========================================

class EmployerCreate(BaseModel):
    """Schema for Employer Registration."""
    phone: str = Field(..., min_length=10, max_length=15, description="Mobile number with country code")
    employer_type: EmployerType
    name: str = Field(..., min_length=2, max_length=50, description="Contact person or individual name")
    location: str = Field(..., description="City or primary location")
    
    # Company fields
    company_name: Optional[str] = None
    email: Optional[EmailStr] = None
    gst_number: Optional[str] = None

    @model_validator(mode='after')
    def check_company_fields(self) -> 'EmployerCreate':
        if self.employer_type == EmployerType.COMPANY:
            if not self.company_name or not self.gst_number:
                raise ValueError('Company Name and GST Number are mandatory for Company accounts.')
        return self

class EmployerResponse(BaseModel):
    """Standard profile response for an Employer."""
    id: str
    phone: str
    employer_type: EmployerType
    name: str
    company_name: Optional[str]
    location: str
    subscription_tier: SubscriptionTier
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

class EmployerPersonalProfileUpdate(BaseModel):
    """Schema for updating the individual user's details"""
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    # Note: We usually don't include 'phone' here because changing a 
    # phone number requires a new OTP verification flow.

class EmployerCompanyProfileUpdate(BaseModel):
    """Schema for updating the business details"""
    company_name: Optional[str] = None
    industry: Optional[str] = None
    company_size: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    location: Optional[str] = None

class ReferralDashboardResponse(BaseModel):
    referral_code: str
    total_referred: int
    total_coins_earned: int