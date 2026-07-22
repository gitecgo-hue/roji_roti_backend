from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

from app.schemas.employee import LocationInput

class JobSearchQuery(BaseModel):
    location: LocationInput
    radius_km: int = Field(default=10, ge=1, le=100, description="Search radius in kilometers")
    category: Optional[str] = None

class CandidateSearchRequest(BaseModel):
    """Payload received from the frontend Search Candidates form"""
    keywords: Optional[str] = None
    city: Optional[str] = None
    min_experience: Optional[float] = None
    max_experience: Optional[float] = None
    min_salary: Optional[float] = None
    max_salary: Optional[float] = None
    education_levels: Optional[List[str]] = None # e.g., ["10th pass", "Graduate"]

class SavedSearchCreate(BaseModel):
    title: str
    filters: CandidateSearchRequest

class SavedSearchResponse(BaseModel):
    id: str
    title: str
    filters: CandidateSearchRequest
    created_at: datetime

    class Config:
        from_attributes = True