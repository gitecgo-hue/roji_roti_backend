from pydantic import BaseModel, Field
from typing import Optional

class UnifiedLoginRequest(BaseModel):
    identifier: str = Field(..., description="Can be Email or Phone Number")
    password: Optional[str] = None
    otp_code: Optional[str] = None