from fastapi import APIRouter, Depends, Query, status, BackgroundTasks
from datetime import datetime, timezone

# --- Dependency Import ---
from app.api.dependencies import get_current_admin

# --- Model Import ---
from app.models.employee import Employee
from app.models.job import Job

#--- Util Import ---
from app.utils.translator import translate_document_fields

router = APIRouter()

# --- DATABASE MAINTENANCE ENDPOINTS ---
@router.post("/system/db-maintenance/fix-schema", status_code=status.HTTP_200_OK)
async def fix_corrupted_schema(
    dry_run: bool = Query(False, description="If true, reports corruption without modifying the DB"),
    current_admin = Depends(get_current_admin) 
):
    """
    DATABASE MAINTENANCE UTILITY (Enterprise Grade)
    -----------------------------------------------
    Purpose: Recovers corrupted arrays, integers, and malformed objects.
    
    Safety Measures:
    1. Admin-only access.
    2. Dry Run Support: Preview changes before committing them.
    3. Memory Protection: Processes backups in batches of 500.
    4. Soft-recovers data by moving corrupted arrays to legacy backup fields.
    """
    
    # 1. LOCK IT DOWN (Security)
    # The `get_current_admin` dependency inherently guarantees the user is a valid admin,
    # so the manual role check has been safely removed.

    db = Employee.get_motor_collection().database
    employees_coll = db["employees"]
    backups_coll = db["corrupted_employees_backup"]

    repair_results = {}
    total_modifications = 0

    # ========================================================
    # PHASE 1: SWEEP AND RECOVER ARRAY FIELDS (Saved as Strings)
    # ========================================================
    array_fields_to_check = [
        "education", 
        "work_experience", 
        "skills", 
        "languages", 
        "saved_job_ids"
    ]

    for field in array_fields_to_check:
        corrupted_query = {field: {"$type": "string"}}
        corrupted_count = await employees_coll.count_documents(corrupted_query)
        
        # --- DRY RUN CHECK ---
        if dry_run:
            if corrupted_count > 0:
                repair_results[field] = f"Found {corrupted_count} corrupted records (Dry run - no changes made)"
            continue 

        if corrupted_count > 0:
            # --- MEMORY PROTECTION: BATCH PROCESSING BACKUPS ---
            cursor = employees_coll.find(corrupted_query)
            batch = []
            
            async for doc in cursor:
                batch.append({
                    "original_employee_id": doc["_id"],
                    "corrupted_field": field,
                    "corrupted_value": doc.get(field),
                    "backed_up_at": datetime.now(timezone.utc)
                })
                
                if len(batch) >= 500:
                    await backups_coll.insert_many(batch)
                    batch = []
                    
            if batch:
                await backups_coll.insert_many(batch)

            # --- SOFT RECOVERY (Rename & Reset) ---
            fix_result = await employees_coll.update_many(
                corrupted_query,
                [
                    {
                        "$set": {
                            f"legacy_{field}_text": f"${field}", 
                            field: [] 
                        }
                    }
                ]
            )
            repair_results[field] = fix_result.modified_count
            total_modifications += fix_result.modified_count
        else:
            repair_results[field] = 0

    # ========================================================
    # PHASE 2: SWEEP AND RECOVER INTEGER FIELDS
    # ========================================================
    integer_fields_to_check = ["age", "experience_years", "current_salary", "experience"]
    
    for field in integer_fields_to_check:
        corrupted_query = {field: {"$type": "string"}}
        corrupted_count = await employees_coll.count_documents(corrupted_query)
        
        # --- DRY RUN CHECK ---
        if dry_run:
            if corrupted_count > 0:
                repair_results[f"{field}_int_fix"] = f"Found {corrupted_count} corrupted records (Dry run - no changes made)"
            continue

        if corrupted_count > 0:
            fix_result = await employees_coll.update_many(
                corrupted_query,
                [{"$set": {field: {"$toInt": f"${field}"}}}]
            )
            repair_results[f"{field}_int_fix"] = fix_result.modified_count
            total_modifications += fix_result.modified_count
        else:
             repair_results[f"{field}_int_fix"] = 0

    # ========================================================
    # PHASE 3: SWEEP AND RECOVER MALFORMED ARRAY OBJECTS
    # ========================================================
    # Query: Find any document where the work_experience array has an item missing the 'company' field
    malformed_we_query = {
        "work_experience": {
            "$elemMatch": {"company": {"$exists": False}}
        }
    }
    malformed_we_count = await employees_coll.count_documents(malformed_we_query)

    if dry_run:
        if malformed_we_count > 0:
            repair_results["malformed_work_experience"] = f"Found {malformed_we_count} corrupted records (Dry run - no changes made)"
        else:
            repair_results["malformed_work_experience"] = 0
    elif malformed_we_count > 0:
        # --- MEMORY PROTECTION: BATCH PROCESSING BACKUPS ---
        cursor = employees_coll.find(malformed_we_query)
        batch = []
        
        async for doc in cursor:
            batch.append({
                "original_employee_id": doc["_id"],
                "corrupted_field": "work_experience (Missing company field)",
                "corrupted_value": doc.get("work_experience"),
                "backed_up_at": datetime.now(timezone.utc)
            })
            
            if len(batch) >= 500:
                await backups_coll.insert_many(batch)
                batch = []
                
        if batch:
            await backups_coll.insert_many(batch)

        # --- SOFT RECOVERY (Rename & Reset) ---
        fix_result = await employees_coll.update_many(
            malformed_we_query,
            [
                {
                    "$set": {
                        "legacy_malformed_work_experience": "$work_experience",
                        "work_experience": []
                    }
                }
            ]
        )
        repair_results["malformed_work_experience"] = fix_result.modified_count
        total_modifications += fix_result.modified_count
    else:
        repair_results["malformed_work_experience"] = 0

    # ========================================================
    # FINAL RESPONSE
    # ========================================================
    mode = "DRY RUN MODE (No changes saved)" if dry_run else "LIVE MODE (Database Updated)"
    
    return {
        "status": "success",
        "mode": mode,
        "message": "Database sweep completed safely.",
        "total_records_modified": total_modifications,
        "fixes_applied": repair_results
    }

# --- Wrong Translation Cleaner ---
@router.post("/system/clean-translations")
async def clean_corrupted_translations():
    """
    One-time utility to fix jobs where Google's 'Error 500' 
    was accidentally saved as the Hindi translation.
    """
    # Fetch all jobs that actually have a Hindi translation dictionary
    jobs = await Job.find({"translations.hi": {"$exists": True}}).to_list()
    
    jobs_fixed_count = 0
    
    for job in jobs:
        needs_saving = False
        
        # Iterate over a list of items to safely modify the dictionary while looping
        if job.translations and "hi" in job.translations:
            for field, translated_text in list(job.translations["hi"].items()):
                
                # Check for the specific Google rate-limit errors
                if isinstance(translated_text, str) and ("Error 500" in translated_text or "That’s an error" in translated_text):
                    
                    # Fetch the original English value from the main document
                    original_text = getattr(job, field, None)
                    
                    if original_text:
                        # Overwrite the broken translation with the English text
                        job.translations["hi"][field] = original_text
                    else:
                        # If the original text is completely gone, just delete the field
                        del job.translations["hi"][field]
                        
                    needs_saving = True
                    
        # Only trigger a database write if we actually fixed something
        if needs_saving:
            await job.save()
            jobs_fixed_count += 1
            
    return {
        "status": "success",
        "message": f"Scanned {len(jobs)} jobs. Successfully fixed corrupted translations in {jobs_fixed_count} jobs."
    }

# --- force Retranslation ---
@router.post("/system/force-retranslate")
async def force_retranslate_jobs(background_tasks: BackgroundTasks):
    """
    Finds jobs where the Hindi translation is just the English text,
    and forces the translation task to run again.
    """
    jobs = await Job.find_all().to_list()
    fixed_count = 0
    
    for job in jobs:
        # Get the current saved Hindi title
        hi_title = job.translations.get("hi", {}).get("job_title") if job.translations else None
        
        # If there is no Hindi translation, OR the Hindi translation is exactly the English text
        if not hi_title or hi_title == job.job_title:
            
            # Trigger your existing background task
            background_tasks.add_task(
                translate_document_fields,
                str(job.id),
                Job,
                ["job_title", "job_description", "job_category"], # Ensure these match your DB fields
                "hi"
            )
            fixed_count += 1

    return {
        "status": "success", 
        "message": f"Queued {fixed_count} jobs for background re-translation. Give it a minute to process!"
    }