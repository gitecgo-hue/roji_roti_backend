from beanie import Document, Indexed
from pydantic import Field, ConfigDict
from datetime import datetime, timezone
from typing import Optional
from enum import Enum

class PaymentStatus(str, Enum):
    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"
    CANCELLED = "cancelled"

class Payment(Document):
    """Tracks all subscription payments made by employers."""
    
    # --- Core Transaction Details ---
    employer_id: Indexed(str)
    plan_name: str  # e.g., "standard", "premium", "enterprise"
    amount: float
    currency: str = "INR"
    status: PaymentStatus

    # When this specific purchased plan expires
    applies_until: Optional[datetime] = None 

    # Stripe, Razorpay, or manual bank transfer reference
    gateway_transaction_id: Optional[str] = None 
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
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