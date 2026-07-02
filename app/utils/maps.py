import httpx
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

class MapService:
    @staticmethod
    async def get_coordinates(address: str) -> dict | None:
        """
        Converts an address (e.g., 'Indore') into precise GPS coordinates using Ola Maps.
        Returns a dictionary with latitude, longitude, and formatted_address.
        """
        # 1. Debug Bypass for Local Testing
        if getattr(settings, "DEBUG", False):
            logger.info(f"🗺️ [MOCK GEOCODE] Converting '{address}' to coordinates.")
            return {
                "latitude": 22.7196, # Defaulting to Indore for mock tests
                "longitude": 75.8577,
                "formatted_address": f"{address.title()}, Madhya Pradesh, India" 
            }

        # 2. Fetch Credentials
        api_key = getattr(settings, "OLA_MAPS_API_KEY", None)
        if not api_key:
            logger.error("🚨 Ola Maps API Key is missing from environment variables.")
            return None

        # 3. Setup Ola Maps Request
        url = "https://api.olamaps.io/places/v1/geocode"
        params = {
            "address": address,
            "api_key": api_key
        }
        headers = {
            "accept": "application/json"
        }

        # 4. Execute Async Request
        try:
            async with httpx.AsyncClient() as client:
                # 10-second timeout prevents the app from hanging if the API goes down
                response = await client.get(url, params=params, headers=headers, timeout=10.0)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Validate both the status flag and the existence of results
                    if data.get("status") == "ok" and data.get("geocodingResults"):
                        results = data.get("geocodingResults", [])
                        
                        # Extract the highest-confidence match safely
                        best_match = results[0]
                        location = best_match.get("geometry", {}).get("location", {})
                        
                        if "lat" in location and "lng" in location:
                            return {
                                "latitude": location["lat"],
                                "longitude": location["lng"],
                                "formatted_address": best_match.get("formatted_address", address)
                            }
                        else:
                            logger.warning(f"⚠️ Geometry missing in Ola Maps response for: '{address}'")
                            return None
                    else:
                        logger.warning(f"⚠️ Ola Maps found no coordinates or returned error for: '{address}'")
                        return None
                        
                else:
                    logger.error(f"❌ Ola Maps API Error [{response.status_code}]: {response.text}")
                    return None
                    
        except httpx.TimeoutException:
            logger.error("❌ Ola Maps API timed out.")
            return None
        except Exception as e:
            logger.error(f"❌ Failed to connect to Ola Maps: {str(e)}")
            return None