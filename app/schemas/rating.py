from pydantic import BaseModel, Field
from typing import Optional

class RatingCreate(BaseModel):
    employee_id: str
    rating_value: int = Field(..., ge=1, le=5, description="Rating from 1 to 5 stars")
    comment: Optional[str] = Field(None, max_length=500)