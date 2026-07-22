from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from app.models.transaction import TransactionType, TransactionStatus
from app.models.payment import PaymentStatus

class TransactionResponse(BaseModel):
    id: str
    title: str
    description: str
    amount: int
    transaction_type: TransactionType
    status: TransactionStatus
    created_at: datetime

    class Config:
        from_attributes = True

class PaymentResponse(BaseModel):
    id: str
    plan_name: str
    amount: float
    status: PaymentStatus
    applies_until: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True

class BillingProfileUpdateRequest(BaseModel):
    gstin: Optional[str] = None
    billing_address: Optional[str] = None