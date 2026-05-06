import httpx
import asyncio
import logging
from datetime import datetime, timezone
from app.models.partner import Partner

logger = logging.getLogger(__name__)

class WebhookService:
    @staticmethod
    async def trigger_event(event_type: str, data: dict):
        """
        Dispatches an event notification to all active partners with a registered webhook URL[cite: 354, 361].
        Supported events include 'worker_registered', 'job_posted', and 'worker_hired' [cite: 355-359].
        """
        # 1. Fetch active partners that have a listener URL configured
        partners = await Partner.find(
            Partner.is_active == True, 
            Partner.webhook_url != None
        ).to_list()

        if not partners:
            return

        # 2. Prepare the standard payload for all recipients [cite: 362, 369]
        # We include a timestamp and the event type for real-time tracking 
        base_payload = {
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data, # Standard JSON format 
        }

        # 3. Create a helper function to handle individual POST requests with logging
        async def send_to_partner(client, partner):
            # Add partner-specific metadata to the payload
            partner_payload = base_payload.copy()
            partner_payload["partner_id"] = str(partner.id)
            
            try:
                # POST the data to the partner's listener URL [cite: 362]
                response = await client.post(
                    partner.webhook_url, 
                    json=partner_payload, 
                    timeout=5.0
                )
                response.raise_for_status()
            except Exception as e:
                logger.error(f"Webhook failed for partner {partner.name} ({partner.webhook_url}): {e}")

        # 4. Dispatch all requests concurrently to ensure real-time integration 
        async with httpx.AsyncClient() as client:
            tasks = [send_to_partner(client, p) for p in partners]
            # asyncio.gather prevents the failure of one partner from stopping others
            await asyncio.gather(*tasks, return_exceptions=True)