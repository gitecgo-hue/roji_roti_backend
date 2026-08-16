from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, EmailStr, Field, ConfigDict, HttpUrl, field_validator, model_validator
from typing import Optional, List, Dict, Any, Union, Annotated
from datetime import datetime, timezone, date
from enum import Enum

# --- Import Models ---
from app.models.base import TranslatableDocument

# =====================================================================
# SUB-MODELS & ENUMS (Database Structure)
# =====================================================================

class Role(str, Enum):
    EMPLOYEE = "employee"
    EMPLOYER = "employer"

class ContactVisibility(str, Enum):
    PRIVATE = "private"
    RESTRICTED = "restricted"
    PUBLIC = "public"

class AdminStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISABLED = "disabled"

class GeoLocation(BaseModel):
    type: str = "Point"
    coordinates: List[float]
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None

class Skill(BaseModel):
    name: str

class WorkExperience(BaseModel):
    job_title: Optional[str] = None
    job_role: Optional[str] = None
    company_name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    currently_working_here: Optional[bool] = None

class Education(BaseModel):
    institute: Optional[str] = None
    degree: Optional[str] = None
    field_of_study: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None

class ProfileDocument(BaseModel):
    type: str = Field(..., description="resume, id, cert")
    url: HttpUrl
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Availability(BaseModel):
    is_available: bool = True
    notice_period_days: Optional[int] = 0

class SocialLinks(BaseModel):
    linkedin: Optional[HttpUrl] = None
    github: Optional[HttpUrl] = None
    website: Optional[HttpUrl] = None

class Preferences(BaseModel):
    job_types: Optional[List[str]] = Field(default_factory=list)
    locations: Optional[List[str]] = Field(default_factory=list)
    remote_ok: Optional[bool] = False

class ProfileMetadata(BaseModel):
    profile_completion: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# =====================================================================
# API UPDATE SCHEMAS (Frontend Payloads)
# =====================================================================

# FIXED: Moved these above EmployeeProfileUpdate to prevent NameError
class EducationUpdate(BaseModel):
    institute: Optional[str] = None
    degree: Optional[str] = None          
    field_of_study: Optional[str] = None 
    start_year: Optional[int] = None
    end_year: Optional[int] = None

class WorkExperienceUpdate(BaseModel):
    job_title: Optional[str] = None
    job_role: Optional[str] = None
    company_name: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    currently_working_here: Optional[bool] = None

    @model_validator(mode='after')
    def enforce_null_end_date(self):
        """
        If the user is currently working here, ensure the end_date is always null.
        """
        if self.currently_working_here:
            self.end_date = None
            
        # If they ARE NOT working there anymore, force them to provide an end_date!
        elif self.end_date is None:
            raise ValueError("end_date is required if you are no longer working here")

class EmployeeProfileUpdate(BaseModel):
    # --- Basic Details ---
    name: Optional[str] = Field(None, alias="full_name")
    title: Optional[str] = Field(None, alias="job_title")
    location: Optional[str] = None
    summary: Optional[str] = Field(None, alias="about_you")
    gender: Optional[str] = Field(None, description="Gender selection")

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
# MAIN EMPLOYEE DOCUMENT
# =====================================================================

class Employee(TranslatableDocument):
    # --- Core Identity ---
    role: Role = Role.EMPLOYEE
    phone: str = Indexed(str, unique=True)
    phone_verified: bool = False
    
    # --- Names & Bio ---
    title: Optional[str] = None
    name: Optional[str] = None
    summary: Optional[str] = None
    email: Optional[EmailStr] = None
    email_verified: bool = False
    total_experience: Optional[float] = None
    gender: Optional[str] = None

    # FIXED: Renamed to match the upload/delete endpoints
    profile_picture_url: Optional[str] = None
    
    # --- Location Data ---
    location_name: Optional[str] = None
    location: Optional[GeoLocation] = None

    # --- Saved Jobs & Applications ---
    saved_job_ids: List[str] = Field(default_factory=list)
    
    # --- Professional Details ---
    skills: Optional[List[Skill]] = Field(default_factory=list)
    work_experience: Optional[List[WorkExperience]] = Field(default_factory=list)
    education: Optional[List[Education]] = Field(default_factory=list)
    resume_url: Optional[str] = None
    languages: Optional[List[str]] = None
    
    # --- Preferences & Settings ---
    contact_visibility: ContactVisibility = ContactVisibility.RESTRICTED
    expected_salary: Optional[float] = None
    availability: Optional[Availability] = Field(default_factory=Availability)
    preferences: Optional[Preferences] = Field(default_factory=Preferences)
    social_links: Optional[SocialLinks] = Field(default_factory=SocialLinks)
    
    # --- System, Tracking & Admin ---
    metadata: Optional[ProfileMetadata] = Field(default_factory=ProfileMetadata)
    status: AdminStatus = AdminStatus.ACTIVE
    admin_notes: Optional[str] = None
    
    # --- Legacy/Auth (Keeping for compatibility) ---
    last_otp_requested_at: Optional[datetime] = None

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    class Settings:
        name = "employees"
        indexes = [
            "role",
            "status",
        ]

    @field_validator('work_experience', 'education', mode='before')
    @classmethod
    def filter_nulls_from_arrays(cls, v):
        """
        Cleans up corrupted database arrays by removing null values
        when fetching the document from MongoDB.
        """
        if isinstance(v, list):
            return [item for item in v if item is not None]
        return v

# =====================================================================
# JOB APPLICATION MODEL
# =====================================================================

class ApplicationStatus(str, Enum):
    # --- Initial Stages ---
    PENDING = "pending"           # Initial state or draft
    APPLIED = "applied"           # Fresh application submitted by employee
    CANCELLED = "cancelled"       # Withdrawn by the employee
    
    # --- Review Process ---
    SHORTLISTED = "shortlisted"   # Employer expressed interest
    REJECTED = "rejected"         # Employer declined the application
    
    # --- Final Stages ---
    ACCEPTED = "accepted"         # Agreement reached
    HIRED = "hired"               # Officially hired for the role
    COMPLETED = "completed"       # Work finished; triggers rating/review flow

# FIXED: Renamed from 'Application' to 'JobApplication' to match your endpoint imports
class JobApplication(TranslatableDocument):
    job_id: PydanticObjectId
    employee_id: PydanticObjectId
    employer_id: Optional[PydanticObjectId] = None
    
    status: ApplicationStatus = ApplicationStatus.APPLIED
    
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "applications"