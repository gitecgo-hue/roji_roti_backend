from beanie import Document, Indexed
from pydantic import Field, ConfigDict
from typing import Optional
from datetime import datetime, timezone

class Rating(Document):
    """
    Stores feedback and star ratings provided by employers to employees.
    """
    # Indexed strings ensure fast lookups when calculating employee averages
    employee_id: Indexed(str)  # The employee being rated
    employer_id: Indexed(str)  # The employer giving the rating
    
    # Strictly enforced 1-5 star scale
    rating_value: int = Field(..., ge=1, le=5, description="Star rating from 1 to 5")
    
    # Feedback text for qualitative reviews
    comment: Optional[str] = Field(default=None, description="Optional feedback text")
    
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Configures Pydantic to handle Beanie's underlying types and field aliases
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True
    )

    class Settings:
        name = "ratings"  # Collection name in MongoDB

class PlatformRating(Document):
    user_id: str
    user_type: str  # e.g., "employer" or "employee"
    rating: int = Field(ge=1, le=5, description="Star rating from 1 to 5")
    feedback: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "platform_ratings"