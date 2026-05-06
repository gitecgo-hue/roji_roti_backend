from beanie import Document
from pydantic import Field
from datetime import datetime
from pymongo import IndexModel, ASCENDING

class OTPVerification(Document):
    phone: str
    otp: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "otp_verifications"
        # Automatically delete the OTP record after 5 minutes (300 seconds)
        indexes = [
            IndexModel(
                [("created_at", ASCENDING)], 
                expireAfterSeconds=300
            )
        ]