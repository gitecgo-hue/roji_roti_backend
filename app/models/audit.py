from datetime import datetime
from typing import Optional, Dict, Any
from beanie import Document, PydanticObjectId
from pydantic import Field

class AuditLog(Document):
    admin_id: PydanticObjectId
    admin_name: str
    action: str  # e.g., "VERIFY_GST", "SUSPEND_USER", "APPROVE_WORKER"
    target_type: str  # "employer" or "employee"
    target_id: PydanticObjectId
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "audit_logs"