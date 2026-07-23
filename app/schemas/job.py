from pydantic import BaseModel, Field, model_validator
from typing import Optional, List
from datetime import datetime
from enum import Enum

# --- Model Imports ---
from app.models.job import JobStatus, JobType 

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
    EXPERIENCED_ONLY = "Experienced Only"
    FRESHER_ONLY = "Fresher Only"

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
    skills_preference: Optional[List[SkillPreference]] = Field(
        default_factory=list, 
        title="Skills preference"
    )
    
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
    short_description: Optional[str] = Field(None, max_length=300)
    
    is_pan_india: bool = False
    job_type: Optional[JobType] = JobType.FULL_TIME
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

# --- RESPONSE & UPDATE SCHEMAS ---
class JobResponse(JobCreateRequest):
    """Schema for sending the job data back to the frontend"""
    id: str
    employer_id: str
    slug: Optional[str] = None
    posted_at: Optional[datetime] = None
 
    class Config:
        from_attributes = True

class JobDashboardResponse(BaseModel):
    """Schema for the Employer Dashboard job cards"""
    id: str
    job_title: str
    location_name: Optional[str] = None
    status: JobStatus
    job_type: Optional[JobType] = None
    posted_at: Optional[datetime] = None
    applied_count: int = 0
    database_matches: int = 0
    
    class Config:
        from_attributes = True

class JobUpdateRequest(BaseModel):
    """Schema for updating an existing job post"""
    job_title: Optional[str] = None
    job_description: Optional[str] = None
    job_city: Optional[str] = None
    pay_type: Optional[PayType] = None
    min_fixed_salary: Optional[float] = None
    max_fixed_salary: Optional[float] = None
    average_incentive: Optional[float] = None
    total_experience_required: Optional[TotalExperience] = None
    skills_preference: Optional[List[SkillPreference]] = None
    job_type: Optional[JobType] = None 
    is_active: Optional[bool] = None