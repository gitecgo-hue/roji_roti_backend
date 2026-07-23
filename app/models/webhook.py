from beanie import Document, Indexed
from pydantic import Field
from typing import List
from datetime import datetime

class WebhookSubscription(Document):
    """
    Stores partner URLs and the events they are subscribed to[cite: 354, 362].
    """
    partner_name: str
    target_url: str # The URL where the POST request is sent [cite: 361-362]
    # Events like: employee_registered, job_posted, employee_hired [cite: 355-360]
    events: List[str] 
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "webhook_subscriptions"