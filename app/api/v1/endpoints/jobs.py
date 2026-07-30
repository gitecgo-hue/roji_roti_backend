from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks 
from fastapi.responses import StreamingResponse, RedirectResponse 
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from beanie import PydanticObjectId
from bson import ObjectId
import pymongo

# ---- Import Models ---
from app.models.notification import Notification, NotificationType
from app.models.job import Job, JobStatus
from app.models.employer import Employer
from app.models.employee import GeoLocation, Employee
from app.models.application import JobApplication

# --- Import Config ---
from app.core.config import settings

# --- Import Schemas ---
from app.schemas.job import (
    JobCreateRequest,
    JobResponse,
    JobDashboardResponse,
    JobUpdateRequest,
    SalaryRangeInput
)

# --- Import Dependencies ---
from app.api.dependencies import get_current_employer, get_current_employee

# --- Import Services ---
from app.services.notification import NotificationService
from app.services.subscriptions import SubscriptionService
from app.services.resumes import ResumeService 
from app.services.webhooks import WebhookService

# --- Import Utilities ---
from app.utils.geocoding import get_coordinates_from_name 

router = APIRouter()

# --- Pydantic Schemas (Local to Search) ---

class JobSearchQuery(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    place_name: Optional[str] = None
    radius_km: int = 10
    category: Optional[str] = None


# =====================================================================
# EMPLOYER: JOB MANAGEMENT
# =====================================================================

# --- BACKGROUND TASK ---
async def match_and_notify_employees(job_id: str, job_category: str, job_city: str, job_title: str, is_pan_india: bool, company_name: str):
    """
    Runs in the background to find matching employees and send them a notification.
    """
    query = {"category": job_category}
    
    if not is_pan_india and job_city:
        query["location_name"] = job_city
        
    matching_employees = await Employee.find(query).limit(100).to_list()
    
    for emp in matching_employees:
        await NotificationService.notify_user(
            user_id=str(emp.id),
            title="New Job Match! 🎯",
            message=f"{company_name} is looking for a {job_title} in your area.",
            notif_type=NotificationType.NEW_JOB_MATCH,
            related_entity_id=job_id
        )

# --- CREATE JOB ENDPOINT ---
@router.post("/create", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_data: JobCreateRequest,
    background_tasks: BackgroundTasks,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Creates a new job posting for the logged-in employer.
    The job is only visible in feeds and triggers notifications if the status is "published".
    """
    # Map the incoming Pydantic schema to the Beanie Database Model
    new_job = Job(
        employer_id=str(current_employer.id),
        
        # --- Basic Details ---
        job_title=job_data.job_title,          
        job_category=job_data.job_category,    
        work_location_type=job_data.work_location_type,
        job_city=job_data.job_city,            
        locations=[job_data.job_city],         
        
        # --- Salary & Pay ---
        pay_type=job_data.pay_type,
        min_fixed_salary=job_data.min_fixed_salary,
        max_fixed_salary=job_data.max_fixed_salary,
        average_incentive=job_data.average_incentive,
        
        # --- Candidate Requirements ---
        minimum_education=job_data.minimum_education,
        total_experience_required=job_data.total_experience_required,
        skills_preference=job_data.skills_preference, 
        
        # --- Interview & Contact ---
        is_walk_in_interview=job_data.is_walk_in_interview,
        address=job_data.address,
        communication_preferences=job_data.communication_preferences,
        
        # --- Descriptions & Settings ---
        job_description=job_data.job_description,
        is_pan_india=job_data.is_pan_india,
        job_type=job_data.job_type,
        is_urgent=job_data.is_urgent,
        
        # --- Visibility Control ---
        status=job_data.status,
        
        # Automatically hide from the Smart Feed unless published
        # (Assuming your feed filters using Job.is_active == True)
        is_active=True if job_data.status == "published" else False
    )

    # Save to database (Only called once!)
    await new_job.insert()

    # --- ONLY Trigger Alerts if the Job is actually PUBLISHED ---
    if new_job.status == "published":
        
        # Alert the Admins immediately
        await NotificationService.notify_user(
             user_id="ADMIN_BROADCAST",
             title="New Job Posted",
             message=f"Employer '{current_employer.company_name}' just posted a new role: {new_job.job_title}.",
             notif_type=NotificationType.SYSTEM_ALERT,
             related_entity_id=str(new_job.id)
        )
        
        # Trigger the matchmaker in the background for employees
        company_name = getattr(current_employer, "company_name", "A company")
        background_tasks.add_task(
            match_and_notify_employees,
            job_id=str(new_job.id),
            job_category=new_job.job_category,
            job_city=new_job.job_city,
            job_title=new_job.job_title,
            is_pan_india=new_job.is_pan_india,
            company_name=company_name
        )

    return new_job
    
# =====================================================================
# PUBLIC/EMPLOYEE: JOB DISCOVERY & SEARCH
# =====================================================================

@router.get("/")
async def get_all_jobs():
    """Fetches all active jobs, ensuring the newest published job is always at the top."""
    
    jobs = await Job.find(
        {"is_active": True}
    ).sort("-created_at").to_list()
    
    return {
        "count": len(jobs),
        "jobs": jobs
    }

@router.get("/feed", response_model=dict)
async def get_smart_job_recommendations(
    lat: Optional[float] = None, 
    lon: Optional[float] = None, 
    radius_km: int = 5, # Defaults to 5km, frontend can change to 10km
    current_employee: Employee = Depends(get_current_employee)
):
    """
    Smart Feed: Dynamically loads jobs based on their exact trade category.
    If GPS is provided, draws a literal circle around the user to find nearby jobs!
    """
    feed_items = {}
    
    # 1. Fetch National Level Jobs First (Remote/Pan-India for their trade)
    feed_items["national_jobs"] = await Job.find(
        Job.is_pan_india == True, 
        Job.is_active == True,
        Job.job_category == current_employee.job_category 
    ).limit(10).to_list()

    # 2. Fallback: If frontend didn't send GPS coords, try to geocode their typed city name
    if not lat or not lon:
        coords = await get_coordinates_from_name(current_employee.location_name)
        if coords:
            lon, lat = coords

    # 3. Geospatial Local Job Query (The Smart Engine)
    if lat and lon:
        search_filter = {
            "is_active": True,
            "is_pan_india": False,
            # Match their specific trade (e.g., "Electrician")
            "job_category": current_employee.job_category, 
            
            # Draw the radius circle
            "current_location": {
                "$near": {
                    "$geometry": {
                        "type": "Point", 
                        "coordinates": [lon, lat] # [longitude, latitude]
                    },
                    # Convert kilometers to meters for MongoDB
                    "$maxDistance": radius_km * 1000 
                }
            }
        }
        
        # Fetch jobs sorted automatically by nearest distance!
        nearby_jobs = await Job.find(search_filter).to_list()
        
        feed_items["radius_searched_km"] = radius_km
        feed_items["matches_found"] = len(nearby_jobs)
        feed_items["recommended_jobs"] = nearby_jobs

    else:
        # 4. Ultimate Fallback: Text matching if both GPS and Geocoding completely fail
        fallback_jobs = await Job.find(
            Job.job_city == current_employee.location_name,
            Job.job_category == current_employee.category,
            Job.is_active == True
        ).limit(20).to_list()

        feed_items["radius_searched_km"] = "N/A (Text Match)"
        feed_items["matches_found"] = len(fallback_jobs)
        feed_items["recommended_jobs"] = fallback_jobs

    return feed_items

# =====================================================================
# EMPLOYEE: JOB SEARCH (Advanced)
# =====================================================================

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

# =====================================================================
# SINGLE JOB DETAIL VIEW
# =====================================================================

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
# EMPLOYER: UPDATE JOB POST
# =====================================================================
@router.put("/update_job/{job_id}")
async def update_job_post(
    job_id: str,
    job_update_data: JobUpdateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Updates an existing job post. 
    Only the employer who created the job is authorized to edit it.
    """
    # 1. Fetch the job from the database
    job = await Job.get(job_id)
    
    # 2. Check if the job exists
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Job post not found."
        )
        
    # 3. Security Check: Ensure the logged-in employer actually owns this job post
    # (Assuming your Job model has an 'employer_id' field)
    if str(job.employer_id) != str(current_employer.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="You are not authorized to edit this job post."
        )
        
    # 4. Extract only the fields the frontend actually sent
    update_dict = job_update_data.model_dump(exclude_unset=True)
    
    if not update_dict:
        return {"message": "No changes provided.", "job_id": job_id}
        
    # 5. Dynamically apply the updates to the Job document
    for field, value in update_dict.items():
        setattr(job, field, value)
        
    # 6. Save the updated document back to MongoDB
    await job.save()
    
    return {
        "message": "Job post updated successfully",
        "job_id": str(job.id),
        "updated_fields": list(update_dict.keys())
    }

# --- BACKGROUND TASK FOR CLEANUP ---
async def handle_deleted_job_cleanup(job_id: str, job_title: str, company_name: str):
    """
    Background task to revoke applications and notify employees 
    when a job is permanently deleted.
    """
    # Find all applications for this job
    applications = await JobApplication.find({"job_id": job_id}).to_list()
    
    for app in applications:
        # Revoke the application (Soft delete or status update is better than hard deleting candidate history)
        app.status = "revoked" # Or "job_deleted", "cancelled"
        await app.save()
        
        # Send Notification to the employee
        # Replace this with your actual notification logic (e.g., Email, Push, or DB Notification)
        notification_message = f"The job '{job_title}' at {company_name} has been closed and your application was revoked."

# ==========================================
# DELETE JOB POST
# ==========================================
@router.delete("/{job_id}", status_code=status.HTTP_200_OK)
async def delete_job_post(
    job_id: str,
    background_tasks: BackgroundTasks,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Permanently deletes a job post and triggers a background task 
    to revoke applications and notify candidates.
    """
    # 1. Fetch the job from the database
    job = await Job.get(job_id)
    
    # 2. Check if the job exists
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Job post not found."
        )
        
    # 3. Security Check: Ensure the logged-in employer actually owns this job post
    if str(job.employer_id) != str(current_employer.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="You are not authorized to delete this job post."
        )
        
    # 4. Trigger the background cleanup task
    # We pass the title and company name so the notification has context even after the job is deleted
    background_tasks.add_task(
        handle_deleted_job_cleanup, 
        job_id=job_id, 
        job_title=getattr(job, "title", "Unknown Job"), 
        company_name=getattr(job, "company_name", "Unknown Company")
    )
        
    # 5. Permanently delete the job from the database
    await job.delete()
    
    return {
        "message": "Job post deleted successfully. Applicants will be notified.",
        "job_id": job_id
    }