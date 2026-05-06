from datetime import datetime, timezone
from beanie import Document, PydanticObjectId
from pydantic import Field

class Transaction(Document):
    user_id: PydanticObjectId
    user_type: str  # "employer" or "employee"
    amount: float
    currency: str = "INR"
    status: str  # "success", "failed", "pending"
    razorpay_payment_id: str
    razorpay_order_id: str
    package_name: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "transactions"