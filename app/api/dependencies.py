from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from pydantic import ValidationError
from bson import ObjectId  # <--- Added Import for ID validation

from app.core.config import settings
from app.models.employer import Employer
from app.models.employee import Employee
from app.models.admin import Admin 
from app.models.auth import TokenBlacklist  
from app.core.security import ALGORITHM

# --- OAuth2 Configuration ---

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/token")

# --- Localization ---

async def get_lang(accept_language: str = Header("en")):
    """
    Extracts language from headers for i18n support.
    """
    return accept_language.split(",")[0].split("-")[0]

# --- Authentication Core ---

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Decodes the JWT, checks the blacklist, and fetches the base user data from the token.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        
        # --- THE GUARD: Check if the token was logged out ---
        jti = payload.get("jti")
        if jti:
            is_blacklisted = await TokenBlacklist.find_one({"jti": jti})
            if is_blacklisted:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, 
                    detail="Session expired or logged out. Please log in again."
                )
        # ----------------------------------------------------
        
        user_id: str = payload.get("sub")
        user_type: str = payload.get("user_type")
        
        if user_id is None or user_type is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="Invalid token payload: missing user ID or role"
            )
            
    except (JWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Could not validate credentials: token may be expired or tampered"
        )

    return {"id": user_id, "user_type": user_type}

# ==============================================================================
# --- BULLETPROOF ROLE GUARDS ---
# ==============================================================================

async def get_current_employer(user_info: dict = Depends(get_current_user)) -> Employer:
    """Ensures the user is an Employer and exists in the database."""
    # 🛑 Prevent 500 errors by checking if the ID is a valid MongoDB ObjectId
    if not ObjectId.is_valid(user_info["id"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid token format. Are you using a registration token instead of an access token?"
        )

    employer = await Employer.get(user_info["id"])
    if not employer or user_info["user_type"] != "employer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Employer access required"
        )
    return employer

async def get_current_employee(user_info: dict = Depends(get_current_user)) -> Employee:
    """Ensures the user is a Worker (Employee) and exists in the database."""
    # 🛑 Prevent 500 errors
    if not ObjectId.is_valid(user_info["id"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid token format. Are you using a registration token instead of an access token?"
        )

    employee = await Employee.get(user_info["id"])
    if not employee or user_info["user_type"] != "employee":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Worker access required"
        )
    return employee

async def get_current_admin(user_info: dict = Depends(get_current_user)) -> Admin:
    """
    Absolute Security Gate: Ensures the token belongs to an active System Administrator.
    """
    if user_info["user_type"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Access denied. System Administrator privileges required."
        )
        
    # 🛑 Prevent 500 errors
    if not ObjectId.is_valid(user_info["id"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid admin token format."
        )
        
    admin_user = await Admin.get(user_info["id"])
    
    if not admin_user or not getattr(admin_user, "is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Admin account is missing or suspended."
        )
        
    return admin_user