from datetime import datetime, timezone
from enum import Enum
from beanie import Document, PydanticObjectId
from pydantic import Field, field_validator

class ApplicationStatus(str, Enum):
    PENDING = "pending"
    APPLIED = "applied"
    REVIEWED = "reviewed"
    SHORTLISTED = "shortlisted"
    REJECTED = "rejected"
    HIRED = "hired"
    COMPLETED = "completed"

class JobApplication(Document):
    # Using PydanticObjectId is the "Pro" way for Beanie to handle MongoDB _id fields
    job_id: PydanticObjectId 
    employee_id: PydanticObjectId
    employer_id: PydanticObjectId 
    
    # Enums prevent typos in your code later
    status: ApplicationStatus
    
    # Timezone-aware datetimes are safer than utcnow()
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator('status', mode='before')
    @classmethod
    def lowercase_status(cls, value):
        if isinstance(value, str):
            return value.lower()
        return value

    class Settings:
        name = "job_applications"