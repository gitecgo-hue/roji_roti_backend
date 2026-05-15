from datetime import datetime
from typing import Optional
from beanie import Document
from pydantic import EmailStr, Field

class Admin(Document):
    name: str
    email: EmailStr
    phone: str
    hashed_password: str
    
    # OTP fields for email fallback
    otp_code: Optional[str] = None
    otp_expires_at: Optional[datetime] = None
    last_otp_requested_at: Optional[datetime] = None
    
    is_active: bool = True
    role: str = "superadmin" # Can be superadmin, moderator, etc.
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "admins"