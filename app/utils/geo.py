import httpx

async def get_coordinates(address: str):
    """
    Converts a string address into coordinates using Nominatim.
    """
    url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
    headers = {"User-Agent": "RojiRotiApp/1.0"}
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        data = response.json()
        
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    return None, None