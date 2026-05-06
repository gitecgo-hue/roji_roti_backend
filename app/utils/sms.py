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
        Internal router that sends the message to the configured provider.
        Supports Mocking in DEBUG mode.
        """
        formatted_phone = SMSService._format_phone(phone)

        if settings.DEBUG:
            logger.info(f"--- DEBUG SMS ---")
            logger.info(f"To: {formatted_phone}")
            logger.info(f"Body: {message}")
            if otp: logger.info(f"OTP: {otp}")
            logger.info(f"-----------------")
            return True

        if settings.SMS_PROVIDER == "msg91":
            return await SMSService._send_msg91(formatted_phone, message, otp)
        elif settings.SMS_PROVIDER == "twilio":
            return await SMSService._send_twilio(formatted_phone, message)
        
        logger.warning(f"No SMS provider configured for {formatted_phone}")
        return False

    @staticmethod
    async def _send_msg91(phone: str, message: str, otp: str = None) -> bool:
        """Integration for MSG91 (Indian Regional Support)."""
        # MSG91 OTP API uses a slightly different endpoint than generic messages
        url = "https://api.msg91.com/api/v5/otp" if otp else "https://api.msg91.com/api/v5/flow/"
        
        payload = {
            "template_id": settings.SMS_OTP_TEMPLATE_ID if otp else settings.SMS_GENERIC_TEMPLATE_ID,
            "mobile": phone.replace("+", ""),
            "authkey": settings.SMS_AUTH_KEY,
            "message": message
        }
        if otp: payload["otp"] = otp

        async with httpx.AsyncClient() as client:
            try:
                # MSG91 often uses GET for OTP but POST for Flows; adapting to your preference
                response = await client.post(url, json=payload)
                return response.status_code == 200
            except Exception as e:
                logger.error(f"MSG91 Gateway Error: {e}")
                return False

    @staticmethod
    async def _send_twilio(phone: str, message: str) -> bool:
        """Integration for Twilio (Global Support)."""
        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.TWILIO_SID}/Messages.json"
        auth = (settings.TWILIO_SID, settings.TWILIO_AUTH_TOKEN)
        data = {
            "To": phone,
            "From": settings.TWILIO_PHONE_NUMBER,
            "Body": message
        }
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, data=data, auth=auth)
                return response.status_code == 201
            except Exception as e:
                logger.error(f"Twilio Gateway Error: {e}")
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