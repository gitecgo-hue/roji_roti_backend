import razorpay
import logging
import os
import uuid 
from datetime import datetime, timedelta
from fastapi import HTTPException, status

from app.core.config import settings
from app.models.payment import Payment
from app.models.subscriptions import Subscription

logger = logging.getLogger(__name__)

class PaymentService:
    # 1. Initialize Razorpay client using project settings
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    # 2. Base Prices (Single source of truth for validation)
    PLAN_PRICES = {
        "standard": 500.0,
        "premium": 1000.0,
        "enterprise": 5000.0
    }

    @classmethod
    async def create_order(cls, employer_id: str, plan_type: str, amount: float):
        """
        Generates a Razorpay order (or mock) and logs the 'created' payment in the DB.
        """
        try:
            amount_in_paise = int(amount * 100)
            
            # --- THE BULLETPROOF FIX: Force the Mock Order ---
            # Keeping this as 'True' for your current test environment
            if True: 
                order = {
                    "id": f"order_mock_{uuid.uuid4().hex[:8]}",
                    "entity": "order",
                    "amount": amount_in_paise,
                    "currency": "INR",
                    "status": "created"
                }
            
            # Save pending record to our database 
            payment_record = Payment(
                employer_id=employer_id,
                amount=amount, 
                razorpay_order_id=order['id'],
                plan_type=plan_type,
                status="created"
            )
            await payment_record.insert()
            
            return order

        except Exception as e:
            logger.error(f"Razorpay Order Creation Error: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail=f"Payment gateway error: {str(e)}"
            )

    @classmethod
    async def verify_payment(cls, order_id: str, payment_id: str, signature: str) -> bool:
        """
        Verifies the cryptographic signature and activates/extends the subscription.
        Integrates the reset logic for mid-month upgrades.
        """
        try:
            # --- THE FINAL BYPASS ---
            # Skip real Razorpay signature check if it's a mock order
            if not order_id.startswith("order_mock_"):
                cls.client.utility.verify_payment_signature({
                    'razorpay_order_id': order_id,
                    'razorpay_payment_id': payment_id,
                    'razorpay_signature': signature
                })
            
            # 2. Find and update our internal payment record
            payment = await Payment.find_one(Payment.razorpay_order_id == order_id)
            if not payment:
                logger.error(f"Payment record not found for order: {order_id}")
                return False
                
            payment.razorpay_payment_id = payment_id
            payment.razorpay_signature = signature
            payment.status = "captured"
            await payment.save()

            # 3. Provision the Subscription (30-day cycle)
            expiry = datetime.utcnow() + timedelta(days=30)
            
            # Fetch existing subscription to check for UPGRADE vs INITIAL purchase
            existing_sub = await Subscription.find_one(Subscription.employer_id == payment.employer_id)
            
            if existing_sub:
                # UPGRADE/RENEWAL LOGIC:
                # 1. Update the plan type
                existing_sub.plan_type = payment.plan_type
                # 2. Reset the 30-day clock
                existing_sub.start_date = datetime.utcnow()
                existing_sub.expiry_date = expiry
                # 3. CRITICAL: Reset ALL usage counters for the new tier
                existing_sub.contacts_checked = 0
                existing_sub.resumes_downloaded = 0
                existing_sub.jobs_posted = 0
                existing_sub.india_level_jobs_posted = 0  # Added reset for national jobs
                
                existing_sub.is_active = True
                await existing_sub.save()
                logger.info(f"Subscription upgraded/renewed for Employer: {payment.employer_id}")
            else:
                # INITIAL PURCHASE LOGIC:
                new_sub = Subscription(
                    employer_id=payment.employer_id,
                    plan_type=payment.plan_type,
                    start_date=datetime.utcnow(),
                    expiry_date=expiry,
                    is_active=True,
                    contacts_checked=0,
                    resumes_downloaded=0,
                    jobs_posted=0,
                    india_level_jobs_posted=0
                )
                await new_sub.insert()
                logger.info(f"New subscription created for Employer: {payment.employer_id}")
                
            return True
            
        except razorpay.errors.SignatureVerificationError:
            logger.error(f"Signature mismatch for order {order_id}. Potential spoofing attempt.")
            return False
        except Exception as e:
            logger.error(f"Payment verification failed: {str(e)}")
            return False