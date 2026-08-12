from pydantic import BaseModel, ConfigDict, EmailStr
from typing import Optional, List

class PublicCandidateResponse(BaseModel):
        id: str
        role: str
        phone: str
        phone_verified: bool
        
        # Names & Bio
        name: Optional[str] = None
        summary: Optional[str] = None
        email: Optional[EmailStr] = None
        avatar: Optional[str] = None
        
        # Location
        location: Optional[dict] = None
        
        # Professional Details
        skills: Optional[list] = []
        work_experience: Optional[list] = []
        education: Optional[list] = []
        
        # Preferences & Settings
        expected_salary: Optional[float] = None
        availability: Optional[dict] = None
        preferences: Optional[dict] = None
        social_links: Optional[dict] = None  

        model_config = ConfigDict(from_attributes=True)