from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
import razorpay
import os

# Models & Services
from app.models.employer import Employer 
from app.models.payment import Payment
from app.models.subscriptions import Subscription
from app.services.payment import PaymentService
from app.services.promotions import PromotionService 
from app.api.dependencies import get_current_employer
from app.models.transaction import Transaction
from app.services.receipt import ReceiptService
from app.api.dependencies import get_current_user

router = APIRouter()

# --- 1. Razorpay Setup ---
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_test_SjjhFIqOpCV19p")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "MJAy2W3vZW64A0Q7fTNbKCbw")

try:
    razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
except Exception as e:
    print(f"Failed to initialize Razorpay: {e}")

# --- 2. Pydantic Schemas & Prices ---
class OrderCreateRequest(BaseModel):
    plan_type: str  # e.g., "standard", "pro", "premium"
    promo_code: Optional[str] = None 

class PaymentVerificationRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

SUBSCRIPTION_PRICES = {
    "standard": 499,
    "pro": 999,      
    "premium": 1999,
    "enterprise": 2499
}

# --- 3. Endpoints ---

@router.post("/create-order", status_code=status.HTTP_201_CREATED)
async def create_subscription_order(
    request: OrderCreateRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Step 1: Initiates a REAL Razorpay order.
    Calculates prices, applies promo codes, and returns an Order ID.
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
    order_data = {
        "amount": amount_in_paise,
        "currency": "INR",
        "receipt": f"rcpt_{str(current_employer.id)[-8:]}_{int(datetime.utcnow().timestamp())}",
        "notes": {
            "employer_id": str(current_employer.id),
            "plan_type": plan_type
        }
    }

    # Call Razorpay API
    try:
        order = razorpay_client.order.create(data=order_data)
        
        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key_id": RAZORPAY_KEY_ID, 
            "plan_type": plan_type,
            "promo_applied": applied_promo_code
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Razorpay Error: {str(e)}")


@router.post("/verify", status_code=status.HTTP_200_OK)
async def verify_subscription_payment(
    request: PaymentVerificationRequest,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Step 2: Secures the payment by verifying the Razorpay cryptographic signature.
    """
    try:
        # Use the Razorpay SDK to verify the signature mathematically
        razorpay_client.utility.verify_payment_signature({
            'razorpay_order_id': request.razorpay_order_id,
            'razorpay_payment_id': request.razorpay_payment_id,
            'razorpay_signature': request.razorpay_signature
        })
    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid payment signature. Potential fraud detected.")

    # 1. Fetch current subscription
    sub = await Subscription.find_one(Subscription.employer_id == str(current_employer.id))
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription record not found")

    # 2. Instant Upgrade Logic
    # (Note: Webhooks also do this, but doing it here updates the UI instantly for the user)
    sub.is_active = True
    sub.contacts_checked = 0 
    sub.resumes_downloaded = 0  
    sub.jobs_posted = 0          
    sub.expiry_date = datetime.utcnow() + timedelta(days=30)
    await sub.save()

    return {
        "message": "Payment verified securely! Subscription activated.",
        "expires_in_days": 30
    }

async def verify_payment(data):
    # ... after signature verification is successful ...
    
    new_transaction = Transaction(
        user_id=current_user.id,
        user_type="employer",
        amount=data.amount,
        status="success",
        razorpay_payment_id=data.razorpay_payment_id,
        razorpay_order_id=data.razorpay_order_id,
        package_name="Premium Plan"
    )
    await new_transaction.insert()

    # Generate and send receipt

@router.get("/transactions/{transaction_id}/receipt")
async def download_receipt(
    transaction_id: str,
    current_user = Depends(get_current_user) # Protect the endpoint
):
    """
    Fetch a transaction by ID and generate a downloadable PDF receipt.
    """
    # 1. Fetch transaction
    transaction = await Transaction.get(transaction_id)
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # 2. Security: Ensure the transaction belongs to the logged-in user
    if str(transaction.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Unauthorized to access this receipt")
        
    # 3. Generate PDF
    # We pass the current_user.name so the receipt is personalized
    pdf_buffer = await ReceiptService.generate_receipt_pdf(transaction, current_user.name)
    
    return Response(
        content=pdf_buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=Receipt_{transaction_id}.pdf"}
    )