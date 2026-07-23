from datetime import datetime
from beanie import Document, PydanticObjectId
from pydantic import Field

class ContactUnlock(Document):
    employer_id: PydanticObjectId
    employee_id: PydanticObjectId
    unlocked_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "contact_unlocks"