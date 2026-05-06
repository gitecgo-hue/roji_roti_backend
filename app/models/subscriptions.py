from beanie import Document, Indexed
from pydantic import Field, ConfigDict
from datetime import datetime
from typing import Optional

class Subscription(Document):
    """
    Tracks employer subscription plans and their monthly quota usage.
    Enforces a unique constraint per employer to prevent duplicate active plans.
    """
    
    # Unique index ensures one subscription record per employer
    employer_id: Indexed(str, unique=True)
    plan_type: str  # "free", "standard", "premium", "enterprise"
    
    # --- Status and Validity ---
    is_active: bool = True
    start_date: datetime = Field(default_factory=datetime.utcnow)
    expiry_date: datetime
    
    # --- Quota Tracking Fields ---
    contacts_checked: int = 0
    resumes_downloaded: int = 0
    jobs_posted: int = 0
    india_level_jobs_posted: int = 0  # Specifically for national-level job posts

    # Modern Pydantic V2 configuration
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True
    )

    class Settings:
        name = "subscriptions"  # Collection name in MongoDB