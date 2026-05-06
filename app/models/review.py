from datetime import datetime
from beanie import Document, PydanticObjectId
from pydantic import Field

class Review(Document):
    employer_id: PydanticObjectId
    worker_id: PydanticObjectId
    rating: int = Field(ge=1, le=5)  # Restrict 1 to 5 stars
    comment: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "reviews"