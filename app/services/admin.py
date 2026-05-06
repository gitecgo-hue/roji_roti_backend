from app.models.employee import Employee
from app.models.employer import Employer
from app.models.job import Job

class AdminService:
    @staticmethod
    async def get_platform_stats():
        """
        Aggregates key metrics for the Roji Roti dashboard.
        """
        # 1. Basic Counts
        total_workers = await Employee.count()
        total_employers = await Employer.count()
        total_jobs = await Job.count()

        # 2. Category Popularity (Aggregation Pipeline)
        # Groups jobs by category and counts them
        category_stats = await Job.aggregate([
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5}
        ]).to_list()

        # 3. Location Distribution
        pan_india_jobs = await Job.find(Job.is_pan_india == True).count()

        return {
            "overview": {
                "workers": total_workers,
                "employers": total_employers,
                "active_jobs": total_jobs
            },
            "market_trends": {
                "top_categories": category_stats,
                "pan_india_coverage": pan_india_jobs
            }
        }