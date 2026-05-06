from beanie import PydanticObjectId
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from app.schemas.employee import LocationInput  # Reusing our lat/lng schema

class JobCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=100)
    category: str = Field(..., description="E.g., Delivery, Construction, Driver")
    
    # Location logic
    is_pan_india: bool = False
    locations: List[str] = Field(default=[], description="List of cities if not pan-India")
    job_coordinates: Optional[LocationInput] = Field(
        default=None, 
        description="Required for 5/10/25km radius searches if not pan-India"
    )
    
    salary: str = Field(..., description="E.g., '15000-20000', '15000'")
    description: str = Field(..., min_length=10)
    required_experience: int = Field(default=0, ge=0, description="Years of experience")

class JobResponse(BaseModel):
    id: PydanticObjectId
    title: str
    category: str
    is_pan_india: bool
    locations: List[str]
    salary: str
    description: str
    required_experience: int
    created_at: datetime
    employer_id: str

    class Config:
        from_attributes = True