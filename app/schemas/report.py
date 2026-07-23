from pydantic import BaseModel
from typing import Dict, Any

class DailyEmployeeReport(BaseModel):
    date: str
    total_registered_today: int
    approved_and_live_today: int

class ReferralReport(BaseModel):
    total_referred_employees: int
    referrals_by_category: Dict[str, int]

class SubscriptionReport(BaseModel):
    active_paid_subscriptions: int
    expiring_in_7_days: int

class ComprehensiveReport(BaseModel):
    daily_employees: DailyEmployeeReport
    referrals: ReferralReport
    subscriptions: SubscriptionReport