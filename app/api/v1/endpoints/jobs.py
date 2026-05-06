# --- Updated app/api/v1/endpoints/jobs.py ---

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks 
from fastapi.responses import StreamingResponse, RedirectResponse 
from pydantic import BaseModel, Field
from datetime import datetime
from bson import ObjectId

from app.models.job import Job, JobScope, GeoLocation
from app.models.employer import Employer
from app.models.employee import Employee
from app.api.dependencies import get_current_employer, get_current_employee
from app.services.subscriptions import SubscriptionService
from app.services.resumes import ResumeService 
from app.services.webhooks import WebhookService
from app.services.notifications import NotificationService 
from app.utils.geocoding import get_coordinates_from_name # Integrated Utility
from beanie import PydanticObjectId

router = APIRouter()

# --- Pydantic Schemas ---

class JobCreate(BaseModel):
    title: str
    category: str
    scope: JobScope = JobScope.LOCAL
    location_name: str # The employer just types "Vijay Nagar"
    salary: str
    description: str
    required_experience: int
    requirements: List[str] = []
    
class JobSearchQuery(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    place_name: Optional[str] = None # Added for name-based search
    radius_km: int = 10
    category: Optional[str] = None

class JobResponse(BaseModel):
    id: str
    employer_id: str
    title: str
    category: str
    scope: JobScope
    locations: List[str]
    salary: str
    description: str
    required_experience: int
    created_at: datetime

# --- Employer: Job Management ---

@router.post("/create", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_in: JobCreate,
    background_tasks: BackgroundTasks,
    current_employer: Employer = Depends(get_current_employer)
):
    # 1. Quota Check
    is_india_level = job_in.scope == JobScope.INDIA
    await SubscriptionService.check_quota(
        employer_id=str(current_employer.id), 
        action_type="post_job", 
        is_india_level=is_india_level
    )

    # 2. Automatic Geocoding (Name -> Coordinates)
    # We take the location_name and convert it to lon/lat for MongoDB
    lat, lon = None, None
    coords = await get_coordinates_from_name(job_in.location_name)
    
    if coords:
        lon, lat = coords
    else:
        # Optional: If geocoding fails, you can decide to stop or continue
        # For local jobs, we usually want to ensure coordinates exist.
        if not is_india_level:
            raise HTTPException(
                status_code=400, 
                detail=f"Could not find coordinates for '{job_in.location_name}'. Please try a more specific area name."
            )

    # 3. Prepare GeoJSON for MongoDB Indexing
    geo_location = None
    if lon and lat:
        geo_location = GeoLocation(type="Point", coordinates=[lon, lat])

    # 4. Save to Database
    new_job = Job(
        employer_id=str(current_employer.id),
        title=job_in.title,
        category=job_in.category,
        scope=job_in.scope,
        locations=[job_in.location_name], # Store the name in the list
        coordinates=geo_location,         # Store the math for the map
        salary=job_in.salary,
        description=job_in.description,
        required_experience=job_in.required_experience,
        requirements=job_in.requirements 
    )
    await new_job.insert()
    
    # 5. Usage Tracking & Background Notification
    await SubscriptionService.track_usage(str(current_employer.id), "post_job")
    background_tasks.add_task(NotificationService.broadcast_new_job, str(new_job.id))
    
    return {
        "message": "Job posted successfully using location name!", 
        "job_id": str(new_job.id),
        "geocoded_address": job_in.location_name,
        "coordinates_saved": f"{lon}, {lat}" if lon else "None (India Level)"
    }


# --- Worker: Job Discovery & Search ---

@router.get("/feed", response_model=dict)
async def get_worker_home_feed(
    lat: Optional[float] = None, 
    lon: Optional[float] = None, 
    radius_km: int = 25, 
    current_worker: Employee = Depends(get_current_employee)
):
    feed_items = {}
    feed_items["national_jobs"] = await Job.find(
        Job.scope == JobScope.INDIA, 
        Job.is_active == True,
        Job.category == current_worker.category 
    ).limit(10).to_list()

    # If coordinates aren't provided, try to geocode the worker's home location_name
    if not lat or not lon:
        coords = await get_coordinates_from_name(current_worker.location_name)
        if coords:
            lon, lat = coords

    if lat and lon:
        search_filter = {
            "is_active": True,
            "scope": JobScope.LOCAL.value,
            "coordinates": {
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
            Job.locations == current_worker.location_name,
            Job.is_active == True
        ).limit(20).to_list()

    return feed_items

# --- Job Search with Flexible Location Input ---

@router.post("/search", response_model=List[JobResponse])
async def search_jobs(query: JobSearchQuery):
    lon, lat = query.lon, query.lat

    # 1. Name to Coordinates
    if query.place_name and not (lon and lat):
        coords = await get_coordinates_from_name(query.place_name)
        if coords:
            lon, lat = coords

    # 2. Execute the Search (Either Fallback or Geo-Search)
    if not (lon and lat):
        # Fallback: If no coordinates found, do a text search
        fallback = {"is_active": True, "$or": [{"scope": "india"}]}
        if query.place_name:
            fallback["$or"].append({"locations": query.place_name})
        if query.category:
            fallback["category"] = query.category
        
        jobs = await Job.find(fallback).to_list()
    else:
        # Geo-Search: If we HAVE coordinates, run the spatial query
        geo_filter = {
            "coordinates": {
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

    # 3. The Bulletproof Fix: Manually map the ObjectId to a string
    formatted_jobs = []
    for job in jobs:
        formatted_jobs.append(
            JobResponse(
                id=str(job.id),  # <--- The magic conversion happens here!
                employer_id=job.employer_id,
                title=job.title,
                category=job.category,
                scope=job.scope,
                locations=job.locations,
                salary=job.salary,
                description=job.description,
                required_experience=job.required_experience,
                created_at=job.created_at
            )
        )
        
    return formatted_jobs

# --- TEMPORARY FIX ENDPOINT ---
from app.core.config import settings
import pymongo

@router.get("/fix-db-indexes")
async def force_create_indexes():
    from app.core.database import db # Grab the raw database connection
    
    collection = db.client[settings.DATABASE_NAME]["JobTree"]
    
    try:
        # 1. Wipe old indexes just in case they are corrupted
        await collection.drop_indexes()
        
        # 2. Force create the 2dsphere index on coordinates
        await collection.create_index([("coordinates", pymongo.GEOSPHERE)])
        
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