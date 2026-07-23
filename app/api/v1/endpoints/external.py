from fastapi import APIRouter, Security, HTTPException
from fastapi.security.api_key import APIKeyHeader
from app.models.partner import Partner
from app.services.webhooks import WebhookService

router = APIRouter()
api_key_header = APIKeyHeader(name="X-API-KEY")

async def get_partner(api_key: str = Security(api_key_header)):
    partner = await Partner.find_one(Partner.api_key == api_key)
    if not partner or not partner.is_active:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return partner

@router.post("/employees/register")
async def external_employee_registration(data: dict, partner: Partner = Depends(get_partner)):
    """
    Allows partner apps to register employees directly[cite: 332, 340].
    """
    # ... logic to create employee ...
    
    # Trigger Webhook so other partners are notified
    await WebhookService.trigger_event("employee_registered", {"phone": data["phone"]})
    return {"status": "registered", "partner": partner.name}