from pydantic import BaseModel, Field
from typing import Optional

class FeedbackCreateRequest(BaseModel):
    """Schema for submitting general platform feedback or bug reports"""
    category: str = Field(..., description="E.g., bug_report, feature_request, general")
    description: str = Field(..., min_length=10, max_length=2000, description="Detailed explanation")