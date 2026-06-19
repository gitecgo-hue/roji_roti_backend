import re
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from beanie import PydanticObjectId

# --- Core Utility Schemas ---

class LocationInput(BaseModel):
    """Handles latitude and longitude for GPS-based job filtering."""
    latitude: float
    longitude: float

# --- Worker Profile & Status Schemas ---

class AvailabilityUpdate(BaseModel):
    """
    Schema for the worker availability toggle.
    True: Available for jobs (Green).
    False: Not available (Red).
    """
    is_available: bool

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
    name: str = Field(..., min_length=2, max_length=50, description="Worker name (Required)")
    category: str = Field(..., description="Job Category (E.g., Driver, House Help)")
    password: str = Field(..., min_length=6, description="Password for account security (Required)")    
    
    # Location data for proximity-based matching
    location_name: str = Field(..., description="Human-readable location name (Required)")
    location: LocationInput
    preferred_locations: List[str] = Field(default=[], description="Multiple locations selection")
    
    # Profile details
    experience: int = Field(default=0, ge=0, description="Years of experience")
    languages: List[str] = Field(default=["Hindi"], description="Supported languages")
    
    # Integrated expected_salary as str for maximum flexibility
    expected_salary: Optional[str] = Field(None, description="Worker's salary expectation")
    gender: Optional[str] = Field(None, description="Gender selection")
    email: Optional[EmailStr] = Field(None, description="Optional email address")
    
    # Referral and tracking
    referred_by_id: Optional[str] = None

class EmployeeResponse(BaseModel):
    """
    Standard data returned to the platform for worker profiles.
    """
    # Let Beanie handle the ObjectId-to-String conversion
    id: PydanticObjectId
    
    phone: str
    
    # Optional fields to allow skeleton profiles to be read without crashing
    name: Optional[str] = None
    category: Optional[str] = None
    location_name: Optional[str] = None
    
    # Made optional with defaults just in case the skeleton profile lacks them
    availability_status: Optional[bool] = False 
    rating: float = Field(default=0.0, description="1-5 star aggregate rating")
    created_at: Optional[datetime] = None

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )

class EmployeeDashboardResponse(BaseModel):
    """
    Schema for the worker-facing dashboard.
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