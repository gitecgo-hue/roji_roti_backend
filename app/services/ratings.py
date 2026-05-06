import logging
from bson import ObjectId
from app.models.rating import Rating
from app.models.employee import Employee
from app.models.employer import Employer

logger = logging.getLogger(__name__)

class RatingService:
    @staticmethod
    async def update_average_rating(user_id: str, user_type: str = "employee"):
        """
        Mathematical recalculation engine for the integrated feedback system.
        Calculates the new average and updates the Employee or Employer profile 
        for search ranking visibility[cite: 222].
        """
        try:
            # 1. Fetch all ratings submitted for this specific user
            if user_type == "employee":
                # Workers are rated by employers [cite: 214]
                ratings = await Rating.find(Rating.employee_id == user_id).to_list()
            else:
                # Framework in place if you ever allow workers to rate employers
                ratings = await Rating.find(Rating.employer_id == user_id).to_list()

            if not ratings:
                return

            # 2. Calculate the new mathematical average 
            total_stars = sum(r.rating_value for r in ratings)
            average_rating = round(total_stars / len(ratings), 1)

            # 3. Update the specific collection
            object_id = ObjectId(user_id)
            
            if user_type == "employee":
                worker = await Employee.get(object_id)
                if worker:
                    worker.rating = average_rating
                    await worker.save()
            else:
                employer = await Employer.get(object_id)
                if employer and hasattr(employer, 'rating'):
                    employer.rating = average_rating
                    await employer.save()
                    
        except Exception as e:
            logger.error(f"Failed to recalculate rating for {user_type} {user_id}: {e}")