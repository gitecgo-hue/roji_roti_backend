# --- Imports ---
import os
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

# --- Rate Limiting Imports ---
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

# --- Core Imports ---
from app.core.limiter import limiter 
from app.core.config import settings
from app.core.database import connect_to_mongo, close_mongo_connection
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.exceptions import initialize_exception_handlers 

# --- Api Imports ---
from app.api.v1.router import api_router
from app.api.v1.endpoints import ivr

# --- ENV Imports ---
from dotenv import load_dotenv


# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup Events ---
    logger.info("Initializing application services...")
    # This is where Beanie gets initialized under the hood!
    await connect_to_mongo() 
    start_scheduler()
    
    yield # Application handles HTTP requests here
    
    # --- Shutdown Events ---
    logger.info("Shutting down application services...")
    stop_scheduler()
    await close_mongo_connection()

# --- FastAPI App Instance ---
app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Initialize global exception wrappers
initialize_exception_handlers(app) 

# --- Rate Limiter Configuration ---
app.state.limiter = limiter

def rate_limit_exceeded_handler(request: Request, exc: Exception):
    from slowapi import _rate_limit_exceeded_handler
    return _rate_limit_exceeded_handler(request, exc)

app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# --- Routing ---
app.include_router(api_router, prefix="/api/v1")
api_router.include_router(ivr.router, prefix="/ivr", tags=["IVR"])

@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirects the root URL to the Swagger documentation."""
    return RedirectResponse(url="/docs")