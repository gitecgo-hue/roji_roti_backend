from beanie import Document, Indexed
from pydantic import Field, ConfigDict
from datetime import datetime
from typing import Optional

class Payment(Document):
    """Tracks all subscription payments made by employers."""
    
    # --- Core Transaction Details ---
    employer_id: Indexed(str)
    plan_type: str  # e.g., "standard", "premium", "enterprise"
    amount: float
    currency: str = "INR"
    
    # --- Razorpay Specific Fields ---
    razorpay_order_id: Indexed(str)
    razorpay_payment_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
    
    # --- Status & Tracking ---
    status: str = Field(default="created")  # "created", "captured", "failed"
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Enables compatibility for complex Pydantic data types
    model_config = ConfigDict(arbitrary_types_allowed=True)

    class Settings:
        name = "payments"