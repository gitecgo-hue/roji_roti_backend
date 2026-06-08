from fastapi import APIRouter, Form, Response, Request
from twilio.twiml.voice_response import VoiceResponse
import logging

# Import your database models and services here later (e.g., Job, Employee, SmsService)

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
    logger.info(f"📞 Incoming call received from: {From}")
    
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
    Digits: str = Form(default=None), # The button the user pressed
    From: str = Form(default=None)
):
    """
    Handles the logic based on the keypad button the user pressed.
    """
    response = VoiceResponse()
    clean_phone = From[-10:] if From else None
    
    if Digits == "1":
        # Example Logic: You could query MongoDB here to find jobs matching their profile!
        response.say("There are currently 5 new jobs in your area. We have just sent you an SMS with the details to apply.")
        # await SmsService.send_job_list(clean_phone)
        
    elif Digits == "2":
        # Example Logic: Check if their profile is 'is_approved'
        response.say("Your Roji Roti worker account is active and verified.")
        
    elif Digits == "3":
        # The <Dial> verb forwards the call to a real human
        response.say("Please hold while we connect you to our support team.")
        response.dial("+919999999999") # Replace with your actual support number
        
    else:
        response.say("Invalid choice. Please try again.")
        response.redirect("/api/v1/ivr/incoming")
        
    return Response(content=str(response), media_type="application/xml")