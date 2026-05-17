from pydantic import BaseModel, Field
from typing import Optional

class UnifiedLoginRequest(BaseModel):
    identifier: str = Field(..., description="Mobile number (Primary) or Email (Secondary)")
    otp_code: str = Field(..., description="6-digit OTP code required for login")