import os
import requests
from fastapi import HTTPException

OLAMAPS_API_KEY = os.getenv("OLAMAPS_API_KEY")
BASE_URL = "https://api.olamaps.io/places/v1"

def reverse_geocode(lat: float, lng: float) -> dict:
    """
    Calls Ola Maps Reverse Geocoding API to turn coordinates into a human-readable address.
    """
    if not OLAMAPS_API_KEY:
        raise HTTPException(status_code=500, detail="OLA_MAPS_API_KEY is not configured in environment variables.")

    url = f"{BASE_URL}/reverse-geocode"
    params = {
        "latlng": f"{lat},{lng}",
        "api_key": OLAMAPS_API_KEY
    }
    headers = {"X-Request-Id": "roji-roti-fastapi"}
    
    response = requests.get(url, params=params, headers=headers)
    
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch location from Ola Maps")
        
    return response.json()