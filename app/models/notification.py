from beanie import Document
from pydantic import Field
from datetime import datetime
from typing import Optional
from beanie import PydanticObjectId

class Notification(Document):
    user_id: PydanticObjectId
    title: str
    message: str
    is_read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "notifications"