from beanie import Document
from pydantic import Field, ConfigDict
from typing import Optional, List
from datetime import datetime

# Re-use the location schema from the employee model to keep your code DRY
from app.models.employee import GeoLocation 

class Job(Document):
    employer_id: str
    title: str
    description: str
    category: str
    
    # --- Location Details ---
    location_name: str
    is_pan_india: bool = False
    locations: List[str] = Field(default_factory=list)
    
    # Required for the Geospatial "$near" search to work!
    # Made Optional because Pan-India jobs might not have a specific GPS coordinate
    current_location: Optional[GeoLocation] = None
    
    # --- Compensation & Requirements ---
    salary_range: Optional[str] = None
    requirements: Optional[str] = None
    required_experience: int = 0
    
    # --- Status Flags ---
    is_urgent: bool = False
    is_active: bool = True
    
    # --- Timestamps ---
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True 
    )

    class Settings:
        name = "jobs"
        # Create a 2dsphere index so workers can search by radius, 
        # plus standard indexes for lightning-fast dashboard queries
        indexes = [
            "employer_id", 
            "category", 
            "is_active",
            [("current_location", "2dsphere")]
        ]