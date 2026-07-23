import logging
from app.models.job import Job
from app.models.employee import Employee
from app.services.sms import SmsService

logger = logging.getLogger(__name__)

class NotificationService:
    @staticmethod
    async def broadcast_new_job(job_id: str):
        """
        Finds nearby, active, and matching employees and sends them an SMS.
        """
        # Fetch the freshly inserted job
        job = await Job.get(job_id)
        if not job:
            return

        # 1. Build the Search Query
        query = {
            "category": job.category,
            "is_approved": True,
            "availability_status": True # Respect the toggle!
        }

        # 2. Add Location Radius (if the job has GPS coordinates)
        if job.current_location:
            # Read from the updated current_location field instead!
            lon = job.current_location.coordinates[0]
            lat = job.current_location.coordinates[1]
            
            query["current_location"] = {
                "$nearSphere": {
                    "$geometry": {
                        "type": "Point",
                        "coordinates": [lon, lat] 
                    },
                    "$maxDistance": 10000 # 10 KM radius
                }
            }
        elif job.locations:
            # Fallback to text-based location match
            query["preferred_locations"] = {"$in": job.locations}

        # 3. Fetch matched employees (Limit to 50 to control SMS costs)
        matched_employees = await Employee.find(query).limit(50).to_list()
        
        if not matched_employees:
            logger.info(f"No available employees found for job {job_id}")
            return

        location_name = job.locations[0] if job.locations else "your area"

        # 4. Dispatch SMS
        for employee in matched_employees:
            await SmsService.send_job_alert(
                phone_number=employee.phone,
                category=job.category,
                location=location_name,
                salary=job.salary
            )