import re
import logging
from fastapi import APIRouter, Form, Response, Request
from twilio.twiml.voice_response import VoiceResponse

from app.models.employee import Employee
from app.models.job import Job
from app.services.sms import SmsService 

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/incoming")
async def handle_incoming_call(
    request: Request,
    From: str = Form(default=None), # The caller's phone number
):
    """
    The main entry point. Twilio calls this when someone dials your number.
    """
    logger.info(f"Incoming call received from: {From}")
    
    response = VoiceResponse()
    
    # The <Gather> verb listens for the user to press a key on their keypad
    gather = response.gather(
        num_digits=1, 
        action="/api/v1/ivr/process-menu", # Where to send the user's choice
        method="POST",
        timeout=5
    )
    
    # What the automated voice will say
    gather.say("Welcome to Roji Roti. Press 1 to hear new jobs in your area. Press 2 to check your account status. Press 3 to speak with support.")
    
    # If the user doesn't press anything, loop back to the start
    response.redirect("/api/v1/ivr/incoming")
    
    # FastAPI MUST return pure XML for Twilio to understand it
    return Response(content=str(response), media_type="application/xml")


@router.post("/process-menu")
async def process_ivr_menu(
    Digits: str = Form(default=None),
    From: str = Form(default=None)
):
    """
    Handles the logic based on the keypad button the user pressed.
    """
    response = VoiceResponse()
    
    # Twilio sends phone numbers with country codes (e.g., +919999999999)
    # We slice the last 10 digits to match your database format
    clean_phone = From[-10:] if From else None
    
    if Digits == "1":
        # ---------------------------------------------------------
        # 1. IDENTIFY THE WORKER
        # ---------------------------------------------------------
        worker = await Employee.find_one({"phone": clean_phone})
        
        if not worker:
            response.say("We could not find a registered profile for this phone number. Please download the Roji Roti app or contact support to register.")
            response.hangup()
            return Response(content=str(response), media_type="application/xml")
            
        if not getattr(worker, "is_approved", False):
            response.say("Your worker profile is currently under review by our team. We will notify you via SMS as soon as your account is activated.")
            response.hangup()
            return Response(content=str(response), media_type="application/xml")

        # ---------------------------------------------------------
        # 2. QUERY MONGODB FOR MATCHING JOBS
        # ---------------------------------------------------------
        worker_category = getattr(worker, "category", None) or getattr(worker, "trade_category", None)
        
        if not worker_category:
            response.say("Your profile is missing a trade category. Please update your profile to hear job recommendations.")
            response.hangup()
            return Response(content=str(response), media_type="application/xml")

        # Case-insensitive category match
        query = {
            "category": re.compile(f"^{worker_category}$", re.IGNORECASE),
            "is_active": True
        }
        
        # Add location match if the worker has a city set
        location_name = getattr(worker, "location_name", None)
        if location_name:
            query["$or"] = [
                {"location": {"$regex": location_name, "$options": "i"}},
                {"locations": {"$regex": location_name, "$options": "i"}},
                {"location_name": {"$regex": location_name, "$options": "i"}}
            ]

        # Fetch the top 3 most recent jobs to read over the phone
        recent_jobs = await Job.find(query).sort("-created_at").limit(3).to_list()

        # ---------------------------------------------------------
        # 3. SPEAK THE RESULTS
        # ---------------------------------------------------------
        if not recent_jobs:
            response.say(f"We currently have no new jobs available for {worker_category} in your area. We will send you an SMS as soon as a match is posted.")
        else:
            response.say(f"We found {len(recent_jobs)} recent jobs matching your profile.")
            
            # Loop through the jobs and read them out loud
            for index, job in enumerate(recent_jobs, start=1):
                title = getattr(job, "title", "A new job")
                salary = getattr(job, "salary_range", getattr(job, "salary", "Salary not specified"))
                loc = getattr(job, "location_name", "your area")
                
                response.say(f"Job {index}: {title} in {loc}. {salary} rupees.")
                response.pause(length=1) # A 1-second breath between jobs
            
            response.say("We are sending the details of these jobs to your phone via SMS right now. Goodbye!")
            
            # TODO: Fire off a background task here to actually send the SMS with the job IDs!

    elif Digits == "2":
        # Check Account Status
        worker = await Employee.find_one({"phone": clean_phone})
        if worker and getattr(worker, "is_approved", False):
            response.say("Your Roji Roti worker account is active and fully verified.")
        else:
            response.say("Your account is either unregistered or pending review.")
            
    elif Digits == "3":
        # Forward to support
        response.say("Please hold while we connect you to our support team.")
        response.dial("+919999999999") 
        
    else:
        response.say("Invalid choice. Please try again.")
        response.redirect("/api/v1/ivr/incoming")
        
    return Response(content=str(response), media_type="application/xml")