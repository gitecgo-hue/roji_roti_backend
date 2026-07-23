from fastapi import APIRouter, Depends
from app.api.dependencies import get_current_employer
from app.models.employer import Employer
from app.models.feedback import PlatformFeedback
from app.schemas.feedback import FeedbackCreateRequest

router = APIRouter()

@router.post("/submit")
async def submit_platform_feedback(
    feedback_data: FeedbackCreateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Submits detailed feedback, bug reports, or feature requests to the admin team.
    """
    
    new_feedback = PlatformFeedback(
        user_id=str(current_employer.id),
        user_type="employer",
        user_email=current_employer.email,
        category=feedback_data.category,
        description=feedback_data.description
    )
    
    await new_feedback.insert()
    
    # Optional: If the category is a 'bug_report', you could trigger a 
    # background task here to send an immediate alert to your developer Slack/Discord!
    
    return {
        "message": "Thank you for your feedback! Our team will review it shortly.",
        "ticket_id": str(new_feedback.id)
    }