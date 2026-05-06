import logging
from typing import Optional, Tuple
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

logger = logging.getLogger(__name__)

# Initialize geolocator with a custom user agent (OSM requirement)
geolocator = Nominatim(user_agent="roji_roti_backend_v1")

async def get_coordinates_from_name(place_name: str) -> Optional[Tuple[float, float]]:
    """
    Converts a place name (e.g., 'Vijay Nagar') into (longitude, latitude).
    Optimized for Indore, India context.
    """
    try:
        # We append context to the search to avoid getting results from other states/countries
        search_query = f"{place_name}, Indore, Madhya Pradesh, India"
        
        # .geocode is a synchronous call by default, but geopy is fast. 
        # For heavy production, we'd use a threadpool, but this is perfect for now.
        location = geolocator.geocode(search_query)
        
        if location:
            logger.info(f"Geocoding Success: {place_name} -> ({location.longitude}, {location.latitude})")
            # Returns [longitude, latitude] to match MongoDB's 2dsphere requirement
            return float(location.longitude), float(location.latitude)
            
        logger.warning(f"Geocoding failed: No coordinates found for {place_name}")
        return None

    except (GeocoderTimedOut, GeocoderServiceError) as e:
        logger.error(f"Geocoding service error for {place_name}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during geocoding: {e}")
        return None