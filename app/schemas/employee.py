import re
from typing import List, Optional
from datetime import date, datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from beanie import PydanticObjectId

# --- Main Profile Completion Request ---
class CompleteEmployeeProfileRequest(BaseModel):
    # 1. Header / Category Details (Looking for jobs in...)
    preferred_roles: Optional[List[str]] = Field(default=[], description="List of job roles they want")
    
    # 2. Basic Details
    current_salary: Optional[str] = Field(None, description="E.g., '14800 per month'")
    email: Optional[EmailStr] = None
    age: Optional[int] = None
    gender: Optional[str] = Field(None, description="Male, Female, Other")
    languages: Optional[List[str]] = Field(default=[], description="E.g., ['English', 'Hindi']")
    education_level: Optional[str] = Field(None, description="E.g., 'Graduate', '12th Pass'")
    
    # 3. Professional Details
    skills: Optional[List[SkillInput]] = []
    work_experience: Optional[List[WorkExperienceInput]] = []
    
    # 4. Location (Usually required for the map matching)
    location_name: Optional[str] = None

# --- Employee Profile Update Schemas ---
class EmployeeProfileUpdate(BaseModel):
    """Unified schema for updating the employee's full profile"""
    
    # --- Personal Details ---
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    location_name: Optional[str] = None 
    
    # --- Professional Details ---
    title: Optional[str] = None 
    skills: Optional[List[str]] = None
    total_experience: Optional[float] = None 
    expected_salary: Optional[float] = None
    education: Optional[str] = None 
    resume_url: Optional[str] = None

# --- location Input for Job Search ---
class LocationInput(BaseModel):
    """Handles latitude and longitude for GPS-based job filtering."""
    latitude: float
    longitude: float

# --- Employee Profile & Status Schemas ---

class AvailabilityUpdate(BaseModel):
    """
    Schema for the employee availability toggle.
    True: Available for jobs (Green).
    False: Not available (Red).
    """
    is_available: bool

# --- Employee KYC Update Schema ---
class EmployeeKYCUpdate(BaseModel):
    """
    Schema for updating sensitive KYC documents after registration.
    """
    aadhar_number: Optional[str] = Field(
        default=None, 
        min_length=12, 
        max_length=12,
        pattern=r"^\d{12}$", 
        description="12-digit Aadhaar Number"
    )
    pan_number: Optional[str] = Field(
        default=None, 
        min_length=10, 
        max_length=10,
        pattern=r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$", 
        description="Standard 10-character PAN format"
    )

# --- Registration & Response Schemas ---
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
    experience: int = Field(default=0, ge=0, description="Years of experience")
    languages: List[str] = Field(default=["Hindi"], description="Supported languages")
    
    # Integrated expected_salary as str for maximum flexibility
    expected_salary: Optional[str] = Field(None, description="Employee's salary expectation")
    gender: Optional[str] = Field(None, description="Gender selection")
    email: Optional[EmailStr] = Field(None, description="Optional email address")
    
    # Referral and tracking
    referred_by_id: Optional[str] = None

# --- Employee Response Schemas ---
class EmployeeResponse(BaseModel):
    id: str
    role: str
    phone: str
    phone_verified: bool
    
    # Names & Bio
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: Optional[str] = None
    summary: Optional[str] = None
    email: Optional[EmailStr] = None
    avatar: Optional[str] = None
    
    # Location
    location: Optional[dict] = None
    
    # Professional Details (Auto-serialized by FastAPI)
    skills: Optional[list] = []
    work_experience: Optional[list] = []
    education: Optional[list] = []
    documents: Optional[list] = []
    
    # Preferences & Settings
    contact_visibility: Optional[str] = None
    salary_expectation: Optional[dict] = None
    availability: Optional[dict] = None
    preferences: Optional[dict] = None
    social_links: Optional[dict] = None
    
    # System & Status
    metadata: Optional[dict] = None
    status: Optional[str] = None
    verified_by_admin: bool = False

# --- Employee Dashboard Response Schema ---
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

# --- Employee Work Experience & Skills Input Schemas ---
class WorkExperienceInput(BaseModel):
    company: str = Field(..., description="Name of the company")
    title: str = Field(..., description="Job title or role")
    start_date: date = Field(..., description="Format: YYYY-MM-DD")
    end_date: Optional[date] = Field(None, description="Format: YYYY-MM-DD. Null means currently working here.")
    description: Optional[str] = None

class SkillInput(BaseModel):
    name: str
    level: Optional[str] = Field(None, description="beginner, intermediate, expert")
    years: Optional[float] = None

# --- Employee Applied Job Response Schema ---
class AppliedJobResponse(BaseModel):
    """Schema for returning a job the employee has applied to"""
    application_id: str
    job_id: str
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    status: str  # e.g., "pending", "shortlisted", "rejected"
    applied_at: datetime

# --- Employee Saved Job Response Schema ---
class SavedJobResponse(BaseModel):
    """Schema for returning a saved job to the employee"""
    job_id: str
    job_title: Optional[str] = None
    company_name: Optional[str] = None
    location: Optional[str] = None
    expected_salary: Optional[float] = None 