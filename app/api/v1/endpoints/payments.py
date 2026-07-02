import json
import hmac
import hashlib
import logging
from typing import Optional
from datetime import datetime, timedelta
from pydantic import BaseModel

import razorpay
from fastapi import APIRouter, Depends, HTTPException, Response, status, Request, BackgroundTasks
from bson import ObjectId

# --- Models & Services ---
from app.core.config import settings
from app.api.dependencies import get_current_employer, get_current_user
from app.models.employer import Employer 
from app.models.transaction import Transaction
from app.models.subscriptions import Subscription
from app.services.promotions import PromotionService 
from app.services.receipt import ReceiptService

logger = logging.getLogger(__name__)
router = APIRouter()

# =====================================================================
# --- 1. RAZORPAY CLIENT & CONFIG ---
# =====================================================================

def get_razorpay_client():
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        logger.error("Razorpay API keys are missing.")
        raise HTTPException(status_code=500, detail="Payment gateway is not configured.")
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

SUBSCRIPTION_PRICES = {
    "standard": 499,
    "pro": 999,      
    "premium": 1999,
    "enterprise": 2499
}

# =====================================================================
# --- 2. PYDANTIC SCHEMAS ---
# =====================================================================

class OrderCreateRequest(BaseModel):
    plan_type: str  # e.g., "standard", "pro", "premium"
    promo_code: Optional[str] = None 

class PaymentVerificationRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

class CreateOrderRequest(BaseModel):
    amount: int
    currency: str = "INR"
    receipt: str = "roji-roti-checkout"

# =====================================================================
# --- 3. ENDPOINTS ---
# =====================================================================

@router.post("/create-order", status_code=status.HTTP_201_CREATED)
async def create_subscription_order(
    request: OrderCreateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Step 1: Initiates a REAL Razorpay order.
    Calculates prices, applies promo codes, saves a pending transaction, and returns an Order ID.
    """
    plan_type = request.plan_type.lower()
    
    if plan_type not in SUBSCRIPTION_PRICES:
        raise HTTPException(status_code=400, detail="Invalid subscription plan.")
        
    final_amount = SUBSCRIPTION_PRICES[plan_type]
    applied_promo_code = None

    # Apply Promo Code Logic
    if request.promo_code:
        try:
            promo, discounted_amount = await PromotionService.validate_and_calculate(
                promo_code_str=request.promo_code, 
                original_price=final_amount
            )
            final_amount = discounted_amount
            applied_promo_code = promo.code
            
            promo.current_usage_count += 1
            await promo.save()
        except HTTPException as e:
            raise e

    # Calculate amount in paise (Razorpay requirement)
    amount_in_paise = int(final_amount * 100)

    # Construct the real Order payload
    receipt_id = f"rcpt_{str(current_employer.id)[-8:]}_{int(datetime.utcnow().timestamp())}"
    order_data = {
        "amount": amount_in_paise,
        "currency": "INR",
        "receipt": receipt_id,
        "notes": {
            "employer_id": str(current_employer.id),
            "plan_type": plan_type
        }
    }

    # Call Razorpay API
    try:
        client = get_razorpay_client()
        razorpay_order = client.order.create(data=order_data)
        
        # Save the pending transaction to the database before returning to frontend
        new_transaction = Transaction(
            user_id=current_employer.id,
            user_type="employer",
            amount=final_amount, # Saving actual INR in database
            status="CREATED",
            razorpay_order_id=razorpay_order["id"],
            package_name=plan_type.capitalize()
        )
        await new_transaction.insert()

        return {
            "order_id": razorpay_order["id"],
            "amount": razorpay_order["amount"], # Paise (For frontend Razorpay SDK)
            "currency": razorpay_order["currency"],
            "key_id": settings.RAZORPAY_KEY_ID,
            "plan_type": plan_type,
            "promo_applied": applied_promo_code
        }
        
    except Exception as e:
        error_message = str(e).lower()
        if "auth" in error_message or "unauthorized" in error_message or "401" in error_message:
            raise HTTPException(status_code=401, detail="Razorpay authentication failed.")
        raise HTTPException(status_code=502, detail=f"Razorpay Error: {str(e)}")


@router.post("/verify", status_code=status.HTTP_200_OK)
async def verify_subscription_payment(
    request: PaymentVerificationRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Step 2: Secures the payment by verifying the Razorpay cryptographic signature.
    Marks transaction as successful and instantly upgrades the subscription.
    """
    # 1. Verify Signature
    try:
        client = get_razorpay_client()
        client.utility.verify_payment_signature({
            'razorpay_order_id': request.razorpay_order_id,
            'razorpay_payment_id': request.razorpay_payment_id,
            'razorpay_signature': request.razorpay_signature
        })
    except razorpay.errors.SignatureVerificationError:
        logger.error(f"🚨 Invalid Signature for Order: {request.razorpay_order_id}")
        raise HTTPException(status_code=400, detail="Invalid payment signature. Potential fraud detected.")

    # 2. Update Transaction Record
    transaction = await Transaction.find_one({"razorpay_order_id": request.razorpay_order_id})
    if not transaction:
        raise HTTPException(status_code=404, detail="Order not found in the database.")
        
    transaction.status = "SUCCESS"
    transaction.razorpay_payment_id = request.razorpay_payment_id
    await transaction.save()

    # 3. Fetch and Upgrade Current Subscription
    sub = await Subscription.find_one(Subscription.employer_id == str(current_employer.id))
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription record not found for this user.")

    # Instant Upgrade Logic
    sub.is_active = True
    sub.contacts_checked = 0 
    sub.resumes_downloaded = 0  
    sub.jobs_posted = 0          
    sub.expiry_date = datetime.utcnow() + timedelta(days=30)
    await sub.save()

    logger.info(f"💰 Payment Successful! Employer {current_employer.id} upgraded to {transaction.package_name}")

    return {
        "status": "success",
        "message": "Payment verified securely! Subscription activated.",
        "expires_in_days": 30,
        "transaction_id": str(transaction.id)
    }


@router.get("/transactions/{transaction_id}/receipt")
async def download_receipt(
    transaction_id: str,
    current_user = Depends(get_current_user) # Protect the endpoint
):
    """
    Step 3: Fetch a transaction by ID and generate a downloadable PDF receipt.
    """
    # 1. Fetch transaction
    transaction = await Transaction.get(ObjectId(transaction_id))
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # 2. Security: Ensure the transaction belongs to the logged-in user
    if str(transaction.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Unauthorized to access this receipt")
        
    # 3. Generate PDF (Passes current_user.name so the receipt is personalized)
    pdf_buffer = await ReceiptService.generate_receipt_pdf(transaction, current_user.name)
    
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=RojiRoti_Receipt_{transaction_id}.pdf"
        }
    )

# =====================================================================
# --- 4. RAZORPAY WEBHOOK (The Safety Net) ---
# =====================================================================

@router.post("/webhook", include_in_schema=True)
async def razorpay_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Razorpay hits this endpoint asynchronously when a payment succeeds or fails.
    This ensures we upgrade the user even if their browser closes prematurely.
    """
    # 1. Get the raw body and the signature header
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    
    if not signature:
        logger.error("🚨 Razorpay Webhook: Missing Signature Header")
        return {"status": "ignored"}

    # 2. Verify the Webhook Signature
    try:
        client = get_razorpay_client()
        webhook_secret = getattr(settings, "RAZORPAY_WEBHOOK_SECRET", None)
        
        # This will throw an error if a hacker is trying to fake a webhook
        client.utility.verify_webhook_signature(
            raw_body.decode('utf-8'), 
            signature, 
            webhook_secret
        )
    except razorpay.errors.SignatureVerificationError:
        logger.error("🚨 Razorpay Webhook: INVALID SIGNATURE!")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"Razorpay Webhook Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Error")

    # 3. Parse the data safely now that we know it's real
    payload = json.loads(raw_body)
    event_type = payload.get("event")
    
    logger.info(f"🔔 Razorpay Webhook Received Event: {event_type}")

    # 4. Handle Specific Events
    if event_type == "payment.captured" or event_type == "order.paid":
        payment_entity = payload["payload"]["payment"]["entity"]
        order_id = payment_entity.get("order_id")
        payment_id = payment_entity.get("id")
        
        # Pass to a background task so we can return a 200 OK immediately 
        # (Razorpay requires a fast response or it will keep retrying)
        background_tasks.add_task(process_successful_webhook, order_id, payment_id)

    # Always return a 200 OK
    return {"status": "received"}


# --- Helper Function for Background Processing ---
async def process_successful_webhook(order_id: str, payment_id: str):
    """Processes the upgrade in the background to keep the webhook fast."""
    try:
        transaction = await Transaction.find_one({"razorpay_order_id": order_id})
        
        if not transaction:
            logger.warning(f"Webhook processing: Order {order_id} not found in DB.")
            return

        # IDEMPOTENCY CHECK: If the frontend already verified this, do nothing!
        if transaction.status == "SUCCESS":
            logger.info(f"Webhook processing: Order {order_id} was already marked successful by frontend.")
            return

        # If we got here, the frontend failed, but the webhook saved the day!
        logger.info(f"🦸‍♂️ Webhook saving the day for Order: {order_id}!")
        
        transaction.status = "SUCCESS"
        transaction.razorpay_payment_id = payment_id
        await transaction.save()

        # Upgrade the Subscription
        sub = await Subscription.find_one(Subscription.employer_id == str(transaction.user_id))
        if sub:
            sub.is_active = True
            sub.contacts_checked = 0 
            sub.resumes_downloaded = 0  
            sub.jobs_posted = 0          
            sub.expiry_date = datetime.utcnow() + timedelta(days=30)
            await sub.save()
            
    except Exception as e:
        logger.error(f"Failed to process webhook for order {order_id}: {str(e)}")