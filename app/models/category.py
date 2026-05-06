# app/models/category.py
from beanie import Document, Indexed
from pydantic import Field, ConfigDict

class Category(Document):
    # Name of the category (e.g., "Electrician", "Tailor")
    # Unique index prevents duplicate categories in the database
    name: Indexed(str, unique=True)
    
    description: str = ""
    
    # Icon or image for the UI
    icon_url: str = ""
    
    is_active: bool = True
    
    # ConfigDict ensures Pydantic handles Beanie's internal types correctly
    model_config = ConfigDict(arbitrary_types_allowed=True)

    class Settings:
        name = "categories"