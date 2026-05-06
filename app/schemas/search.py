from pydantic import BaseModel, Field
from typing import Optional
from app.schemas.employee import LocationInput

class JobSearchQuery(BaseModel):
    location: LocationInput
    radius_km: int = Field(default=10, ge=1, le=100, description="Search radius in kilometers")
    category: Optional[str] = None