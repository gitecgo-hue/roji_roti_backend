import os
import re
import random
import string
from datetime import datetime, timedelta
from fastapi import HTTPException
from app.core.config import settings
from app.models.auth import OTP
from app.models.admin import Admin
from app.models.employee import Employee
from app.models.employer import Employer
from app.services.email import EmailService
from app.utils.sms import SMSService

class OTPService:
    @staticmethod
    def is_email(identifier: str) -> bool:
        return bool(re.match(r"[^@]+@[^@]+\.[^@]+", identifier))

    @staticmethod
    async def get_user_by_identifier(identifier: str):
        if OTPService.is_email(identifier):
            return await Admin.find_one({"email": identifier}) or \
                   await Employer.find_one({"email": identifier}) or \
                   await Employee.find_one({"email": identifier})
        else:
            clean_phone = identifier[-10:]
            return await Admin.find_one({"phone": clean_phone}) or \
                   await Employer.find_one({"phone": clean_phone}) or \
                   await Employee.find_one({"phone": clean_phone})

    @staticmethod
    async def verify_and_consume_otp(identifier: str, otp_code: str, is_email_auth: bool):
        """Cleanly verifies OTPs for all endpoints. Includes Dev Bypass."""
        
        # DEV TESTING BYPASS
        # Replace 'True' with 'os.getenv("ENVIRONMENT") == "development"' later for safety
        DEV_MODE = True 
        MASTER_OTP = "1234"

        if DEV_MODE and otp_code == MASTER_OTP:
            return True # Immediately approve the OTP without checking the database
        # ==========================================

        if is_email_auth:
            user = await OTPService.get_user_by_identifier(identifier)
            if not user:
                raise HTTPException(status_code=404, detail="Email not registered.")
            if not getattr(user, "otp_code") or user.otp_code != otp_code:
                raise HTTPException(status_code=400, detail="Invalid Email OTP.")
            if getattr(user, "otp_expires_at") and user.otp_expires_at < datetime.utcnow():
                raise HTTPException(status_code=400, detail="Email OTP expired.")
            
            user.otp_code = None
            user.otp_expires_at = None
            await user.save()
        else:
            clean_phone = identifier[-10:]
            otp_record = await OTP.find_one({"phone": clean_phone})
            
            # --- NO MORE HASHING: Direct string comparison ---
            if not otp_record or not otp_record.code or otp_record.code != otp_code:
                raise HTTPException(status_code=400, detail="Invalid or expired SMS OTP.")
            
            otp_record.code = None
            await otp_record.save()
            
        return True

    @staticmethod
    async def generate_and_send_otp(identifier: str, app_role: str, user=None, name: str = None):
        """Generates and sends OTP, with a built-in DEBUG bypass for testing."""
        now = datetime.utcnow()
        
        # --- DEBUG MODE CHECK ---
        is_debug = getattr(settings, "DEBUG", False)
        
        # Use '1234' for fast local testing, otherwise generate random 4-digit code
        otp_code = "1234" if is_debug else ''.join(random.choices(string.digits, k=4))

        if OTPService.is_email(identifier):
            if user:
                user.otp_code = otp_code
                user.otp_expires_at = now + timedelta(minutes=5)
                user.last_otp_requested_at = now 
                await user.save()
                
            if not is_debug:
                await EmailService.send_otp_email(to_email=identifier, otp=otp_code)
                
            return "email", otp_code
        else:
            clean_phone = identifier[-10:]
            
            if user:
                user.last_otp_requested_at = now
                await user.save()

            otp_record = await OTP.find_one({"phone": clean_phone})
            if otp_record:
                if otp_record.last_request_date.date() < now.date():
                    otp_record.daily_count = 0
                    
                # Don't block rate limits while testing locally
                if otp_record.daily_count >= 10 and not is_debug:
                    raise HTTPException(status_code=429, detail="Daily SMS limit reached.")
                    
                # --- Store the raw OTP directly ---
                otp_record.code = otp_code
                otp_record.user_type = app_role
                otp_record.daily_count += 1
                otp_record.last_request_date = now
                if name: 
                    otp_record.name = name  
                await otp_record.save()
            else:
                new_otp = OTP(
                    phone=clean_phone, 
                    code=otp_code,
                    user_type=app_role, 
                    daily_count=1, 
                    last_request_date=now,
                    name=name
                )
                await new_otp.insert()
                otp_record = await OTP.find_one({"phone": clean_phone})
            
            if not is_debug:
                session_id = await SMSService.send_otp(identifier, otp_code)
                if session_id and otp_record:
                    otp_record.session_id = session_id
                    otp_record.delivery_status = "PENDING"
                    await otp_record.save()
                
            return "mobile number", otp_code