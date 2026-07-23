from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from app.models.job import JobStatus, JobType 

class SalaryRangeInput(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    currency: Optional[str] = "INR"

class JobCreateRequest(BaseModel):
    """Schema for the frontend to send when creating a new job"""
    title: str = Field(..., min_length=3, description="Job Title")
    short_description: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = None
    
    category: Optional[str] = None
    location_name: Optional[str] = None
    locations: Optional[List[str]] = []
    is_pan_india: bool = False
    
    job_type: Optional[JobType] = JobType.FULL_TIME
    salary_range: Optional[SalaryRangeInput] = None
    required_experience: Optional[float] = Field(0.0, ge=0)
    skills: Optional[List[str]] = []
    
    is_urgent: bool = False
    status: JobStatus = JobStatus.DRAFT

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
    title: str
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
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    experience_required: Optional[float] = None
    skills_required: Optional[List[str]] = None
    job_type: Optional[str] = None # e.g., "Full-time", "Part-time"
    is_active: Optional[bool] = None # Allows the employer to pause/close the job