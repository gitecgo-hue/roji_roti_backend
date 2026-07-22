from datetime import datetime, timezone
from beanie import Document, PydanticObjectId
from pydantic import Field
from enum import Enum
from typing import Optional

class TransactionType(str, Enum):
    ADDED = "added"
    SPENT = "spent"
    EXPIRED = "expired"
    RETURNED = "returned"

class TransactionStatus(str, Enum):
    SUCCESS = "success"
    PENDING = "pending"
    FAILED = "failed"

class Transaction(Document):
    employer_id: str
    amount: int  # Positive for added/returned, negative for spent/expired
    transaction_type: TransactionType
    title: str  # e.g., "Coins Added", "Coins Spent"
    description: str # e.g., "Purchased", "Posted Job #229241423"
    status: TransactionStatus = TransactionStatus.SUCCESS
    
    # Optional reference ID if they spent coins on a specific job or candidate unlock
    reference_id: Optional[str] = None 
    
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "transactions"