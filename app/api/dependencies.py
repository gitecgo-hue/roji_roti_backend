from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from pydantic import ValidationError
from bson import ObjectId
import redis.asyncio as redis
import jwt

# --- Import Config ---
from app.core.config import settings
from app.core.security import ALGORITHM

# --- Import Service ---
from app.services.location import OlaMapsService

# --- Import Models ---
from app.models.employer import Employer
from app.models.employee import Employee
from app.models.admin import Admin 
from app.models.auth import TokenBlacklist  

# --- OAuth2 Configuration ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/token")
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"

# --- OLA MAPS Configuration ---
# Use settings to initialize Redis
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

def get_location_service() -> OlaMapsService:
    """Dependency to provide the Ola Maps Service with Redis attached."""
    # Pass the API key directly from settings
    return OlaMapsService(
        api_key=settings.OLA_MAPS_API_KEY, 
        redis_client=redis_client
    )

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
        # Ensure compatibility whether the token uses 'role' or 'user_type'
        user_type: str = payload.get("user_type") or payload.get("role")
        
        if user_id is None or user_type is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="Invalid token payload: missing user ID or role"
            )
            
    except (JWTError, ValidationError, jwt.PyJWTError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Could not validate credentials: token may be expired or tampered",
            headers={"WWW-Authenticate": "Bearer"}
        )

    return {"id": user_id, "user_type": user_type}

# --- ROLE GUARDS ---
async def get_current_employer(user_info: dict = Depends(get_current_user)) -> Employer:
    """Ensures the user is an Employer and exists in the database."""
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
    """Ensures the user is an employee (Employee) and exists in the database."""
    if not ObjectId.is_valid(user_info["id"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid token format. Are you using a registration token instead of an access token?"
        )

    employee = await Employee.get(user_info["id"])
    if not employee or user_info["user_type"] != "employee":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Employee access required"
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

async def get_any_current_user(user_info: dict = Depends(get_current_user)):
    """
    UNIVERSAL GUARD: Authenticates the user as either an Employer or an Employee.
    Returns the user object with a dynamically attached '.role' property.
    """
    if not ObjectId.is_valid(user_info["id"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid token format."
        )

    # Route the database check based on the token's user_type
    if user_info["user_type"] == "employer":
        user = await Employer.get(user_info["id"])
    elif user_info["user_type"] == "employee":
        user = await Employee.get(user_info["id"])
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Access denied. Employer or Employee credentials required."
        )

    # Ensure the user actually still exists in the database
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="User account not found. Please log in again."
        )

    # Attach the role to the user object dynamically so we can check it later
    # (Matches the logic expected by your resume download endpoint)
    user.role = user_info["user_type"]
    
    return user

# --- LANGUAGE GUARD ---
def get_user_language(accept_language: str = Header(default="en")) -> str:
    """
    Extracts the primary language code from the frontend's request header.
    """
    if not accept_language:
        return "en"
    
    primary_lang = accept_language.split(",")[0].split("-")[0].lower()
    return primary_lang