import os
import json
from beanie import PydanticObjectId
from datetime import datetime, timedelta
from fastapi import APIRouter, Request, HTTPException, Header, status
import razorpay

# Models & Database
from app.models.subscriptions import Subscription

router = APIRouter()

# Initialize your Razorpay client
client = razorpay.Client(auth=(os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")))

### =====================================================================
### --- 1. RAZORPAY WEBHOOK ENDPOINT ---
### =====================================================================
@router.post("/razorpay")
async def razorpay_webhook(
    request: Request, 
    x_razorpay_signature: str = Header(None)
):
    """
    Listens for Razorpay events to securely activate subscriptions.
    Verifies signatures using the official SDK to prevent spoofing.
    """
    
    # 1. Check if the header exists at all
    if not x_razorpay_signature:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Missing signature header"
        )

    # 2. Get the RAW body (CRITICAL: Do not use request.json())
    raw_body = await request.body()

    # 3. Get the WEBHOOK secret from .env
    webhook_secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")

    # 4. Verify the Cryptographic Signature using the official library
    try:
        client.utility.verify_webhook_signature(
            raw_body.decode('utf-8'), 
            x_razorpay_signature, 
            webhook_secret
        )
    except Exception as e:
        # If the math fails, it prints the exact reason and throws a 400
        print(f"Signature Verification Failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid signature"
        )

    # --- IF YOU PASS THE CHECK, PROCESS THE DATABASE UPGRADE BELOW ---

    # 5. Parse the JSON Payload safely
    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid JSON payload"
        )

    # 6. Process the Event
    event_type = payload.get('event')
    
    if event_type == 'payment.captured':
        payment_entity = payload['payload']['payment']['entity']
        
        # 💡 THE BRIDGE: Extract metadata sent during 'create-order'
        notes = payment_entity.get('notes', {})
        employer_id = notes.get('employer_id')
        plan_type = notes.get('plan_type', 'pro') 

        if employer_id:
            try:
                # Update the subscription in the database
                sub = await Subscription.find_one({"employer_id": employer_id})
                if sub:
                    sub.plan_type = plan_type
                    sub.is_active = True
                    sub.start_date = datetime.utcnow()
                    sub.expiry_date = datetime.utcnow() + timedelta(days=30)
                    
                    # Reset quotas based on the specific plan purchased
                    if plan_type in ['pro', 'premium']:
                        sub.contacts_checked = 0 
                        sub.jobs_posted = 0
                        sub.resumes_downloaded = 0
                        
                    await sub.save()
                    print(f"Securely upgraded employer {employer_id} to {plan_type}!")
                else:
                    print(f"Subscription record not found for employer: {employer_id}")
            except Exception as e:
                print(f"❌ Error updating subscription: {e}")
        else:
            print("Payment captured, but no employer_id was found in the order notes.")

    elif event_type == 'payment.failed':
        # Safely extract the payment ID to log the failure
        payment_id = payload.get('payload', {}).get('payment', {}).get('entity', {}).get('id', 'Unknown')
        print(f"Payment failed event received for payment ID: {payment_id}")

    # Razorpay expects a 200 OK response, or it will keep retrying the webhook
    return {"status": "ok"}