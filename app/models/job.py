from beanie import Document, PydanticObjectId, Indexed
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime, timezone
from enum import Enum

class JobStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    PAUSED = "paused"
    CLOSED = "closed"

class JobType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    REMOTE = "remote"

class SalaryRange(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    currency: Optional[str] = "INR"

class LocationPoint(BaseModel):
    type: str = "Point"
    coordinates: List[float] # [longitude, latitude]

class Job(Document):
    # --- Core Identifiers ---
    employer_id: Indexed(str)
    title: str = Field(..., min_length=3)
    slug: Optional[Indexed(str)] = None
    
    # --- Descriptions ---
    short_description: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = None
    
    # --- Categorization & Location ---
    category: Optional[Indexed(str)] = None
    location_name: Optional[str] = None
    locations: Optional[List[str]] = [] # Array of location strings
    location_point: Optional[LocationPoint] = None # For geospatial searches
    is_pan_india: bool = False
    
    # --- Job Details ---
    job_type: Optional[JobType] = JobType.FULL_TIME
    salary_range: Optional[SalaryRange] = None
    required_experience: Optional[float] = Field(0.0, ge=0) # Minimum experience
    skills: Optional[List[str]] = []
    
    # --- Status & Timestamps ---
    is_urgent: bool = False
    status: JobStatus = JobStatus.DRAFT
    posted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # --- Analytics Counters (Optional but highly recommended) ---
    applicants_count: int = 0
    views_count: int = 0
    shortlisted_count: int = 0
    hires_count: int = 0

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    class Settings:
        name = "jobs"
        indexes = [
            "employer_id",
            "category",
            "status",
            [("location_point.coordinates", "2dsphere")]
        ]