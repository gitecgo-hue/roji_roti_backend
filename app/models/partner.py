from beanie import Document, Indexed
from pydantic import Field, ConfigDict
from typing import Optional
import secrets

class Partner(Document):
    name: str
    contact_email: str
    # The API Key is generated once and used in the header: X-API-KEY
    api_key: Indexed(str, unique=True)
    is_active: bool = True
    webhook_url: Optional[str] = None # Where we send event notifications

    @classmethod
    async def create_partner(cls, name: str, email: str):
        key = f"rr_{secrets.token_urlsafe(32)}"
        new_partner = cls(name=name, contact_email=email, api_key=key)
        await new_partner.insert()
        return new_partner

    class Settings:
        name = "partners"