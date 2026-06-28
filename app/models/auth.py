from beanie import Document, Indexed
from pydantic import Field
from datetime import datetime, timezone
from typing import Optional

class OTP(Document):
    # Indexed for fast lookups during login
    phone: Indexed(str) 
    
    # Optional so we can clear it (set to None) after a successful login
    hashed_code: Optional[str] = None  
    
    user_type: str # "employee", "employer", or "admin"
    
    # --- Abuse Prevention Fields ---
    daily_count: int = 0
    last_request_date: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Standard creation timestamp (CRITICAL: Removed TTL index so daily_count persists!)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    session_id: Optional[str] = None
    delivery_status: str = "PENDING"

    class Settings:
        name = "otps"


class TokenBlacklist(Document):
    jti: Indexed(str, unique=True)
    
    # TTL Index: MongoDB will automatically delete this document when it expires!
    expires_at: Indexed(datetime, expireAfterSeconds=0)

    class Settings:
        name = "token_blacklist"