from datetime import datetime
from fastapi import HTTPException, status
from app.models.promo import PromoCode

class PromotionService:
    @staticmethod
    async def validate_and_calculate(promo_code_str: str, original_price: float) -> tuple[PromoCode, float]:
        """
        Validates a promo code and returns the applied code object and the new discounted price.
        """
        # Normalize the code to uppercase
        code_str = promo_code_str.strip().upper()
        
        promo = await PromoCode.find_one(PromoCode.code == code_str)
        
        # 1. Check Existence and Status
        if not promo or not promo.is_active:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or inactive promo code.")
            
        # 2. Check Expiry Dates
        now = datetime.utcnow()
        if promo.valid_from > now:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This promo code is not active yet.")
        if promo.valid_until and promo.valid_until < now:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This promo code has expired.")
            
        # 3. Check Usage Limits
        if promo.current_usage_count >= promo.max_usage_limit:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This promo code has reached its usage limit.")

        # 4. Calculate the Discount
        final_price = original_price
        if promo.discount_type == "percentage":
            discount_amount = original_price * (promo.discount_value / 100)
            final_price = original_price - discount_amount
        elif promo.discount_type == "flat":
            final_price = original_price - promo.discount_value
            
        # Ensure price never drops below 0
        final_price = max(0.0, final_price)
        
        return promo, final_price