from beanie import Document, Indexed
from pydantic import Field
from datetime import datetime, timezone
from typing import Optional

class OTP(Document):
    phone: Indexed(str)
    hashed_code: str
    user_type: str # "employee" or "employer"

    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Move the TTL index logic here. 
    # Wrap 'datetime' with Indexed to set the expiry.
    created_at: Indexed(datetime, expireAfterSeconds=300) = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    class Settings:
        name = "otps"
        # We don't need the 'indexes' list here for simple TTLs