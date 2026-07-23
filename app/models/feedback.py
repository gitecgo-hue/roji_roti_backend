from beanie import Document
from pydantic import Field
from typing import Optional
from datetime import datetime, timezone

class PlatformFeedback(Document):
    user_id: str
    user_type: str  # "employer" or "employee"
    user_email: Optional[str] = None # Helpful so your support team can reply to them
    
    category: str = Field(description="e.g., 'bug_report', 'feature_request', 'general_feedback'")
    description: str = Field(max_length=2000)
    
    # Internal status for your admin team to track if it's been handled
    status: str = "open" # "open", "in_progress", "resolved"
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "platform_feedback"