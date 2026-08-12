from beanie import Document, Indexed
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict
from datetime import datetime, timezone
from enum import Enum
import pymongo

# --- Import Models ---
from app.models.base import TranslatableDocument

# --- ENUMS (Matches UI & System States) ---
class JobStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    UNDER_REVIEW = "under_review"
    EXPIRED = "expired"
    CLOSED = "closed"

class JobType(str, Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    TEMPORARY = "temporary"
    REMOTE = "remote"

class WorkLocationType(str, Enum):
    WORK_FROM_OFFICE = "Work From Office"
    WORK_FROM_HOME = "Work From Home"
    FIELD_JOB = "Field Job"

class PayType(str, Enum):
    FIXED_ONLY = "Fixed Only"
    FIXED_AND_INCENTIVE = "Fixed + Incentive"
    INCENTIVE_ONLY = "Incentive Only"

class MinimumEducation(str, Enum):
    TENTH_PASS = "10th pass"
    TWELFTH_PASS = "12th pass"
    ITI = "ITI"
    DIPLOMA = "Diploma"
    GRADUATE = "Graduate"
    POST_GRADUATE = "Post Graduate"

class TotalExperience(str, Enum):
    ANY = "Any"
    ZERO_TO_ONE = "0-1"
    ONE_TO_TWO = "1-2"
    TWO_TO_THREE = "2-3"
    THREE_TO_FIVE = "3-5"
    FIVE_TO_SEVEN = "5+"

class SkillPreference(str, Enum):
    FLEXIBLE_WORKING_HOURS = "Flexible Working Hours"
    WEEKLY_PAYOUT = "Weekly Payout"
    OVERTIME_PAY = "Overtime Pay"
    JOINING_BONUS = "Joining Bonus"
    HEALTH_INSURANCE = "Health Insurance"

class CommunicationPreference(str, Enum):
    YES_TO_MYSELF = "Yes, to myself"
    YES_TO_OTHER_RECRUITER = "Yes, to other recruiter"
    NO_WILL_CONTACT_FIRST = "No, I will contact candidates first"

# --- HELPER SCHEMAS ---
class GeoPoint(BaseModel):
    """The exact format MongoDB needs for Maps (GeoJSON Point)"""
    type: str = "Point"
    coordinates: List[float] # MUST be [longitude, latitude] in that order!

# --- BEANIE DATABASE MODEL ---
class Job(TranslatableDocument):
    # --- Core Identifiers ---
    employer_id: Indexed(str)
    job_title: str = Field(..., min_length=3)
    slug: Optional[Indexed(str)] = None
    
    # --- Categorization & Location ---
    job_category: Indexed(str)
    work_location_type: WorkLocationType
    job_city: str
    locations: Optional[List[str]] = []
    
    # The Geospatial Field for radius searches
    location_point: Optional[GeoPoint] = None
    is_pan_india: bool = False
    
    # --- Salary & Pay ---
    pay_type: PayType
    min_fixed_salary: Optional[float] = None
    max_fixed_salary: Optional[float] = None
    average_incentive: Optional[float] = None
    
    # --- Candidate Requirements ---
    minimum_education: MinimumEducation
    total_experience_required: TotalExperience
    skills_preference: List[SkillPreference] = []
    
    # --- Interview & Contact ---
    is_walk_in_interview: bool
    address: str
    communication_preferences: CommunicationPreference
    
    # --- Descriptions & Settings ---
    job_description: Optional[str] = None
    job_type: Optional[JobType] = JobType.FULL_TIME
    
    # --- Status & Timestamps ---
    is_urgent: bool = False
    is_active: bool = True
    status: JobStatus = JobStatus.DRAFT
    posted_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # --- Analytics Counters ---
    applicants_count: int = 0
    views_count: int = 0
    shortlisted_count: int = 0
    hires_count: int = 0

    translations: Optional[Dict[str, Dict[str, str]]] = {}

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    class Settings:
        name = "jobs"
        indexes = [
            "employer_id",
            "job_category",
            "status",
            # The Geospatial Map Index for rapid 5km/10km radius searching
            [("location_point", "2dsphere")]    
        ]