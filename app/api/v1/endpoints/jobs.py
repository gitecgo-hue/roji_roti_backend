from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks 
from fastapi.responses import StreamingResponse, RedirectResponse 
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from bson import ObjectId
import pymongo

from app.models.job import Job, SalaryRange
from app.models.employer import Employer
from app.models.employee import GeoLocation, Employee
from app.schemas.job import JobCreateRequest, JobResponse
from app.api.dependencies import get_current_employer, get_current_employee
from app.services.subscriptions import SubscriptionService
from app.services.resumes import ResumeService 
from app.services.webhooks import WebhookService
from app.services.notifications import NotificationService 
from app.utils.geocoding import get_coordinates_from_name 
from beanie import PydanticObjectId

router = APIRouter()

# --- Pydantic Schemas (Local to Search) ---

class JobSearchQuery(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    place_name: Optional[str] = None # Added for name-based search
    radius_km: int = 10
    category: Optional[str] = None


# =====================================================================
# EMPLOYER: JOB MANAGEMENT
# =====================================================================

@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_data: JobCreateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Creates a new job posting for the logged-in employer.
    """
    # 1. Map the incoming Pydantic schema to the Beanie Database Model
    new_job = Job(
        employer_id=str(current_employer.id),
        title=job_data.title,
        short_description=job_data.short_description,
        description=job_data.description,
        category=job_data.category,
        location_name=job_data.location_name,
        locations=job_data.locations,
        is_pan_india=job_data.is_pan_india,
        job_type=job_data.job_type,
        required_experience=job_data.required_experience,
        skills=job_data.skills,
        is_urgent=job_data.is_urgent,
        status=job_data.status
    )

    # 2. Handle nested objects like Salary Range
    if job_data.salary_range:
        new_job.salary_range = SalaryRange(
            min=job_data.salary_range.min,
            max=job_data.salary_range.max,
            currency=job_data.salary_range.currency
        )

    # 3. If published immediately, set the posted_at time
    if new_job.status == "published":
        new_job.posted_at = datetime.now(timezone.utc)

    # 4. Save to Database
    await new_job.insert()

    # 5. Return response (FastAPI handles mapping the _id to 'id' automatically based on our response schema)
    response_dict = new_job.model_dump()
    response_dict["id"] = str(new_job.id)
    response_dict["employer_id"] = str(new_job.employer_id)

    return response_dict
    
# =====================================================================
# WORKER: JOB DISCOVERY & SEARCH
# =====================================================================

@router.get("/feed", response_model=dict)
async def get_worker_home_feed(
    lat: Optional[float] = None, 
    lon: Optional[float] = None, 
    radius_km: int = 25, 
    current_worker: Employee = Depends(get_current_employee)
):
    """
    Dynamically loads jobs for the worker based on their trade category and GPS location.
    """
    feed_items = {}
    
    # 1. Fetch National Level Jobs First
    feed_items["national_jobs"] = await Job.find(
        Job.is_pan_india == True, 
        Job.is_active == True,
        Job.category == current_worker.category 
    ).limit(10).to_list()

    # 2. If coordinates aren't provided, try to geocode the worker's home location_name
    if not lat or not lon:
        coords = await get_coordinates_from_name(current_worker.location_name)
        if coords:
            lon, lat = coords

    # 3. Geospatial Local Job Query
    if lat and lon:
        search_filter = {
            "is_active": True,
            "is_pan_india": False,
            "current_location": {
                "$near": {
                    "$geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "$maxDistance": radius_km * 1000
                }
            }
        }
        feed_items["local_jobs"] = await Job.find(search_filter).limit(20).to_list()
    else:
        # Fallback to string matching if geocoding fails
        feed_items["local_jobs"] = await Job.find(
            Job.location_name == current_worker.location_name,
            Job.is_active == True
        ).limit(20).to_list()

    return feed_items


@router.post("/search", response_model=List[JobResponse])
async def search_jobs(query: JobSearchQuery):
    """
    Advanced job searching supporting both raw text (location_name) and Geospatial radiuses.
    """
    lon, lat = query.lon, query.lat

    # 1. Name to Coordinates
    if query.place_name and not (lon and lat):
        coords = await get_coordinates_from_name(query.place_name)
        if coords:
            lon, lat = coords

    # 2. Execute the Search (Either Fallback or Geo-Search)
    if not (lon and lat):
        # Fallback: If no coordinates found, do a text search
        fallback = {"is_active": True, "$or": [{"is_pan_india": True}]}
        if query.place_name:
            fallback["$or"].append({"location_name": query.place_name})
            fallback["$or"].append({"locations": query.place_name})
        if query.category:
            fallback["category"] = query.category
        
        jobs = await Job.find(fallback).to_list()
    else:
        # Geo-Search: If we HAVE coordinates, run the spatial query
        geo_filter = {
            "current_location": {
                "$nearSphere": { 
                    "$geometry": {
                        "type": "Point", 
                        "coordinates": [lon, lat]
                    },
                    "$maxDistance": query.radius_km * 1000
                }
            },
            "is_active": True
        }
        if query.category:
            geo_filter["category"] = query.category
            
        jobs = await Job.find(geo_filter).to_list()

    # 3. Format the response
    formatted_jobs = []
    for job in jobs:
        formatted_jobs.append(
            JobResponse(
                id=job.id, 
                employer_id=job.employer_id,
                title=job.title,
                description=job.description,
                category=job.category,
                location_name=job.location_name,
                is_pan_india=job.is_pan_india,
                locations=job.locations,
                salary_range=job.salary_range,
                requirements=job.requirements,
                required_experience=job.required_experience,
                is_urgent=job.is_urgent,
                is_active=job.is_active,
                created_at=job.created_at
            )
        )
        
    return formatted_jobs


@router.get("/{job_id}", response_model=Job, status_code=status.HTTP_200_OK)
async def get_single_job(job_id: str):
    """
    Retrieves the complete details of a single job by its MongoDB ID.
    """
    # 1. Safely validate that the provided ID is a valid MongoDB ObjectId
    try:
        parsed_id = PydanticObjectId(job_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid Job ID format."
        )

    # 2. Fetch the job from the database
    job = await Job.get(parsed_id)

    # 3. Handle the case where the job doesn't exist
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Job not found."
        )

    # If you only want users to see active jobs, you can uncomment this:
    if not job.is_active:
        raise HTTPException(status_code=403, detail="This job is no longer active or has been closed.")

    return job

# =====================================================================
# DATABASE MAINTENANCE (ADMIN TOOLS)
# =====================================================================

from app.core.config import settings

@router.get("/fix-db-indexes")
async def force_create_indexes():
    """
    Force-rebuilds the Geospatial Index if MongoDB drops it.
    Updated to point to the new 'current_location' field!
    """
    from app.core.database import db # Grab the raw database connection
    
    collection = db.client[settings.DATABASE_NAME]["jobs"] # Use correct collection name
    
    try:
        # 1. Wipe old indexes just in case they are corrupted
        await collection.drop_indexes()
        
        # 2. Force create the 2dsphere index on current_location
        await collection.create_index([("current_location", pymongo.GEOSPHERE)])
        
        # 3. Retrieve the active indexes to prove it worked
        active_indexes = await collection.index_information()
        
        return {
            "message": "SUCCESS! The map index is officially built.",
            "indexes": active_indexes
        }
    except Exception as e:
        return {
            "message": "FAILED to build index. There is bad data in your database blocking it.",
            "error_details": str(e)
        }