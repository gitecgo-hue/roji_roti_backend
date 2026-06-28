import httpx
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class SMSService:
    @staticmethod
    def _format_phone(phone: str) -> str:
        """Ensures phone has the correct country code format."""
        return phone if phone.startswith("+") else f"+91{phone}"

    @staticmethod
    async def _send_to_provider(phone: str, message: str, otp: str = None) -> bool:
        """
        Internal router that sends the message.
        Supports Mocking in DEBUG mode to save API credits during development.
        """
        formatted_phone = SMSService._format_phone(phone)

        # 1. Debug Bypass (Prints to terminal instead of sending real SMS)
        if getattr(settings, "DEBUG", False):
            logger.info(f"--- DEBUG SMS ---")
            logger.info(f"To: {formatted_phone}")
            logger.info(f"Body: {message}")
            if otp: logger.info(f"OTP: {otp}")
            logger.info(f"-----------------")
            return True

        # 2. Production: Route strictly to 2Factor.in
        return await SMSService._send_2factor(phone, otp)

    # --- Provider Integrations ---

    @staticmethod
    async def _send_2factor(phone: str, otp: str) -> bool:
        """Integration for 2Factor.in (Specialized OTP Support)."""
        if not otp:
            logger.warning("Currently, the 2Factor integration only supports OTP messages.")
            return False

        # Ensure phone number is just the 10 digits as required by 2Factor
        clean_phone = phone[-10:]
        api_key = getattr(settings, "TWO_FACTOR_API_KEY", None)
        
        if not api_key:
            logger.error("2Factor API Key is missing from environment variables.")
            return False

       # Add your exact Template Name to the end of the URL string
        template_name = getattr(settings, "TWO_FACTOR_TEMPLATE_ID", None)
        if not template_name:
            logger.error("2Factor Template ID is missing from environment variables.")
            return False
        url = f"https://2factor.in/API/V1/{api_key}/SMS/{clean_phone}/{otp}/{template_name}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url)
                data = response.json()

                if data.get("Status") == "Success":
                    return True
                else:
                    logger.error(f"2Factor API Error: {data.get('Details')}")
                    return False
        except Exception as e:
            logger.error(f"Failed to connect to 2Factor API: {str(e)}")
            return False

    # --- Business Logic Methods ---

    @staticmethod
    async def send_otp(phone: str, otp: str) -> bool:
        """Sends a verification code for registration/login."""
        message = f"Your Roji Roti code is {otp}. Valid for 5 mins."
        return await SMSService._send_to_provider(phone, message, otp=otp)

    @staticmethod
    async def send_job_alert(phone: str, job_title: str, location: str) -> bool:
        """Notifies workers of relevant local jobs."""
        message = f"New Job Alert: {job_title} in {location}! Open Roji Roti to apply."
        return await SMSService._send_to_provider(phone, message)

    @staticmethod
    async def send_subscription_reminder(phone: str, days_left: int) -> bool:
        """Reminds employers before contact access expires."""
        message = f"Your Roji Roti subscription expires in {days_left} days. Renew now!"
        return await SMSService._send_to_provider(phone, message)