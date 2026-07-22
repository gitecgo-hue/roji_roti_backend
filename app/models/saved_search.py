from beanie import Document
from pydantic import Field
from datetime import datetime, timezone
from typing import Dict, Any

class SavedSearch(Document):
    employer_id: str
    title: str
    # We store the search payload as a dictionary so it's flexible
    filters: Dict[str, Any] 
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "saved_searches" # MongoDB collection name