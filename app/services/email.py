import logging

logger = logging.getLogger(__name__)

class EmailService:
    @staticmethod
    async def send_otp_email(to_email: str, otp: str):
        """
        Sends a 6-digit OTP to the user's email.
        Currently using a Mock Console Print for fast testing.
        """
        
        # --- MOCK EMAIL (Prints to your terminal) ---
        print("\n" + "="*40)
        print(f"📧 MOCK EMAIL SENT TO: {to_email}")
        print(f"🔐 YOUR LOGIN OTP IS: {otp}")
        print("="*40 + "\n")
        
        return True

        # --- REAL EMAIL CODE (Uncomment when ready to go live) ---
        """
        import smtplib
        from email.message import EmailMessage
        import os
        
        SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
        SMTP_USER = os.getenv("SMTP_USER", "your-email@gmail.com")
        SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "your-app-password")
        
        try:
            msg = EmailMessage()
            msg.set_content(f"Your Roji Roti login OTP is: {otp}. It is valid for 5 minutes.")
            msg['Subject'] = 'Your Login OTP'
            msg['From'] = SMTP_USER
            msg['To'] = to_email

            server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
        except Exception as e:
            logger.error(f"Failed to send email: {str(e)}")
            return False
        """