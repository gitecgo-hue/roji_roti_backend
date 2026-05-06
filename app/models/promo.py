from beanie import Document, Indexed
from pydantic import Field
from datetime import datetime
from typing import Optional

class PromoCode(Document):
    """Stores discount offers and promotional campaigns configured by the Admin."""
    code: Indexed(str, unique=True) # e.g., "DIWALI50" or "WELCOME20"
    discount_type: str # "percentage" or "flat"
    discount_value: float # e.g., 20.0 (for 20%) or 100.0 (for ₹100)
    
    # Validation Rules
    valid_from: datetime = Field(default_factory=datetime.utcnow)
    valid_until: Optional[datetime] = None
    max_usage_limit: int = 100 # Total times this code can be used platform-wide
    current_usage_count: int = 0
    is_active: bool = True

    class Settings:
        name = "promo_codes"