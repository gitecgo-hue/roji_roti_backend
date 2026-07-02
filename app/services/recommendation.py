import logging
from typing import List
from app.models.job import Job
from app.models.employee import Employee

logger = logging.getLogger(__name__)

class RecommendationService:
    @staticmethod
    async def get_best_jobs_for_worker(worker: Employee, max_distance_km: int = 15, limit: int = 20) -> List[dict]:
        """
        Uses a MongoDB Aggregation Pipeline to score and rank jobs for a specific worker.
        """
        # 1. Safely extract worker data
        worker_lat = worker.current_location.coordinates[1]
        worker_lon = worker.current_location.coordinates[0]
        worker_category = getattr(worker, "category", "").lower()
        worker_exp = getattr(worker, "experience", 0)

        # 2. Build the Aggregation Pipeline
        pipeline = [
            # STAGE 1: GeoSpatial Search (MUST be the first stage). 
            # Filters out any job further than max_distance_km.
            {
                "$geoNear": {
                    "near": {
                        "type": "Point",
                        "coordinates": [worker_lon, worker_lat]
                    },
                    "distanceField": "distance_in_meters",
                    "maxDistance": max_distance_km * 1000,
                    "spherical": True,
                    "query": {"is_active": True} # Only look at active jobs
                }
            },
            
            # STAGE 2: The Scoring Engine
            {
                "$addFields": {
                    # Convert distance to KM for easier math
                    "distance_km": {"$divide": ["$distance_in_meters", 1000]},
                    
                    # Ensure job category is lowercase for comparison
                    "job_cat_lower": {"$toLower": "$category"},
                }
            },
            {
                "$addFields": {
                    "match_score": {
                        "$add": [
                            # A. CATEGORY SCORE (40 Points max)
                            {
                                "$cond": [{"$eq": ["$job_cat_lower", worker_category]}, 40, 0]
                            },
                            
                            # B. DISTANCE SCORE (40 Points max)
                            # Closer than 2km = 40 pts, 2-5km = 30 pts, 5-10km = 15 pts, further = 5 pts
                            {
                                "$switch": {
                                    "branches": [
                                        {"case": {"$lte": ["$distance_km", 2]}, "then": 40},
                                        {"case": {"$lte": ["$distance_km", 5]}, "then": 30},
                                        {"case": {"$lte": ["$distance_km", 10]}, "then": 15},
                                    ],
                                    "default": 5
                                }
                            },
                            
                            # C. EXPERIENCE SCORE (20 Points max)
                            # If job requires less or equal exp than worker has = 20 pts
                            {
                                "$cond": [
                                    {"$lte": [{"$ifNull": ["$experience_required", 0]}, worker_exp]}, 
                                    20, 
                                    0
                                ]
                            }
                        ]
                    }
                }
            },
            
            # STAGE 3: Filter out terrible matches (e.g., must have a score > 30 to even show up)
            {
                "$match": {
                    "match_score": {"$gte": 30}
                }
            },
            
            # STAGE 4: Rank them! Highest score first, then closest distance
            {
                "$sort": {
                    "match_score": -1,
                    "distance_in_meters": 1
                }
            },
            
            # STAGE 5: Limit the results
            {
                "$limit": limit
            }
        ]

        # 3. Execute the pipeline using Beanie's aggregate function
        try:
            # .aggregate() returns a cursor, we convert it to a list
            recommended_jobs = await Job.aggregate(pipeline).to_list()
            return recommended_jobs
        except Exception as e:
            logger.error(f"Failed to generate recommendations: {str(e)}")
            return []