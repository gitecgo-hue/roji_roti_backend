import httpx
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class SmsService:
    # Example using a generic SMS gateway (e.g., MSG91)
    SMS_API_URL = "https://api.smsprovider.com/v1/send"
    
    @classmethod
    async def send_job_alert(cls, phone_number: str, category: str, location: str, salary: str):
        """Formats and sends a job alert SMS to a employee."""
        # Note: In India, DLT registration requires strict SMS templates.
        message = f"Roji Roti Alert: New {category} job near {location}. Expected Salary: {salary}. Open the app to apply now!"
        
        payload = {
            "api_key": settings.SMS_API_KEY,
            "to": phone_number,
            "message": message,
            "sender_id": "ROJIRO" # 6-letter approved sender ID
        }
        
        try:
            async with httpx.AsyncClient() as client:
                # Fire and forget (timeout set short so it doesn't hang)
                response = await client.post(cls.SMS_API_URL, json=payload, timeout=3.0)
                response.raise_for_status()
                logger.info(f"SMS sent successfully to {phone_number}")
        except Exception as e:
            logger.error(f"Failed to send SMS to {phone_number}: {e}")