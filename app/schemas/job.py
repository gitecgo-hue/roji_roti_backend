from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from beanie import PydanticObjectId
from typing import Optional, List
from datetime import datetime
from enum import Enum

# --- Model Imports ---
from app.models.job import (
    JobStatus, 
    JobTypeEnum, 
    WorkLocationType, 
    PayType, 
    MinimumEducation, 
    TotalExperience, 
    SkillPreference, 
    CommunicationPreference
)

# --- ENUMS & UTILITY SCHEMAS ---
class SalaryRangeInput(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    currency: Optional[str] = "INR"

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

# --- MAIN REQUEST SCHEMA ---
class JobCreateRequest(BaseModel):
    """Schema for the frontend to send when creating a new job"""
    # --- Basic Details ---
    job_title: str = Field(..., min_length=3, title="Job title / Designation")
    job_category: str = Field(..., title="Job category")
    work_location_type: WorkLocationType = Field(..., title="Work location type")
    job_city: str = Field(..., title="Job city")
    
    # --- Salary & Pay ---
    pay_type: PayType = Field(..., title="What is the pay type?")
    min_fixed_salary: Optional[float] = Field(None, title="Minimum fixed salary/month")
    max_fixed_salary: Optional[float] = Field(None, title="Maximum fixed salary/month")
    average_incentive: Optional[float] = Field(None, title="Average incentive/month")
    
    # --- Candidate Requirements ---
    minimum_education: MinimumEducation = Field(..., title="Minimum education")
    total_experience_required: TotalExperience = Field(..., title="Total experience required")
    skills_preference: Optional[List[str]] = []
    
    # --- Interview & Contact ---
    is_walk_in_interview: bool = Field(
        ..., 
        title="Is this a walk-in interview?",
        description="True for 'Yes', False for 'No'"
    )
    address: str = Field(
        ..., 
        title="Address", 
        description="Serves as Walk-in address or Company address based on is_walk_in_interview"
    )
    communication_preferences: CommunicationPreference = Field(..., title="Communication preferences")
    
    # --- Descriptions & Backend Settings ---
    job_description: Optional[str] = Field(None, title="Job description")
    
    is_pan_india: bool = False
    job_type: Optional[JobTypeEnum] = None    
    is_urgent: bool = False
    status: JobStatus = JobStatus.DRAFT

    # --- Conditional Validation Logic ---
    @model_validator(mode='after')
    def validate_pay_fields(self) -> 'JobCreateRequest':
        """
        Dynamically validates the required fields based on the selected pay_type,
        ensuring data integrity matches the frontend UI behavior.
        """
        # Rule 1: Fixed Salary Checks
        if self.pay_type in [PayType.FIXED_ONLY, PayType.FIXED_AND_INCENTIVE]:
            if self.min_fixed_salary is None:
                raise ValueError("Minimum fixed salary is required for this pay type.")
            if self.max_fixed_salary is None:
                raise ValueError("Maximum fixed salary is required for this pay type.")
            if self.min_fixed_salary > self.max_fixed_salary:
                raise ValueError("Minimum fixed salary cannot be greater than the maximum.")

        # Rule 2: Incentive Checks
        if self.pay_type in [PayType.FIXED_AND_INCENTIVE, PayType.INCENTIVE_ONLY]:
            if self.average_incentive is None:
                raise ValueError("Average incentive is required for this pay type.")

        # Rule 3: Data Cleanup (Safety Measure)
        if self.pay_type == PayType.FIXED_ONLY:
            self.average_incentive = None
            
        if self.pay_type == PayType.INCENTIVE_ONLY:
            self.min_fixed_salary = None
            self.max_fixed_salary = None

        return self

    @field_validator('job_type', mode='before')
    @classmethod
    def normalize_job_type(cls, v):
        if isinstance(v, str):
            # Converts "Full-Time", "FULL_TIME", or "Full time" -> "full_time"
            return v.lower().replace("-", "_").replace(" ", "_")
        return v

# --- RESPONSE & UPDATE SCHEMAS ---
class JobResponse(JobCreateRequest):
    """Schema for sending the job data back to the frontend"""
    id: PydanticObjectId
    employer_id: str
    slug: Optional[str] = None
    posted_at: Optional[datetime] = None
    applicants_count: Optional[int] = 0
    shortlisted_count: Optional[int] = 0
    hires_count: Optional[int] = 0
    views_count: Optional[int] = 0
    created_at: datetime
 
    model_config = ConfigDict(from_attributes=True)

class JobDashboardResponse(BaseModel):
    """Schema for the Employer Dashboard job cards"""
    id: PydanticObjectId
    job_title: str
    location_name: Optional[str] = None
    status: JobStatus
    job_type: Optional[JobTypeEnum] = None
    posted_at: Optional[datetime] = None
    applied_count: int = 0
    database_matches: int = 0
    
    model_config = ConfigDict(from_attributes=True)

class JobUpdateRequest(BaseModel):
    """Schema for updating an existing job post. All fields are optional."""
    
    # --- Basic Details ---
    job_title: Optional[str] = Field(None, min_length=3, title="Job title / Designation")
    job_category: Optional[str] = Field(None, title="Job category")
    work_location_type: Optional[WorkLocationType] = Field(None, title="Work location type")
    job_city: Optional[str] = Field(None, title="Job city")
    
    # --- Salary & Pay ---
    pay_type: Optional[PayType] = Field(None, title="What is the pay type?")
    min_fixed_salary: Optional[float] = Field(None, title="Minimum fixed salary/month")
    max_fixed_salary: Optional[float] = Field(None, title="Maximum fixed salary/month")
    average_incentive: Optional[float] = Field(None, title="Average incentive/month")
    
    # --- Candidate Requirements ---
    minimum_education: Optional[MinimumEducation] = Field(None, title="Minimum education")
    total_experience_required: Optional[TotalExperience] = Field(None, title="Total experience required")
    skills_preference: Optional[List[str]] = None    

    # --- Interview & Contact ---
    is_walk_in_interview: Optional[bool] = Field(None, title="Is this a walk-in interview?")
    address: Optional[str] = Field(None, title="Address")
    communication_preferences: Optional[CommunicationPreference] = Field(None, title="Communication preferences")
    
    # --- Descriptions & Backend Settings ---
    job_description: Optional[str] = Field(None, title="Job description")
    
    is_pan_india: Optional[bool] = None
    job_type: Optional[JobTypeEnum] = None
    is_urgent: Optional[bool] = None
    status: Optional[JobStatus] = None
    is_active: Optional[bool] = None  # Specific to updates (e.g., pausing a job)

    # ==========================================
    # CONDITIONAL VALIDATION FOR UPDATES
    # ==========================================
    @model_validator(mode='after')
    def validate_pay_fields(self) -> 'JobUpdateRequest':
        """
        Validates salary logic only if the frontend is attempting to change the pay_type.
        """
        # If pay_type is not being updated, skip validation
        if self.pay_type is None:
            return self

        # Rule 1: Fixed Salary Checks
        if self.pay_type in [PayType.FIXED_ONLY, PayType.FIXED_AND_INCENTIVE]:
            if self.min_fixed_salary is None or self.max_fixed_salary is None:
                raise ValueError("When updating to a fixed pay type, you must provide both min and max salary.")
            if self.min_fixed_salary > self.max_fixed_salary:
                raise ValueError("Minimum fixed salary cannot be greater than the maximum.")

        # Rule 2: Incentive Checks
        if self.pay_type in [PayType.FIXED_AND_INCENTIVE, PayType.INCENTIVE_ONLY]:
            if self.average_incentive is None:
                raise ValueError("When updating to an incentive pay type, average incentive must be provided.")

        # Rule 3: Data Cleanup (Safety Measure)
        if self.pay_type == PayType.FIXED_ONLY:
            self.average_incentive = None
            
        if self.pay_type == PayType.INCENTIVE_ONLY:
            self.min_fixed_salary = None
            self.max_fixed_salary = None

        return self

    @field_validator('job_type', mode='before')
    @classmethod
    def normalize_job_type(cls, v):
        if isinstance(v, str):
            return v.lower().replace("-", "_").replace(" ", "_")
        return v

class LocationInput(BaseModel):
    address_text: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None