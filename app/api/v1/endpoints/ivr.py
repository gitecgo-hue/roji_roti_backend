import re
import logging
from fastapi import APIRouter, Request, BackgroundTasks

from app.models.employee import Employee
from app.models.job import Job
from app.services.ivr import IVRService
from app.utils.sms import SMSService  # Using your existing SMS service

logger = logging.getLogger(__name__)
router = APIRouter()

# =====================================================================
# 1. TRIGGER A CALL (Employer calling Worker securely)
# =====================================================================

@router.post("/connect-employer")
async def connect_employer_to_worker(
    employer_phone: str, 
    worker_phone: str, 
    background_tasks: BackgroundTasks
):
    """
    Frontend hits this endpoint when an Employer clicks "Call Worker".
    Uses BackgroundTasks so the frontend doesn't freeze waiting for the telecom network.
    """
    background_tasks.add_task(IVRService.trigger_click_to_call, employer_phone, worker_phone)
    
    return {
        "status": "success", 
        "message": "Call initiated! Please answer the incoming call on your phone."
    }

# =====================================================================
# 2. WEBHOOK: HANDLE INCOMING MENUS & CALL LOGS 
# =====================================================================

@router.post("/webhook/call-logs", include_in_schema=False)
@router.get("/webhook/call-logs", include_in_schema=False)
async def ivr_webhook_receiver(request: Request, background_tasks: BackgroundTasks):
    """
    IVR Solutions hits this endpoint during or after a call.
    We process DTMF (keypad presses) and trigger backend actions like sending an SMS.
    """
    try:
        # 1. Extract data whether it was sent as JSON, Form, or URL Params
        if request.method == "POST":
            try:
                data = await request.json()
            except:
                data = dict(await request.form())
        else:
            data = dict(request.query_params)

        # 2. Extract standard fields (Map these to IVR Solutions' exact keys if different)
        caller_phone = data.get("caller_number") or data.get("From")
        dtmf_input = data.get("dtmf") or data.get("Digits")
        
        # Clean the phone number to 10 digits for DB matching
        clean_phone = caller_phone[-10:] if caller_phone else None

        if not clean_phone:
            return {"status": "ignored", "message": "No caller phone provided"}

        logger.info(f"☎️ IVR WEBHOOK: Caller {clean_phone} pressed {dtmf_input}")

        # =========================================================
        # 3. BUSINESS LOGIC BASED ON KEY PRESS
        # =========================================================
        
        # --- PRESS 1: FIND NEW JOBS ---
        if dtmf_input == "1":
            worker = await Employee.find_one({"phone": clean_phone})
            
            if not worker or not getattr(worker, "is_approved", False):
                logger.info("Unregistered or unapproved user pressed 1.")
                return {"status": "received"}

            worker_category = getattr(worker, "category", None) or getattr(worker, "trade_category", None)
            
            if worker_category:
                # Query MongoDB for jobs matching category and location
                query = {
                    "category": re.compile(f"^{worker_category}$", re.IGNORECASE),
                    "is_active": True
                }
                
                location_name = getattr(worker, "location_name", None)
                if location_name:
                    query["$or"] = [
                        {"location": {"$regex": location_name, "$options": "i"}},
                        {"locations": {"$regex": location_name, "$options": "i"}},
                        {"location_name": {"$regex": location_name, "$options": "i"}}
                    ]

                recent_jobs = await Job.find(query).sort("-created_at").limit(3).to_list()

                # Generate and send an SMS with the job details!
                if recent_jobs:
                    job_text = "\n".join([f"- {getattr(j, 'title', 'Job')} in {getattr(j, 'location_name', 'your area')}" for j in recent_jobs])
                    sms_message = f"Roji Roti: Here are 3 new jobs for you!\n{job_text}\nOpen the app to apply."
                    
                    # Send SMS in background so IVR webhook doesn't hang
                    background_tasks.add_task(SMSService._send_to_provider, caller_phone, sms_message)

        # --- PRESS 2: CHECK ACCOUNT STATUS ---
        elif dtmf_input == "2":
            worker = await Employee.find_one({"phone": clean_phone})
            if worker:
                status = "ACTIVE" if getattr(worker, "is_approved", False) else "PENDING REVIEW"
                sms_message = f"Roji Roti: Your account status is currently {status}."
                background_tasks.add_task(SMSService._send_to_provider, caller_phone, sms_message)

        # Always return 200 OK (in JSON) so IVR Solutions knows you successfully received the webhook
        return {"status": "success", "action_taken": True}

    except Exception as e:
        logger.error(f"IVR Webhook Error: {str(e)}")
        return {"status": "error_handled"}