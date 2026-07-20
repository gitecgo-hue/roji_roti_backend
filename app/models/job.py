from beanie import Document, PydanticObjectId, Indexed
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from enum import Enum

# =====================================================================
# SUB-MODELS & ENUMS
# =====================================================================

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

class SalaryType(str, Enum):
    FIXED = "fixed"
    RANGE = "range"
    NEGOTIABLE = "negotiable"
    COMPETITIVE = "competitive"

class Visibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    UNLISTED = "unlisted"

class SalaryRange(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    currency: Optional[str] = "INR"

class LocationPoint(BaseModel):
    type: str = "Point"
    coordinates: List[float] # [longitude, latitude]

# =====================================================================
# MAIN JOB DOCUMENT
# =====================================================================

class Job(Document):
    # --- Core Identifiers ---
    employer_id: Indexed(PydanticObjectId)
    title: str = Field(..., min_length=3)
    slug: Optional[Indexed(str)] = None
    
    # --- Descriptions ---
    short_description: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = None
    requirements: Optional[str] = None
    responsibilities: Optional[str] = None
    
    # --- Categorization ---
    category: Optional[Indexed(str)] = None
    subcategory: Optional[str] = None
    job_type: Optional[JobType] = JobType.FULL_TIME
    skills: Optional[List[str]] = []
    
    # --- Location details ---
    location_name: Optional[str] = None
    location_point: Optional[LocationPoint] = None
    locations: Optional[List[str]] = [] # Multiple city names
    is_pan_india: bool = False
    
    # --- Compensation & Experience ---
    salary_range: Optional[SalaryRange] = None
    salary_type: Optional[SalaryType] = SalaryType.RANGE
    required_experience: Optional[float] = Field(0.0, ge=0) # In years
    
    # --- Status & Flags ---
    is_urgent: bool = False
    is_active: bool = True
    status: JobStatus = JobStatus.DRAFT
    visibility: Visibility = Visibility.PUBLIC
    
    # --- Timestamps ---
    posted_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    
    # --- Analytics & Counters ---
    applicants_count: int = 0
    views_count: int = 0
    shortlisted_count: int = 0
    hires_count: int = 0
    
    # --- Extensibility ---
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    class Settings:
        name = "jobs"
        indexes = [
            "employer_id",
            "category",
            "status",
            "slug",
            [("location_point.coordinates", "2dsphere")]
        ]