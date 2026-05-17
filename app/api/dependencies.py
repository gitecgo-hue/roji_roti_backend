from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from pydantic import ValidationError

from app.core.config import settings
from app.models.employer import Employer
from app.models.employee import Employee
from app.models.auth import TokenBlacklist  # <--- Added Import
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

# --- Role-Specific Dependencies ---

async def get_current_employer(user_info = Depends(get_current_user)) -> Employer:
    """Ensures the user is an Employer and exists in the database."""
    employer = await Employer.get(user_info["id"])
    if not employer or user_info["user_type"] != "employer":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Employer access required"
        )
    return employer

async def get_current_employee(user_info = Depends(get_current_user)) -> Employee:
    """Ensures the user is a Worker (Employee) and exists in the database."""
    employee = await Employee.get(user_info["id"])
    if not employee or user_info["user_type"] != "employee":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Worker access required"
        )
    return employee

async def get_current_admin(current_user: Employer = Depends(get_current_employer)) -> Employer:
    """
    Checks if the authenticated Employer has Admin privileges.
    Uses get_current_employer first to ensure they exist and are an employer,
    then checks the is_admin attribute on the model.
    """
    if not getattr(current_user, "is_admin", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin privileges required."
        )
    return current_user