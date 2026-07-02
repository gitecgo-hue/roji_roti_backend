import asyncio
import hashlib
import hmac

from app.api.v1.endpoints.payments import PublicPaymentVerificationRequest, verify_razorpay_payment
from app.core.config import settings


def test_verify_razorpay_payment_accepts_valid_signature(monkeypatch):
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", "test_secret")

    request = PublicPaymentVerificationRequest(
        razorpay_order_id="order_123",
        razorpay_payment_id="pay_123",
        razorpay_signature=hmac.new(
            b"test_secret",
            b"order_123|pay_123",
            hashlib.sha256,
        ).hexdigest(),
    )

    result = asyncio.run(verify_razorpay_payment(request))

    assert result["verified"] is True
    assert result["message"] == "Payment verified successfully."
