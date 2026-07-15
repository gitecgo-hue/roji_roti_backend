from beanie import Document, PydanticObjectId, Indexed
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, List
from datetime import datetime, timezone
from pymongo import GEOSPHERE
from enum import Enum

# Standard GeoJSON format for MongoDB spatial queries
class GeoLocation(BaseModel):
    type: str = "Point"
    coordinates: List[float]  # [longitude, latitude]

# --- NEW: Nested model for the "Work Experience" section ---
class WorkExperience(BaseModel):
    job_title: str
    company_name: Optional[str] = None
    duration_months: Optional[int] = None
    description: Optional[str] = None

class Employee(Document):
    # --- Core Identity ---
    phone: Indexed(str, unique=True)
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    hashed_password: Optional[str] = None
    last_otp_requested_at: Optional[datetime] = None
    
    # --- Profile Header & Categories ---
    category: Optional[str] = None    
    trade_category: Optional[str] = None 
    preferred_roles: Optional[List[str]] = [] # Maps to "Looking for jobs in..."
    
    # --- Location Data (Integrated) ---
    location_name: Optional[str] = None
    current_location: Optional[GeoLocation] = None 
    preferred_locations: Optional[List[str]] = []
    
    # --- Basic Details ---
    age: Optional[int] = None
    gender: Optional[str] = None
    languages: Optional[List[str]] = ["Hindi"]
    education_level: Optional[str] = None
    current_salary: Optional[str] = None  # Maps to "Current/Last Salary"
    expected_salary: Optional[str] = None 
    
    # --- Professional Details ---
    skills: Optional[List[str]] = []
    experience: Optional[int] = None  # in years
    experience_years: Optional[int] = None
    work_experience: Optional[List[WorkExperience]] = [] # Maps to Work Experience list
    daily_rate: Optional[float] = None
    
    # --- Documentation & Media ---
    resume_url: Optional[str] = None
    photo_url: Optional[str] = None
    profile_picture_url: Optional[str] = None
    adhar_card_number: Optional[str] = None
    pan_card: Optional[str] = None
    
    # --- KYC Hybrid Verification ---
    kyc_status: str = Field(default="UNVERIFIED") # Can be: UNVERIFIED, PENDING_REVIEW, VERIFIED
    kyc_attempts: int = Field(default=0)
    kyc_document_url: Optional[str] = None # Where we store the blurry image for the admin to look at
    
    # --- Platform Status & Ratings ---
    is_approved: bool = Field(default=False) 
    is_active: bool = Field(default=True)
    is_available: bool = True
    availability_status: bool = Field(default=True) 
    rating: float = 0.0
    total_reviews: int = 0
    
    # --- Tracking & Referrals ---
    referred_by_id: Optional[str] = None 
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True
    )

    class Settings:
        name = "employees"
        indexes = [
            "phone", 
            "email", 
            "category",
            [("current_location", "2dsphere")] 
        ]


# --- Job Application Model ---
class ApplicationStatus(str, Enum):
    # --- Initial Stages ---
    PENDING = "pending"           # Initial state or draft
    APPLIED = "applied"           # Fresh application submitted by worker
    CANCELLED = "cancelled"       # Withdrawn by the worker
    
    # --- Review Process ---
    SHORTLISTED = "shortlisted"   # Employer expressed interest
    REJECTED = "rejected"         # Employer declined the application
    
    # --- Final Stages ---
    ACCEPTED = "accepted"         # Agreement reached
    HIRED = "hired"               # Officially hired for the role
    COMPLETED = "completed"       # Work finished; triggers rating/review flow

class Application(Document):
    # Using PydanticObjectId handles the conversion between String and ObjectId automatically
    job_id: PydanticObjectId
    employee_id: PydanticObjectId
    employer_id: Optional[PydanticObjectId] = None
    
    # Set the default using the Enum class
    status: ApplicationStatus = ApplicationStatus.APPLIED
    
    # Use timezone-aware UTC for Python 3.13+ compatibility
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        # This tells Beanie exactly which collection to use
        name = "applications"