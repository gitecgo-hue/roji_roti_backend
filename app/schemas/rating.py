from pydantic import BaseModel, Field
from typing import Optional

# Schema for creating a new rating for an employee
class RatingCreate(BaseModel):
    employee_id: str
    rating_value: int = Field(..., ge=1, le=5, description="Rating from 1 to 5 stars")
    comment: Optional[str] = Field(None, max_length=500)

# Schema for creating a new platform rating
class PlatformRatingCreate(BaseModel):
    """Schema for submitting a platform rating"""
    rating: int = Field(..., ge=1, le=5, description="Star rating from 1 to 5")
    feedback: Optional[str] = Field(None, max_length=1000, description="Optional written review")