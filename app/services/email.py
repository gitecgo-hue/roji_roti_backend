import httpx
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    async def send_email(to_email: str, subject: str, html_content: str) -> bool:
        """
        Base method to send any HTML email via Brevo's REST API.
        Includes a DEBUG bypass so you don't waste API credits while testing locally.
        """
        # 1. Debug Bypass (Prints to terminal instead of sending real email)
        if getattr(settings, "DEBUG", False):
            logger.info("========================================")
            logger.info(f"MOCK EMAIL SENT TO: {to_email}")
            logger.info(f"Subject: {subject}")
            logger.info(f"Content: {html_content}")
            logger.info("========================================")
            return True

        # 2. Fetch Credentials
        api_key = getattr(settings, "BREVO_API_KEY", None)
        sender_email = getattr(settings, "BREVO_SENDER_EMAIL", "support@rojiroti.com")
        sender_name = getattr(settings, "BREVO_SENDER_NAME", "Roji Roti")

        if not api_key:
            logger.error("Brevo API Key is missing from environment variables.")
            return False

        # 3. Setup Brevo Request
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json"
        }
        
        payload = {
            "sender": {"name": sender_name, "email": sender_email},
            "to": [{"email": to_email}],
            "subject": subject,
            "htmlContent": html_content
        }

        # 4. Execute Async Request
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload)
                
                # Brevo returns 201 Created on success
                if response.status_code in (200, 201, 202):
                    logger.info(f"✅ Email successfully sent to {to_email}")
                    return True
                else:
                    logger.error(f"❌ Brevo API Error: {response.text}")
                    return False
        except Exception as e:
            logger.error(f"❌ Failed to connect to Brevo API: {str(e)}")
            return False

    # =================================================================
    # --- Business Logic Methods ---
    # =================================================================

    @staticmethod
    async def send_otp_email(to_email: str, otp: str) -> bool:
        """
        Sends the 4-digit verification code.
        This is the exact method currently being called in your auth.py file.
        """
        subject = "Your Roji Roti Verification Code"
        
        # You can style this HTML however you want later
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px;">
            <h2 style="color: #333; text-align: center;">Welcome to Roji Roti</h2>
            <p style="font-size: 16px; color: #555;">Your login verification code is:</p>
            <div style="text-align: center; margin: 20px 0;">
                <span style="font-size: 24px; font-weight: bold; background: #f4f4f4; padding: 10px 20px; letter-spacing: 4px; border-radius: 4px; color: #d32f2f;">
                    {otp}
                </span>
            </div>
            <p style="font-size: 14px; color: #777; text-align: center;">This code is valid for 5 minutes. Please do not share it with anyone.</p>
        </div>
        """
        
        return await EmailService.send_email(to_email, subject, html_content)
        
    @staticmethod
    async def send_welcome_email(to_email: str, name: str) -> bool:
        """Example: A welcome email to send after successful registration."""
        subject = "Welcome to the Roji Roti Family!"
        html_content = f"<h3>Hi {name},</h3><p>We are thrilled to have you on board. Let's find you the best opportunities!</p>"
        
        return await EmailService.send_email(to_email, subject, html_content)