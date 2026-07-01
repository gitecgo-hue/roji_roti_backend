import httpx
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class MapService:
    @staticmethod
    async def get_coordinates(address: str):
        """
        Converts an address (e.g., 'Indore') into Latitude and Longitude using Ola Maps.
        """
        api_key = getattr(settings, "OLA_MAPS_API_KEY", None)
        if not api_key:
            logger.error("Ola Maps API Key is missing.")
            return None

        # Ola Maps Forward Geocoding Endpoint
        url = "https://api.olamaps.io/places/v1/geocode"
        
        params = {
            "address": address,
            "api_key": api_key
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params)
                data = response.json()

                if data.get("status") == "ok" and data.get("geocodingResults"):
                    # Extract the best match
                    location = data["geocodingResults"][0]["geometry"]["location"]
                    return {
                        "latitude": location["lat"],
                        "longitude": location["lng"],
                        "formatted_address": data["geocodingResults"][0]["formatted_address"]
                    }
                else:
                    logger.error(f"Ola Maps Error: {data}")
                    return None
        except Exception as e:
            logger.error(f"Map Geocoding Failed: {str(e)}")
            return None