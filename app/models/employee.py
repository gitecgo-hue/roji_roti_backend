from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, EmailStr, Field, ConfigDict, HttpUrl, field_validator
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
    experience_years: Optional[int] = None
    experience_months: Optional[int] = None
    currently_working_here: Optional[bool] = None

class Education(BaseModel):
    institution: str
    degree: Optional[str] = None
    field: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None

class ProfileDocument(BaseModel):
    type: str = Field(..., description="resume, id, cert")
    url: HttpUrl
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class SalaryExpectation(BaseModel):
    expected_salary: Optional[float] = Field(None, description="Expected salary amount")

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
class EmployeeProfileUpdate(BaseModel):
    # --- Basic Details ---
    full_name: Optional[str] = None
    job_title: Optional[str] = None
    location: Optional[str] = None
    about_you: Optional[str] = None

    # --- Preferences ---
    salary_expectation: Optional[SalaryExpectation] = None
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

class EducationUpdate(BaseModel):
    education_level: Optional[str] = None
    institute_school: Optional[str] = None
    year: Optional[str] = None

class WorkExperienceUpdate(BaseModel):
    job_title: Optional[str] = None
    job_role: Optional[str] = None
    company_name: Optional[str] = None
    experience_years: Optional[int] = None
    experience_months: Optional[int] = None
    currently_working_here: Optional[bool] = None

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
    avatar: Optional[str] = None
    
    # --- Location Data ---
    location_name: Optional[str] = None
    location: Optional[GeoLocation] = None

    # --- Saved Jobs & Applications ---
    saved_job_ids: List[str] = Field(default_factory=list)
    
    # --- Professional Details ---
    job_category: Optional[str] = None
    skills: Optional[List[Skill]] = Field(default_factory=list)
    work_experience: Optional[List[WorkExperience]] = Field(default_factory=list)
    education: Optional[List[Education]] = Field(default_factory=list)
    resume_url: Optional[str] = None
    languages: Optional[List[str]] = None
    
    # --- Preferences & Settings ---
    contact_visibility: ContactVisibility = ContactVisibility.RESTRICTED
    salary_expectation: Optional[SalaryExpectation] = None
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

class Application(TranslatableDocument):
    job_id: PydanticObjectId
    employee_id: PydanticObjectId
    employer_id: Optional[PydanticObjectId] = None
    
    status: ApplicationStatus = ApplicationStatus.APPLIED
    
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "applications"