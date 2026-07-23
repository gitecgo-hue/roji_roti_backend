from datetime import datetime, timedelta, timezone
from app.models.employee import Employee
from app.models.subscriptions import Subscription

class ReportService:
    @staticmethod
    async def get_daily_employee_stats():
        """Calculates 'Employee going live every day' report."""
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Total raw registrations today
        total_today = await Employee.find(Employee.created_at >= today).count()
        
        # Employees who registered today AND are approved/visible
        live_today = await Employee.find(
            Employee.created_at >= today,
            Employee.is_approved == True,
            Employee.availability_status == True
        ).count()
        
        return {
            "date": today.strftime("%Y-%m-%d"),
            "total_registered_today": total_today,
            "approved_and_live_today": live_today
        }

    @staticmethod
    async def get_referral_stats():
        """Aggregates the 'Referred Employee' report' broken down by job category."""
        # 1. Total count
        total_referred = await Employee.find(Employee.referred_by_id != None).count()
        
        # 2. MongoDB Aggregation to group by category
        pipeline = [
            {"$match": {"referred_by_id": {"$ne": None}}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}}
        ]
        
        category_breakdown = await Employee.aggregate(pipeline).to_list()
        
        # Format the output for the frontend schema
        formatted_breakdown = {item["_id"]: item["count"] for item in category_breakdown}
        
        return {
            "total_referred_employees": total_referred,
            "referrals_by_category": formatted_breakdown
        }

    @staticmethod
    async def get_subscription_stats():
        """Tracks 'Upcoming renewals' and active revenue streams."""
        now = datetime.now(timezone.utc)
        next_week = now + timedelta(days=7)
        
        active = await Subscription.find(
            Subscription.expiry_date > now, 
            Subscription.plan_type != "free"
        ).count()
        
        expiring = await Subscription.find(
            Subscription.expiry_date > now, 
            Subscription.expiry_date <= next_week,
            Subscription.plan_type != "free"
        ).count()
        
        return {
            "active_paid_subscriptions": active,
            "expiring_in_7_days": expiring
        }