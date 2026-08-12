import re
from typing import List, Optional
from datetime import date, datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from beanie import PydanticObjectId

# =====================================================================
# API UPDATE SCHEMAS (Frontend Payloads)
# =====================================================================
class EducationUpdate(BaseModel):
    institute: Optional[str] = None
    field_of_study: Optional[str] = None
    start_year: Optional[str] = None
    end_year: Optional[str] = None

class WorkExperienceUpdate(BaseModel):
    job_title: Optional[str] = None
    job_role: Optional[str] = None
    company_name: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    currently_working_here: Optional[bool] = None

class EmployeeProfileUpdate(BaseModel):
    """Unified schema for updating the employee's full profile"""
    # --- Basic Details ---
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    location: Optional[str] = None
    about_you: Optional[str] = None

    # --- Preferences ---
    expected_salary: Optional[float] = None
    notice_period_days: Optional[int] = None
    preferred_job_types: Optional[List[str]] = None
    preferred_locations: Optional[List[str]] = None
    remote_work: Optional[bool] = None

    # --- Skills & Languages ---
    skills: Optional[List[str]] = None
    languages: Optional[List[str]] = None

    # --- Nested Lists ---
    work_experience: Optional[List[WorkExperienceUpdate]] = None
    education: Optional[List[EducationUpdate]] = None

# =====================================================================
# OTHER SCHEMAS
# =====================================================================

class LocationInput(BaseModel):
    """Handles latitude and longitude for GPS-based job filtering."""
    latitude: float
    longitude: float

class AvailabilityUpdate(BaseModel):
    """
    Schema for the employee availability toggle.
    True: Available for jobs (Green).
    False: Not available (Red).
    """
    is_available: bool

class EmployeeCreate(BaseModel):
    """
    Fields required for mobile registration of Job Seekers.
    Combined from core requirements and validation constraints.
    """
    phone: str = Field(..., min_length=10, max_length=15, description="Mobile number (Required)")
    name: str = Field(..., min_length=2, max_length=50, description="Employee name (Required)")
    category: str = Field(..., description="Job Category (E.g., Driver, House Help)")
    
    # Location data for proximity-based matching
    location_name: str = Field(..., description="Human-readable location name (Required)")
    
    # --- MADE OPTIONAL FOR OLA MAPS AUTO-FILL ---
    location: Optional[LocationInput] = Field(
        default=None, 
        description="Backend will auto-fill this using Ola Maps"
    )
    
    preferred_locations: List[str] = Field(default=[], description="Multiple locations selection")
    
    # Profile details
    work_experience: int = Field(default=0, ge=0, description="Years of experience")
    languages: List[str] = Field(default=["Hindi"], description="Supported languages")
    
    # Integrated expected_salary as str for maximum flexibility
    expected_salary: Optional[float] = Field(None, description="Employee's salary expectation")
    gender: Optional[str] = Field(None, description="Gender selection")
    email: Optional[EmailStr] = Field(None, description="Optional email address")
    
    # Referral and tracking
    referred_by_id: Optional[str] = None

class EmployeeResponse(BaseModel):
    id: str
    role: str
    phone: str
    phone_verified: bool
    
    # Names & Bio
    name: Optional[str] = None
    summary: Optional[str] = None
    email: Optional[EmailStr] = None
    avatar: Optional[str] = None
    
    # Location
    location: Optional[dict] = None
    
    # Professional Details (Auto-serialized by FastAPI)
    skills: Optional[list] = []
    work_experience: Optional[list] = []
    education: Optional[list] = []
    
    # Preferences & Settings
    contact_visibility: Optional[str] = None
    expected_salary: Optional[float] = None
    availability: Optional[dict] = None
    preferences: Optional[dict] = None
    social_links: Optional[dict] = None
    
    # System & Status
    metadata: Optional[dict] = None
    status: Optional[str] = None
    verified_by_admin: bool = False

class EmployeeDashboardResponse(BaseModel):
    """
    Schema for the employee-facing dashboard.
    Shows engagement metrics like total unlocks.
    """
    name: str
    category: str
    is_available: bool
    total_unlocks: int
    location: str
    daily_rate: Optional[float]
    rating: float = 0.0

    model_config = ConfigDict(
        from_attributes=True
    )

class WorkExperienceInput(BaseModel):
    job_title: Optional[str] = None
    job_role: Optional[str] = None
    company_name: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    currently_working_here: Optional[bool] = None

class SkillInput(BaseModel):
    name: str

class AppliedJobResponse(BaseModel):
    """Schema for returning a job the employee has applied to"""
    application_id: str
    job_id: str
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    status: str
    applied_at: datetime

class SavedJobResponse(BaseModel):
    """Schema for returning a saved job to the employee"""
    job_id: str
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    location: Optional[str] = None
    expected_salary: Optional[float] = None
    created_at: Optional[datetime] = None