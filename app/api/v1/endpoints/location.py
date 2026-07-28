from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Tuple

from app.api.dependencies import get_location_service
from app.services.location import OlaMapsService

router = APIRouter()

# =====================================================================
# LOCATION & GEOCODING ENDPOINTS
# =====================================================================

@router.get("/autocomplete")
async def place_autocomplete(
    query: str = Query(..., min_length=3, description="The address text the user is typing"),
    loc_service: OlaMapsService = Depends(get_location_service)
):
    """Provides predictive text suggestions for an address search bar."""
    return await loc_service.autocomplete(query)

@router.get("/geocode")
async def get_coordinates(
    address: str = Query(..., description="Full text address to look up"),
    loc_service: OlaMapsService = Depends(get_location_service)
):
    """Converts a text address into GPS coordinates."""
    return await loc_service.geocode(address)

@router.get("/reverse-geocode")
async def get_address(
    lat: float = Query(..., description="Latitude"),
    lng: float = Query(..., description="Longitude"),
    loc_service: OlaMapsService = Depends(get_location_service)
):
    """Converts raw GPS coordinates back into a human-readable address."""
    return await loc_service.reverse_geocode(lat, lng)