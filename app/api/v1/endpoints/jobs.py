from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks 
from fastapi.responses import StreamingResponse, RedirectResponse 
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from beanie import PydanticObjectId
from bson import ObjectId
import pymongo
import math
import re

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
from app.api.dependencies import (
    get_current_employer,
    get_current_employee,
    get_any_current_user
    )

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
    keyword: Optional[str] = None     
    location: Optional[str] = None    
    experience: Optional[str] = None


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

# =====================================================================
# EMPLOYEE: SMART JOB RECOMMENDATIONS (Dynamic Feed)
# =====================================================================
@router.get("/feed", response_model=dict)
async def get_smart_job_recommendations(
    lat: Optional[float] = Query(None, description="User's latitude"), 
    lon: Optional[float] = Query(None, description="User's longitude"), 
    radius_km: int = Query(5, description="Search radius in kilometers"),
    job_category: Optional[str] = Query(None, description="Optional category to filter by"),
    location_name: Optional[str] = Query(None, description="Optional city name if GPS is off")
):
    """
    Public Smart Feed: Dynamically loads jobs. 
    Does NOT require authentication.
    If GPS is provided, draws a literal circle around the coordinates to find nearby jobs.
    """
    feed_items = {}
    
    # 1. Fetch National Level Jobs First (Remote/Pan-India)
    national_query = {
        "is_pan_india": True, 
        "is_active": True,
        "status": "published"
    }
    if job_category:
        national_query["job_category"] = job_category
        
    feed_items["national_jobs"] = await Job.find(national_query).limit(10).to_list()

    # 2. Fallback: If frontend didn't send GPS coords, try to geocode their typed city name
    if not (lat and lon) and location_name:
        coords = await get_coordinates_from_name(location_name)
        if coords:
            lon, lat = coords

    # 3. Geospatial Local Job Query (The Smart Engine)
    if lat and lon:
        search_filter = {
            "is_active": True,
            "status": "published",
            "is_pan_india": False,
            "current_location": {
                "$near": {
                    "$geometry": {
                        "type": "Point", 
                        "coordinates": [lon, lat] # [longitude, latitude]
                    },
                    "$maxDistance": radius_km * 1000 
                }
            }
        }
        if job_category:
            search_filter["job_category"] = job_category
            
        # Fetch jobs sorted automatically by nearest distance!
        nearby_jobs = await Job.find(search_filter).to_list()
        
        feed_items["radius_searched_km"] = radius_km
        feed_items["matches_found"] = len(nearby_jobs)
        feed_items["recommended_jobs"] = nearby_jobs

    else:
        # 4. Ultimate Fallback: Text matching if both GPS and Geocoding completely fail
        fallback_query = {
            "is_active": True,
            "status": "published"
        }
        
        if job_category:
            fallback_query["job_category"] = job_category
            
        if location_name:
            # Case-insensitive text match for the city
            loc_regex = re.compile(f"{re.escape(location_name)}", re.IGNORECASE)
            fallback_query["$or"] = [
                {"job_city": loc_regex},
                {"location_name": loc_regex}
            ]

        # Fetch newest jobs matching the fallback criteria
        fallback_jobs = await Job.find(fallback_query).sort("-created_at").limit(20).to_list()

        feed_items["radius_searched_km"] = "N/A (Text Match / Global Recent)"
        feed_items["matches_found"] = len(fallback_jobs)
        feed_items["recommended_jobs"] = fallback_jobs

    return feed_items

# ----------------------------------------------------------------
# GLOBAL JOB RECOMMENDATIONS (Completely ignores location)
# ----------------------------------------------------------------
@router.get("/recommendations", status_code=status.HTTP_200_OK)
async def get_global_job_recommendations(
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
    limit: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    category: Optional[str] = Query(None, description="Optional job category to filter by"),
    skills: Optional[List[str]] = Query(None, description="Optional list of skills to filter by")
):
    """
    Returns a global feed of jobs with partial matching (half-spellings).
    If a typo results in 0 matches, it falls back to suggesting the newest jobs.
    """
    # 1. Base Query: Only show active, published jobs
    base_query = {
        "is_active": True,
        "status": "published"
    }
    
    # Clone the base query so we can modify it safely
    query = dict(base_query)

    # 2. Build Recommendation Logic (Partial Matches & Forgiving Regex)
    or_conditions = []
    
    if category and category.strip():
        clean_category = " ".join(category.split())
        # Removed ^ and $ to allow partial string matches 
        # (e.g. "driv" will match "Driving", "dev" matches "Developer")
        or_conditions.append({
            "job_category": re.compile(f"{re.escape(clean_category)}", re.IGNORECASE)
        })

    if skills:
        clean_skills = [" ".join(s.split()) for s in skills if s.strip()]
        if clean_skills:
            regex_skills = [
                re.compile(f"{re.escape(s)}", re.IGNORECASE) 
                for s in clean_skills
            ]
            or_conditions.append({"skills_preference": {"$in": regex_skills}})

    if or_conditions:
        query["$and"] = [{"$or": or_conditions}]

    # 3. First Attempt: Calculate Pagination & Fetch Data
    total_matches = await Job.find(query).count()
    is_suggestion_fallback = False

    # 4. FALLBACK LOGIC: If typos are so bad that 0 jobs matched...
    if total_matches == 0 and or_conditions:
        # Revert back to the base query (fetch newest global jobs instead)
        query = dict(base_query)
        total_matches = await Job.find(query).count()
        is_suggestion_fallback = True # Flag this so the frontend knows!

    skip_count = (page - 1) * limit
    total_pages = math.ceil(total_matches / limit) if total_matches > 0 else 1

    # 5. Fetch the Data
    recommended_jobs = await Job.find(query).sort("-created_at").skip(skip_count).limit(limit).to_list()

    # 6. Return response with the fallback flag
    return {
        "metadata": {
            "is_exact_match": not is_suggestion_fallback,
            "message": "Results found" if not is_suggestion_fallback else "No exact matches found. Showing recommended suggestions instead."
        },
        "pagination": {
            "current_page": page,
            "limit": limit,
            "total_matches": total_matches,
            "total_pages": total_pages,
            "has_next_page": page < total_pages,
            "has_prev_page": page > 1
        },
        "results": recommended_jobs
    }

# =====================================================================
# EMPLOYEE: JOB SEARCH (Advanced)
# =====================================================================
@router.get("/search", response_model=List[JobResponse])
async def search_jobs(
    keyword: Optional[str] = Query(None, description="Search by skills, company, title"),
    location: Optional[str] = Query(None, description="Search by city or location"),
    experience: Optional[List[str]] = Query(None, description="e.g., '0-1 yrs', '1-3 yrs'"),
    work_mode: Optional[List[str]] = Query(None, description="e.g., 'Remote', 'Hybrid', 'Onsite'"),
    job_type: Optional[List[str]] = Query(None, description="e.g., 'Full-time', 'Internship'"),
    salary: Optional[List[str]] = Query(None, description="e.g., '0-10L', '10-25L'")
):
    """
    Advanced GET job search combining text search and multiple checkbox filters.
    Supports partial word matching (e.g., 'driv' matches 'Driver').
    Ignores empty fields sent by the frontend to prevent query failures.
    """
    # 1. Base Query: Only show active, published jobs
    db_query = {
        "is_active": True, 
        "status": "published"
    }

    and_conditions = []

    # 2. Top Search Bar: Keyword (Partial Word Match)
    if keyword and keyword.strip():
        # Split into words to allow partial matching on multiple fragments 
        # e.g., "soft eng" matches "Software Engineer"
        words = keyword.strip().split()
        for word in words:
            # By not using ^ and $, this inherently searches for half-words anywhere in the string
            kw_regex = re.compile(f"{re.escape(word)}", re.IGNORECASE)
            
            and_conditions.append({
                "$or": [
                    {"job_title": kw_regex},
                    {"job_category": kw_regex},
                    {"skills_preference": kw_regex}
                ]
            })

    # 3. Top Search Bar: Location (Partial Word Match)
    if location and location.strip():
        loc_regex = re.compile(f"{re.escape(location.strip())}", re.IGNORECASE)
        
        and_conditions.append({
            "$or": [
                {"job_city": loc_regex},
                {"locations": loc_regex},
                {"location_name": loc_regex},
                {"address": loc_regex},
                {"is_pan_india": True} 
            ]
        })

    # 4. Sidebar Filters: Experience (Cleaned to prevent [""] crashes)
    if experience:
        valid_exp = [e.strip() for e in experience if e and e.strip()]
        if valid_exp:
            exp_regex_list = [re.compile(f"{re.escape(e)}", re.IGNORECASE) for e in valid_exp]
            and_conditions.append({
                "$or": [
                    {"total_experience_required": {"$in": exp_regex_list}},
                    {"required_experience": {"$in": exp_regex_list}}
                ]
            })

    # 5. Sidebar Filters: Work Mode 
    if work_mode:
        valid_modes = [m.strip() for m in work_mode if m and m.strip()]
        if valid_modes:
            mode_conditions = []
            for mode in valid_modes:
                if mode.lower() == "remote":
                    mode_conditions.extend([re.compile("remote", re.IGNORECASE), re.compile("work from home", re.IGNORECASE)])
                elif mode.lower() == "onsite":
                    mode_conditions.extend([re.compile("onsite", re.IGNORECASE), re.compile("work from office", re.IGNORECASE)])
                else:
                    mode_conditions.append(re.compile(f"{re.escape(mode)}", re.IGNORECASE))
                    
            and_conditions.append({"work_location_type": {"$in": mode_conditions}})

    # 6. Sidebar Filters: Job Type
    if job_type:
        valid_types = [t.strip() for t in job_type if t and t.strip()]
        if valid_types:
            type_regex_list = [re.compile(f"{re.escape(j)}", re.IGNORECASE) for j in valid_types]
            and_conditions.append({"job_type": {"$in": type_regex_list}})

    # 7. Sidebar Filters: Salary 
    if salary:
        valid_salaries = [s.strip() for s in salary if s and s.strip()]
        if valid_salaries:
            salary_conditions = []
            for s in valid_salaries:
                s_clean = s.replace(" ", "").upper()
                if s_clean == "0-10L":
                    salary_conditions.append({"min_fixed_salary": {"$lte": 1000000}})
                elif s_clean == "10-25L":
                    salary_conditions.append({"min_fixed_salary": {"$gte": 1000000, "$lte": 2500000}})
                elif s_clean == "25-50L":
                    salary_conditions.append({"min_fixed_salary": {"$gte": 2500000, "$lte": 5000000}})
                elif s_clean == "50L+":
                    salary_conditions.append({"min_fixed_salary": {"$gte": 5000000}})
                    
            if salary_conditions:
                and_conditions.append({"$or": salary_conditions})

    # 8. Apply all conditions to the final MongoDB query
    if and_conditions:
        db_query["$and"] = and_conditions

   # 9. Execute the Search
    jobs = await Job.find(db_query).sort("-created_at").to_list()

    # 10. Safely format the response by dumping the exact database model fields
    formatted_jobs = []
    for job in jobs:
        # Convert the Beanie database model into a standard Python dictionary
        # Use .dict() for Pydantic v1, or .model_dump() for Pydantic v2
        job_data = job.model_dump() if hasattr(job, "model_dump") else job.dict()
        
        # Ensure the MongoDB ObjectId is converted to a string for the response
        job_data["id"] = str(job.id)
        
        # Unpack the dictionary directly into your response model
        formatted_jobs.append(JobResponse(**job_data))
        
    return formatted_jobs

# =====================================================================
# SINGLE JOB DETAIL VIEW
# =====================================================================
@router.get("/{job_id}", response_model=Job, status_code=status.HTTP_200_OK)
async def get_single_job(job_id: str):
    """
    Retrieves the complete details of a single job globally by its MongoDB ID.
    No authorization is required.
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

    # 3. Handle the case where the job doesn't exist at all
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Job not found."
        )

    # 4. Prevent public access to private drafts or closed jobs
    if job.status != "published" or not job.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="This job is no longer available."
        )

    # Query the JobApplication collection for this exact job_id
    actual_applicant_count = await JobApplication.find({"job_id": str(job.id)}).count()
    
    # Override the static database number with the true real-time count
    job.applicants_count = actual_applicant_count

    return job

# =====================================================================
# EMPLOYER: UPDATE JOB POST
# ====================================================================+
@router.put("/update_job/{job_id}")
async def update_job_post(
    job_id: str,
    job_update_data: JobUpdateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Updates an existing job post. 
    Only the employer who created the job is authorized to edit it.
    Automatically manages visibility (is_active) based on the publication status.
    """
    # 1. Fetch the job from the database
    try:
        job = await Job.get(PydanticObjectId(job_id))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid Job ID format."
        )
    
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
            detail="You are not authorized to edit this job post."
        )
        
    # 4. Extract only the fields the frontend actually sent
    update_dict = job_update_data.model_dump(exclude_unset=True)
    
    if not update_dict:
        return {"message": "No changes provided.", "job_id": job_id}
        
    # --- 5. THE FIX: Sync is_active when status changes ---
    if "status" in update_dict:
        # Automatically determine visibility based on the new status
        is_published = update_dict["status"] == "published"
        
        # Inject the is_active change directly into our update dictionary
        update_dict["is_active"] = is_published
        
    # 6. Dynamically apply the updates to the Job document
    for field, value in update_dict.items():
        setattr(job, field, value)
        
    # 7. Save the updated document back to MongoDB
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