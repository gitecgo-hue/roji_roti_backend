from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Tuple

from app.api.dependencies import get_location_service
from app.services.location import OlaMapsService

router = APIRouter()

# --- Pydantic Schema for the Distance Matrix ---
class DistanceMatrixRequest(BaseModel):
    origins: List[Tuple[float, float]] = Field(
        ..., 
        description="List of [latitude, longitude] arrays. Example: [[19.076, 72.877]]"
    )
    destinations: List[Tuple[float, float]] = Field(
        ..., 
        description="List of [latitude, longitude] arrays. Example: [[18.520, 73.856]]"
    )

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

# =====================================================================
# ROUTING & DISTANCE ENDPOINTS
# =====================================================================

@router.get("/directions")
async def get_route_directions(
    origin_lat: float = Query(...),
    origin_lng: float = Query(...),
    dest_lat: float = Query(...),
    dest_lng: float = Query(...),
    loc_service: OlaMapsService = Depends(get_location_service)
):
    """Calculates route, distance, and ETAs between a starting point and destination."""
    return await loc_service.get_directions(
        origin_lat=origin_lat, 
        origin_lng=origin_lng, 
        dest_lat=dest_lat, 
        dest_lng=dest_lng
    )

@router.post("/distance-matrix")
async def calculate_distance_matrix(
    request: DistanceMatrixRequest,
    loc_service: OlaMapsService = Depends(get_location_service)
):
    """
    Calculates distance and travel times between multiple origins and destinations.
    NOTE: This is a POST request in our API to handle complex JSON arrays easily, 
    but the service calls Ola Maps using GET.
    """
    return await loc_service.get_distance_matrix(
        origins=request.origins,
        destinations=request.destinations
    )