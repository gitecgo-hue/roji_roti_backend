from beanie import PydanticObjectId
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

# =====================================================================
# LOCATION SCHEMAS
# =====================================================================

class LocationInput(BaseModel):
    latitude: float
    longitude: float


# =====================================================================
# JOB CREATION REQUEST
# =====================================================================

class JobCreateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=100, description="e.g., 'Need 2 Carpenters for Cabinet Work'")
    description: str = Field(..., min_length=10)
    category: str = Field(..., description="Must match one of the system categories")
    
    # Precise Geospatial Location (Required for Worker Proximity Feed)
    location: LocationInput
    location_name: str = Field(..., description="City or specific area name")
    
    # Broader Location Fallbacks (Kept from your original schema)
    is_pan_india: bool = False
    locations: List[str] = Field(default=[], description="List of additional cities if applicable")
    
    # Compensation & Details
    salary_range: Optional[str] = Field(default=None, description="E.g., '15000-20000', '15000/month'")
    requirements: Optional[str] = Field(default=None, description="Specific skills or tools required")
    required_experience: int = Field(default=0, ge=0, description="Years of experience required")
    
    is_urgent: bool = False


# =====================================================================
# JOB RESPONSE
# =====================================================================

class JobResponse(BaseModel):
    id: PydanticObjectId
    employer_id: str
    title: str
    description: str
    category: str
    
    location_name: str
    is_pan_india: bool
    locations: List[str]
    
    salary_range: Optional[str] = None
    requirements: Optional[str] = None
    required_experience: int
    is_urgent: bool
    is_active: bool
    
    created_at: datetime

    class Config:
        from_attributes = True