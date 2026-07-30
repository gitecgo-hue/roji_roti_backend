from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, EmailStr, Field, ConfigDict, HttpUrl, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, date
from enum import Enum

# =====================================================================
# SUB-MODELS & ENUMS
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
    coordinates: List[float]  # [longitude, latitude]
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None

class Skill(BaseModel):
    name: str
    level: Optional[str] = Field(None, description="beginner, intermediate, expert")
    years: Optional[float] = None

class WorkExperience(BaseModel):
    company: str
    title: str
    start_date: date
    end_date: Optional[date] = None # null = present
    description: Optional[str] = None
    location: Optional[GeoLocation] = None

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
    min: Optional[float] = None
    max: Optional[float] = None
    currency: Optional[str] = "INR"

class Availability(BaseModel):
    is_available: bool = True
    notice_period_days: Optional[int] = 0

class SocialLinks(BaseModel):
    linkedin: Optional[HttpUrl] = None
    github: Optional[HttpUrl] = None
    website: Optional[HttpUrl] = None

class Preferences(BaseModel):
    job_types: Optional[List[str]] = []
    locations: Optional[List[str]] = []
    remote_ok: Optional[bool] = False

class ProfileMetadata(BaseModel):
    profile_completion: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# =====================================================================
# MAIN EMPLOYEE DOCUMENT
# =====================================================================

class Employee(Document):
    # --- Core Identity ---
    role: Role = Role.EMPLOYEE
    phone: Indexed(str, unique=True)
    phone_verified: bool = False
    
    # --- Names & Bio ---
    title: Optional[str] = None
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
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
    skills: Optional[List[Skill]] = []
    work_experience: Optional[List[WorkExperience]] = []
    education: Optional[List[Education]] = []
    documents: Optional[List[ProfileDocument]] = []
    
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

class Application(Document):
    job_id: PydanticObjectId
    employee_id: PydanticObjectId
    employer_id: Optional[PydanticObjectId] = None
    
    status: ApplicationStatus = ApplicationStatus.APPLIED
    
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "applications"