import razorpay
import hmac
import hashlib
from app.core.config import settings

class PaymentService:
    # Initialize the client with your API keys
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    @staticmethod
    def create_order(amount_in_rupees: int, receipt_id: str):
        """
        Creates a Razorpay Order. 
        Amount must be in paise (e.g., ₹1.00 = 100 paise).
        """
        data = {
            "amount": amount_in_rupees * 100,  # Convert to paise
            "currency": "INR",
            "receipt": receipt_id,
            "payment_capture": 1  # Auto-capture payment
        }
        try:
            order = PaymentService.client.order.create(data=data)
            return order  # Contains the 'id' (order_id)
        except Exception as e:
            print(f"Razorpay Order Creation Failed: {e}")
            return None

    @staticmethod
    def verify_signature(razorpay_order_id: str, razorpay_payment_id: str, razorpay_signature: str) -> bool:
        """
        Verifies the payment signature to ensure the payment wasn't tampered with.
        """
        try:
            # Razorpay's helper for signature verification
            params_dict = {
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            }
            PaymentService.client.utility.verify_payment_signature(params_dict)
            return True
        except Exception:
            return False