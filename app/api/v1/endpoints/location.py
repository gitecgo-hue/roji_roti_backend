from fastapi import APIRouter

# --- Service Imports ---
from app.services.maps import reverse_geocode

router = APIRouter()

@router.post("/resolve")
async def resolve_location(lat: float, lng: float):
    """
    Endpoint for frontend to send GPS coordinates and get back a structured address.
    """
    map_data = reverse_geocode(lat, lng)
    
    # Safely parse the results structure returned by Ola Maps API
    results = map_data.get("results", [])
    formatted_address = results[0].get("formatted_address") if results else "Unknown location"
    
    return {
        "success": True,
        "address": formatted_address,
        "latitude": lat,
        "longitude": lng,
        "raw_data": map_data
    }