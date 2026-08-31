from fastapi import APIRouter, Depends, Query, status, BackgroundTasks
from datetime import datetime, timezone

# --- Dependency Import ---
from app.api.dependencies import get_current_admin

# --- Model Import ---
from app.models.employee import Employee
from app.models.employer import Employer
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

# --- Employeee Translation Cleanup ---
@router.post("/system/clean-employee-translations")
async def clean_employee_translations():
    """
    Hunts down the Google 'Error 500' string in Employee translations
    and restores their real original names/text.
    """
    # Fetch all employees that have a Hindi translation dictionary
    employees = await Employee.find({"translations.hi": {"$exists": True}}).to_list()
    
    fixed_count = 0
    
    for emp in employees:
        needs_saving = False
        
        if emp.translations and "hi" in emp.translations:
            for field, translated_text in list(emp.translations["hi"].items()):
                
                # Look for the exact Google error string
                if isinstance(translated_text, str) and ("Error 500" in translated_text or "That’s an error" in translated_text):
                    
                    # Fetch their real name/data from the main English document
                    original_text = getattr(emp, field, None)
                    
                    if original_text:
                        emp.translations["hi"][field] = original_text
                    else:
                        del emp.translations["hi"][field]
                        
                    needs_saving = True
                    
        if needs_saving:
            await emp.save()
            fixed_count += 1
            
    return {
        "status": "success",
        "message": f"Scanned {len(employees)} employees. Successfully fixed corrupted data for {fixed_count} profiles."
    }

# --- Wrong Jobs Translation Cleaner ---
@router.post("/system/clean-jobs-translations")
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

# --- Force Employee Retranslate ---
@router.post("/system/bulk-retranslate-employees")
async def bulk_retranslate_all_employees(background_tasks: BackgroundTasks):
    """
    Forces the background translation task to run for EVERY employee in the database,
    ensuring all nested dictionaries and lists are finally translated.
    """
    all_employees = await Employee.find_all().to_list()
    
    # We force every translatable field into the background task
    fields_to_translate = [
        "name",
        "title",
        "summary",
        "gender",
        "location_name",
        "languages",
        "skills",
        "education",
        "preferences"
    ]
    
    for emp in all_employees:
        background_tasks.add_task(
            translate_document_fields,
            str(emp.id),
            Employee,
            fields_to_translate,
            "hi"
        )
        
    return {
        "status": "success",
        "message": f"Successfully queued {len(all_employees)} employees for full translation. Wait 1-2 minutes for Google to process!"
    }

# --- Force Employer Retranslate ---
@router.post("/system/bulk-retranslate-employers")
async def bulk_retranslate_all_employers(background_tasks: BackgroundTasks):
    """
    Forces the background translation task to run for EVERY Employer in the database.
    """
    all_employers = await Employer.find_all().to_list()
    
    # Adjust these to match your actual Employer model fields
    fields_to_translate = [
        "name",
        "gender",
        "company_name",
        "company_type",
        "industry",
        "description",
        "company_address",
        "address"
    ]
    
    for emp in all_employers:
        background_tasks.add_task(
            translate_document_fields,
            str(emp.id),
            Employer,
            fields_to_translate,
            "hi"
        )
        
    return {
        "status": "success",
        "message": f"Successfully queued {len(all_employers)} employers for full translation. Wait 1-2 minutes!"
    }

# --- force Jobs Retranslation ---
@router.post("/system/bulk-retranslate-jobs")
async def bulk_retranslate_all_jobs(background_tasks: BackgroundTasks):
    """
    Forces the background translation task to run for EVERY job in the database.
    This ensures all newly added fields and arrays get translated properly.
    """
    # 1. Fetch ALL jobs from the database
    all_jobs = await Job.find_all().to_list()
    
    # 2. Define the exact fields we want to make sure are translated
    fields_to_translate = [
        "job_title", 
        "job_category", 
        "job_description", 
        "job_city", 
        "minimum_education",
        "total_experience_required", 
        "address", 
        "communication_preferences", 
        "skills_preference"
    ]
    
    # 3. Queue them up in the background
    for job in all_jobs:
        background_tasks.add_task(
            translate_document_fields,
            str(job.id),
            Job,
            fields_to_translate,
            "hi"
        )
        
    return {
        "status": "success",
        "message": f"Successfully queued {len(all_jobs)} jobs for full translation. Please wait 1-2 minutes for the background threads to finish processing!"
    }
