from beanie import Document, Indexed
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List
from datetime import datetime
from enum import Enum
from pymongo import GEOSPHERE

class JobScope(str, Enum):
    """Defines if the job is local to a specific area or pan-India."""
    LOCAL = "local"
    INDIA = "india"

class GeoLocation(BaseModel):
    """Standard GeoJSON format required by MongoDB for spatial queries."""
    type: str = "Point"
    coordinates: List[float]  # [longitude, latitude]

class Job(Document):
    employer_id: str
    title: str
    category: str
    scope: str
    locations: List[str]
    coordinates: Optional[GeoLocation] = None
    salary: str
    description: str
    required_experience: int
    requirements: List[str] = []
    is_active: bool = True
    created_at: datetime = datetime.utcnow()

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True  # Allows using 'salary_range' in JSON but 'salary' in code
    )

class Settings:
    name = "jobs"
    indexes = [
        "employer_id", 
        "category", 
        "is_active",
        [("coordinates", "2dsphere")]
    ]