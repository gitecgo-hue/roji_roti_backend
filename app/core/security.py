import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Union, Optional
from jose import jwt

# --- Core Imports ---
from app.core.config import settings

# --- Configuration ---

# This fixes the 'ImportError' in dependencies.py
ALGORITHM = settings.ALGORITHM 

# --- Token Generation ---

def create_access_token(
    subject: Union[str, Any], 
    user_type: str, 
    expires_delta: Optional[timedelta] = None
) -> str:
    """
    Generates a JWT Token for Roji Roti authentication.
    - subject: The unique user ID or Phone Number
    - user_type: "employee", "employer", "admin", or "registration_token"
    - expires_delta: Optional custom expiration (used for fast-expiring registration tokens)
    """
    if expires_delta:
        # Use custom expiration if provided (e.g., 15 mins for registration tokens)
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        # Default expiration from settings for standard login tokens
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    # Payload matches the keys expected in app/api/dependencies.py
    to_encode = {
        "exp": expire, 
        "sub": str(subject),
        "user_type": user_type,
        "jti": str(uuid.uuid4()) 
    }
    
    # Sign the token using our secret key and the defined algorithm
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt