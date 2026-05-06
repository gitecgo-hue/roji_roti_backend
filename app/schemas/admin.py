from pydantic import BaseModel
from typing import Dict, List
from app.schemas.employer import EmployerResponse
from app.schemas.employee import EmployeeResponse

class AdminDashboardStats(BaseModel):
    total_employers: int
    total_workers: int
    active_jobs: int
    pending_verifications: int
    # Breakdown of revenue by plan type
    revenue_stats: Dict[str, float]