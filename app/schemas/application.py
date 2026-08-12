from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime

class ApplicantResponse(BaseModel):
    """Schema for returning an applicant's details to the employer"""
    application_id: str
    employee_id: str
    name: Optional[str] = None
    phone: str
    email: Optional[EmailStr] = None  # <-- Here is the email!
    status: str
    applied_at: datetime
    
    # You can also add resume_url or total_experience if you want the employer to see them!
    resume_url: Optional[str] = None
    total_experience: Optional[float] = None