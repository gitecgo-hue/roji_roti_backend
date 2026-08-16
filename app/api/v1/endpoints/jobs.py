from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks, Request
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
from app.models.base import TranslatableDocument
from app.models.category import Category
from app.models.application import JobApplication, ApplicationStatus

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
    get_any_current_user,
    get_user_language
)
get_optional_current_user = get_any_current_user

# --- Import Services ---
from app.services.notification import NotificationService
from app.services.subscriptions import SubscriptionService
from app.services.resumes import ResumeService 
from app.services.webhooks import WebhookService

# --- Import Utilities ---
from app.utils.geocoding import get_coordinates_from_name 
from app.utils.translator import translate_document_fields

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
            title="New Job Match!",
            message=f"{company_name} is looking for a {job_title} in your area.",
            notif_type=NotificationType.NEW_JOB_MATCH,
            related_entity_id=job_id
        )

# --- CREATE JOB ENDPOINT ---
@router.post("/create", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_job(
    job_data: JobCreateRequest,
    background_tasks: BackgroundTasks,
    current_employer: Employer = Depends(get_current_employer),
    lang: str = Depends(get_user_language) 
):

    # AUTO-CREATE CATEGORY LOGIC
    if job_data.job_category:
        # Check if the category already exists in the database
        existing_category = await Category.find_one(Category.name == job_data.job_category)
        
        # If it does not exist, create it immediately!
        if not existing_category:
            new_category = Category(
                name=job_data.job_category,
                description=f"All jobs related to {job_data.job_category}",
                icon_url="",  # Leave blank or set a default generic icon URL here
                is_active=True
            )
            await new_category.insert()
    
    """
    Creates a new job posting for the logged-in employer.
    """
    new_job = Job(
        employer_id=str(current_employer.id),
        job_title=job_data.job_title,          
        job_category=job_data.job_category,    
        work_location_type=job_data.work_location_type,
        job_city=job_data.job_city,            
        locations=[job_data.job_city],   
        pay_type=job_data.pay_type,
        min_fixed_salary=job_data.min_fixed_salary,
        max_fixed_salary=job_data.max_fixed_salary,
        average_incentive=job_data.average_incentive,
        minimum_education=job_data.minimum_education,
        total_experience_required=job_data.total_experience_required,
        skills_preference=job_data.skills_preference, 
        is_walk_in_interview=job_data.is_walk_in_interview,
        address=job_data.address,
        communication_preferences=job_data.communication_preferences,
        job_description=job_data.job_description,
        is_pan_india=job_data.is_pan_india,
        job_type=job_data.job_type,
        is_urgent=job_data.is_urgent,
        status=job_data.status,
        is_active=True if job_data.status == "published" else False
    )

    if not new_job.is_pan_india and new_job.job_city:
        search_string = f"{new_job.address}, {new_job.job_city}" if new_job.address else new_job.job_city
        coords = await get_coordinates_from_name(search_string)
        if coords:
            lon, lat = coords
            new_job.location_point = {
                "type": "Point",
                "coordinates": [lon, lat]
            }

    await new_job.insert()

    # Trigger translation in the background
    background_tasks.add_task(
        translate_document_fields, 
        str(new_job.id), 
        Job, 
        ["job_title", "job_description"], 
        "hi"
    )

    if new_job.status == "published":
        await NotificationService.notify_user(
             user_id="ADMIN_BROADCAST",
             title="New Job Posted",
             message=f"Employer '{current_employer.company_name}' just posted a new role: {new_job.job_title}.",
             notif_type=NotificationType.SYSTEM_ALERT,
             related_entity_id=str(new_job.id)
        )
        
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

    # Return the localized job
    return new_job.localize(lang_code=lang)
    
# =====================================================================
# PUBLIC/EMPLOYEE: JOB DISCOVERY & SEARCH
# =====================================================================

@router.get("/")
async def get_all_jobs(lang: str = Depends(get_user_language)):
    """Fetches all active jobs, translated based on user preference."""
    
    jobs = await Job.find({"is_active": True}).sort("-created_at").to_list()
    
    return {
        "count": len(jobs),
        "jobs": [job.localize(lang_code=lang) for job in jobs] # Apply translation here
    }

# =====================================================================
# EMPLOYEE: SMART JOB RECOMMENDATIONS (Dynamic Feed)
# =====================================================================
@router.get("/feed", response_model=dict)
async def get_smart_job_recommendations(
    lat: Optional[float] = Query(None, description="User's latitude"), 
    lon: Optional[float] = Query(None, description="User's longitude"), 
    radius_km: int = Query(15, description="Search radius in kilometers"),
    job_category: Optional[str] = Query(None, description="Optional category"),
    location_name: Optional[str] = Query(None, description="Optional city name"),
    lang: str = Depends(get_user_language) 
):
    feed_items = {}

    if not (lat and lon) and location_name:
        coords = await get_coordinates_from_name(location_name)
        if coords:
            lon, lat = coords

    if lat and lon:
        search_filter = {
            "is_active": True,
            "status": "published",
            "is_pan_india": False, 
            "location_point": {  
                "$near": {
                    "$geometry": {
                        "type": "Point", 
                        "coordinates": [lon, lat] 
                    },
                    "$maxDistance": radius_km * 1000 
                }
            }
        }
        
        if job_category:
            search_filter["job_category"] = job_category
            
        nearby_jobs = await Job.find(search_filter).to_list()
        
        feed_items["radius_searched_km"] = radius_km
        feed_items["matches_found"] = len(nearby_jobs)
        # Apply translation
        feed_items["recommended_jobs"] = [job.localize(lang_code=lang) for job in nearby_jobs]

    else:
        fallback_query = {
            "is_active": True,
            "status": "published",
            "is_pan_india": False 
        }
        
        if job_category:
            fallback_query["job_category"] = job_category
            
        if location_name:
            # SCENARIO A: They provided a city name, but no GPS coords
            loc_regex = re.compile(f"{re.escape(location_name)}", re.IGNORECASE)
            fallback_query["$or"] = [
                {"job_city": loc_regex},
                {"location_name": loc_regex}
            ]
            
            fallback_jobs = await Job.find(fallback_query).sort("-created_at").limit(20).to_list()

            feed_items["radius_searched_km"] = "N/A (Text Match)"
            feed_items["matches_found"] = len(fallback_jobs)
            feed_items["recommended_jobs"] = [job.localize(lang_code=lang) for job in fallback_jobs]
            
        else:
            # SCENARIO B: No location provided at all. Fetch top 10 newest jobs!
            # Removed the pan_india restriction so they get the absolute newest across the platform
            fallback_query.pop("is_pan_india", None)
            
            # Sort by descending creation date and limit to 10
            newest_jobs = await Job.find(fallback_query).sort("-created_at").limit(10).to_list()
            
            feed_items["radius_searched_km"] = "No location (Showing Top 10 Newest)"
            feed_items["matches_found"] = len(newest_jobs)
            feed_items["recommended_jobs"] = [job.localize(lang_code=lang) for job in newest_jobs]

    return feed_items

# ----------------------------------------------------------------
# GLOBAL JOB RECOMMENDATIONS
# ----------------------------------------------------------------
@router.get("/recommendations", status_code=status.HTTP_200_OK)
async def get_global_job_recommendations(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(None),
    skills: Optional[List[str]] = Query(None),
    lang: str = Depends(get_user_language) 
):
    base_query = {
        "is_active": True,
        "status": "published"
    }
    
    query = dict(base_query)
    or_conditions = []
    
    if category and category.strip():
        clean_category = " ".join(category.split())
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

    total_matches = await Job.find(query).count()
    is_suggestion_fallback = False

    if total_matches == 0 and or_conditions:
        query = dict(base_query)
        total_matches = await Job.find(query).count()
        is_suggestion_fallback = True 

    skip_count = (page - 1) * limit
    total_pages = math.ceil(total_matches / limit) if total_matches > 0 else 1

    recommended_jobs = await Job.find(query).sort("-created_at").skip(skip_count).limit(limit).to_list()

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
        "results": [job.localize(lang_code=lang) for job in recommended_jobs]
    }

# =====================================================================
# EMPLOYEE: JOB SEARCH (Advanced)
# =====================================================================
@router.get("/search", response_model=List[dict]) # Changed to List[dict] due to localize
async def search_jobs(
    keyword: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    experience: Optional[List[str]] = Query(None),
    work_mode: Optional[List[str]] = Query(None),
    job_type: Optional[List[str]] = Query(None),
    salary: Optional[List[str]] = Query(None),
    lang: str = Depends(get_user_language)
):
    db_query = {
        "is_active": True, 
        "status": "published"
    }

    and_conditions = []

    if keyword and keyword.strip():
        words = keyword.strip().split()
        for word in words:
            kw_regex = re.compile(f"{re.escape(word)}", re.IGNORECASE)
            and_conditions.append({
                "$or": [
                    {"job_title": kw_regex},
                    {"job_category": kw_regex},
                    {"skills_preference": kw_regex}
                ]
            })

    if location and location.strip():
        loc_regex = re.compile(f"{re.escape(location.strip())}", re.IGNORECASE)
        and_conditions.append({
            "$or": [
                {"job_city": loc_regex},
                {"locations": loc_regex},
                {"location_name": loc_regex},
                {"address": loc_regex}
            ]
        })

    if experience:
        valid_exp = [e.strip() for e in experience if e and e.strip()]
        if valid_exp:
            exp_conditions = []
            for e in valid_exp:
                e_clean = e.replace("–", "-")
                exp_conditions.append(re.compile(f"{re.escape(e_clean)}", re.IGNORECASE))
                if "0-1" in e_clean:
                    exp_conditions.extend([re.compile("fresher", re.IGNORECASE), re.compile("any", re.IGNORECASE)])

            and_conditions.append({
                "$or": [
                    {"total_experience_required": {"$in": exp_conditions}},
                    {"required_experience": {"$in": exp_conditions}}
                ]
            })

    if work_mode:
        valid_modes = [m.strip() for m in work_mode if m and m.strip()]
        if valid_modes:
            mode_conditions = []
            for mode in valid_modes:
                mode_lower = mode.lower()
                if mode_lower == "remote":
                    mode_conditions.extend([re.compile("remote", re.IGNORECASE), re.compile("work from home", re.IGNORECASE)])
                elif mode_lower == "hybrid":
                    mode_conditions.append(re.compile("hybrid", re.IGNORECASE))
                elif mode_lower == "onsite":
                    mode_conditions.extend([re.compile("onsite", re.IGNORECASE), re.compile("work from office", re.IGNORECASE), re.compile("field job", re.IGNORECASE)])
                else:
                    mode_conditions.append(re.compile(f"{re.escape(mode)}", re.IGNORECASE))
                    
            and_conditions.append({"work_location_type": {"$in": mode_conditions}})

    if job_type:
        valid_types = [t.strip() for t in job_type if t and t.strip()]
        if valid_types:
            type_conditions = []
            for j_type in valid_types:
                j_lower = j_type.lower()
                if j_lower == "full-time":
                    type_conditions.extend([re.compile("full-time", re.IGNORECASE), re.compile("full_time", re.IGNORECASE)])
                elif j_lower == "part-time":
                    type_conditions.extend([re.compile("part-time", re.IGNORECASE), re.compile("part_time", re.IGNORECASE)])
                else:
                    type_conditions.append(re.compile(f"{re.escape(j_type)}", re.IGNORECASE))
                    
            and_conditions.append({"job_type": {"$in": type_conditions}})

    if salary:
        valid_salaries = [s.strip() for s in salary if s and s.strip()]
        if valid_salaries:
            salary_conditions = []
            for s in valid_salaries:
                s_clean = s.replace(" ", "").upper().replace("–", "-")
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

    if and_conditions:
        db_query["$and"] = and_conditions

    jobs = await Job.find(db_query).sort("-created_at").to_list()

    # Apply translations directly while formatting
    formatted_jobs = []
    for job in jobs:
        formatted_jobs.append(job.localize(lang_code=lang))
        
    return formatted_jobs

# =====================================================================
# SINGLE JOB DETAIL VIEW
# =====================================================================
# 1. THE OPTIONAL AUTH BYPASS
async def get_optional_guest_user(request: Request):
    """
    Manually checks for a token to bypass FastAPI's strict 401 auto-error.
    Returns None for public guests, or the User object for logged-in users.
    """
    auth_header = request.headers.get("Authorization")
    
    # If no token is provided, they are a public guest. Safely return None.
    if not auth_header or not auth_header.startswith("Bearer "):
        return None 
        
    token = auth_header.split(" ")[1]
    
    try:
        # You need to decode the token here using your app's existing logic.
        # If your get_any_current_user function accepts a token string directly, you can do:
        # return await get_any_current_user(token=token)
        
        # Otherwise, decode it manually like this (adjust to match your app's standard):
        # payload = jwt.decode(token, "YOUR_SECRET_KEY", algorithms=["HS256"])
        # user_id = payload.get("id")
        # return await Employee.get(PydanticObjectId(user_id)) 
        
        pass
        
    except Exception:
        # If the token is expired or invalid, just treat them as a public guest
        return None 

# 2. THE PUBLIC ENDPOINT
@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    lang: str = Depends(get_user_language),
    current_user: Optional[Any] = Depends(get_optional_guest_user)
):
    # 1. Fetch the job from the database
    job = await Job.get(PydanticObjectId(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 2. Increment view count (this saves to DB instantly)
    await job.update({"$inc": {"views_count": 1}})

    # ==========================================
    # 3. QUERIES (Handling ObjectId vs String)
    # ==========================================
    
    # Applicants
    real_applicants = await JobApplication.find({"job_id": job.id}).count()
    if real_applicants == 0:
        real_applicants = await JobApplication.find({"job_id": str(job.id)}).count()
        
    # Shortlisted
    real_shortlisted = await JobApplication.find(
        {"job_id": job.id, "status": ApplicationStatus.SHORTLISTED}
    ).count()
    if real_shortlisted == 0:
        real_shortlisted = await JobApplication.find(
            {"job_id": str(job.id), "status": "shortlisted"}
        ).count()

    # Hires
    real_hires = await JobApplication.find(
        {"job_id": job.id, "status": ApplicationStatus.HIRED}
    ).count()
    if real_hires == 0:
        real_hires = await JobApplication.find(
            {"job_id": str(job.id), "status": "hired"}
        ).count()

    # ==========================================
    # 4. FETCH CURRENT USER'S APPLICATION STATUS
    # ==========================================
    user_application_status = None
    
    # This now perfectly bypasses for public guests (because current_user will be None)
    if current_user and getattr(current_user, "role", None) == "employee":
        
        user_app = await JobApplication.find_one({
            "job_id": job.id, 
            "employee_id": current_user.id
        })
        
        if not user_app:
            user_app = await JobApplication.find_one({
                "job_id": str(job.id), 
                "employee_id": str(current_user.id)
            })
            
        if user_app:
            user_application_status = getattr(user_app, "status", None)

    # ==========================================
    # 5. INJECT COUNTS AND STATUS POST-LOCALIZATION
    # ==========================================
    
    # Run localization
    localized_job = job.localize(lang_code=lang)
    
    # Force it into a standard dictionary
    if hasattr(localized_job, "model_dump"):
        job_dict = localized_job.model_dump()
    elif hasattr(localized_job, "dict"):
        job_dict = localized_job.dict()
    else:
        job_dict = dict(localized_job)
    
    # Inject our live stats
    job_dict["applicants_count"] = real_applicants
    job_dict["shortlisted_count"] = real_shortlisted
    job_dict["hires_count"] = real_hires
    job_dict["views_count"] = getattr(job, "views_count", 0) + 1
    
    # Inject the current user's application status
    job_dict["user_application_status"] = user_application_status

    return job_dict

# =====================================================================
# EMPLOYER: UPDATE JOB POST
# ====================================================================+
@router.put("/update_job/{job_id}")
async def update_job_post(
    job_id: str,
    job_update_data: JobUpdateRequest,
    background_tasks: BackgroundTasks, # Added background tasks
    current_employer: Employer = Depends(get_current_employer)
):
    try:
        job = await Job.get(PydanticObjectId(job_id))
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Job ID format.")
    
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job post not found.")
        
    if str(job.employer_id) != str(current_employer.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to edit this job post.")
        
    update_dict = job_update_data.model_dump(exclude_unset=True)
    
    if not update_dict:
        return {"message": "No changes provided.", "job_id": job_id}
        
    if "status" in update_dict:
        is_published = update_dict["status"] == "published"
        update_dict["is_active"] = is_published
        
    for field, value in update_dict.items():
        setattr(job, field, value)
        
    await job.save()
    
    # --- RE-TRIGGER TRANSLATION ON UPDATE ---
    # If the title or description was updated, update the Hindi translation in the background!
    if "job_title" in update_dict or "job_description" in update_dict:
        background_tasks.add_task(
            translate_document_fields, 
            str(job.id), 
            Job, 
            ["job_title", "job_description"], 
            "hi"
        )
    
    return {
        "message": "Job post updated successfully",
        "job_id": str(job.id),
        "updated_fields": list(update_dict.keys())
    }

# --- BACKGROUND TASK FOR CLEANUP ---
async def handle_deleted_job_cleanup(job_id: str, job_title: str, company_name: str):
    applications = await JobApplication.find({"job_id": job_id}).to_list()
    for app in applications:
        app.status = "revoked" 
        await app.save()
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
    job = await Job.get(job_id)
    
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job post not found.")
        
    if str(job.employer_id) != str(current_employer.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to delete this job post.")
        
    background_tasks.add_task(
        handle_deleted_job_cleanup, 
        job_id=job_id, 
        job_title=getattr(job, "title", "Unknown Job"), 
        company_name=getattr(job, "company_name", "Unknown Company")
    )
        
    await job.delete()
    
    return {
        "message": "Job post deleted successfully. Applicants will be notified.",
        "job_id": job_id
    }