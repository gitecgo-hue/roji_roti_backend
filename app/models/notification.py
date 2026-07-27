from beanie import Document, Indexed
from pydantic import Field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

class NotificationType(str, Enum):
    # --- Employer Alerts ---
    JOB_POSTED = "job_posted"
    NEW_APPLICANT = "new_applicant"
    JOB_CLOSED = "job_closed"
    SUBSCRIPTION_ALERT = "subscription_alert"
    
    # --- Employee Alerts (NEW) ---
    APPLICATION_UPDATE = "application_update" 
    INTERVIEW_INVITE = "interview_invite"
    NEW_JOB_MATCH = "new_job_match"
    
    # --- Shared Alerts ---
    PROFILE_UPDATE = "profile_update"
    KYC_UPDATE = "kyc_update"
    SECURITY_LOGIN = "security_login"
    SYSTEM_ALERT = "system_alert"

class Notification(Document):
    user_id: Indexed(str)
    title: str
    message: str
    type: NotificationType
    related_entity_id: Optional[str] = None 
    is_read: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "notifications"