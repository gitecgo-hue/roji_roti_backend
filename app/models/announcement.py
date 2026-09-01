from beanie import Document
from pydantic import Field
from datetime import datetime, timezone
from typing import Optional

class Announcement(Document):
    title: str
    message: str
    send_sms: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str

    class Settings:
        name = "announcements"