import httpx
import uuid
import logging
import json
import redis.asyncio as redis
from fastapi import HTTPException, status
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class OlaMapsService:
    def __init__(self, api_key: str, redis_client: Optional[redis.Redis] = None):
        self.api_key = api_key
        self.base_url = "https://api.olamaps.io"
        self.redis = redis_client
        
        # TTLs (Time to Live) in seconds
        self.CACHE_TTL_GEOCODE = 60 * 60 * 24 * 30  # 30 days (Static data)
        self.CACHE_TTL_ROUTE = 60 * 5               # 5 minutes (Traffic dependent)

    async def _make_request(self, method: str, endpoint: str, params: dict = None) -> Dict[str, Any]:
        """Internal helper to manage headers, API keys, and error handling."""
        if params is None:
            params = {}
        
        params["api_key"] = self.api_key
        headers = {"X-Request-Id": uuid.uuid4().hex}

        async with httpx.AsyncClient(base_url=self.base_url) as client:
            try:
                response = await client.request(
                    method=method, 
                    url=endpoint, 
                    params=params, 
                    headers=headers,
                    timeout=10.0 
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"Ola Maps HTTP Error: {e.response.status_code} - {e.response.text}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY, 
                    detail="Mapping service is currently unavailable."
                )
            except httpx.RequestError as e:
                logger.error(f"Ola Maps Connection Error: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE, 
                    detail="Failed to connect to the mapping provider."
                )

    async def autocomplete(self, query: str) -> Dict[str, Any]:
        """Fetches type-ahead suggestions (Intentionally NOT cached due to high variability)."""
        return await self._make_request("GET", "/places/v1/autocomplete", {"input": query})

    async def geocode(self, address: str) -> Dict[str, Any]:
        """Converts a readable address into Latitude & Longitude with 30-day caching."""
        cache_key = f"ola:geocode:{address.strip().lower()}"
        
        # 1. Check Cache
        if self.redis:
            cached_result = await self.redis.get(cache_key)
            if cached_result:
                return json.loads(cached_result)

        # 2. Fetch from API
        result = await self._make_request("GET", "/places/v1/geocode", {"address": address})

        # 3. Save to Cache
        if self.redis and result.get("status") == "ok":
            await self.redis.setex(cache_key, self.CACHE_TTL_GEOCODE, json.dumps(result))
            
        return result

    async def reverse_geocode(self, lat: float, lng: float) -> Dict[str, Any]:
        """Converts Lat/Lng into an address with 30-day caching (failsafe if Redis is down)."""
        cache_key = f"ola:reverse_geocode:{round(lat, 4)},{round(lng, 4)}"
        
        # 1. Try checking cache safely
        if self.redis:
            try:
                cached_result = await self.redis.get(cache_key)
                if cached_result:
                    return json.loads(cached_result)
            except Exception:
                pass # Redis is offline, ignore and proceed to API

        latlng = f"{lat},{lng}"
        result = await self._make_request("GET", "/places/v1/reverse-geocode", {"latlng": latlng})

        # 2. Try saving to cache safely
        if self.redis and result.get("status") == "ok":
            try:
                await self.redis.setex(cache_key, self.CACHE_TTL_GEOCODE, json.dumps(result))
            except Exception:
                pass # Ignore cache write errors if Redis goes down

        return result