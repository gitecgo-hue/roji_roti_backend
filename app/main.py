# --- IN app/main.py ---
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# Rate Limiting Imports
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.core.limiter import limiter 

from app.core.config import settings
from app.core.database import connect_to_mongo, close_mongo_connection
from app.core.scheduler import start_scheduler, stop_scheduler
from app.core.exceptions import initialize_exception_handlers  # <--- Import your handlers
from app.api.v1.router import api_router
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

class LanguageMiddleware(BaseHTTPMiddleware):
    """
    Detects language from 'Accept-Language' header to support 
    multi-language UI elements and notifications (e.g., English or Hindi).
    """
    async def dispatch(self, request: Request, call_next):
        lang = request.headers.get("Accept-Language", "en").split(",")[0][:2]
        request.state.lang = lang if lang in ["en", "hi"] else "en"
        response = await call_next(request)
        return response

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
initialize_exception_handlers(app)  # <--- CRITICAL: Registers the error interceptors

# --- Rate Limiter Configuration ---
app.state.limiter = limiter

def rate_limit_exceeded_handler(request: Request, exc: Exception):
    from slowapi import _rate_limit_exceeded_handler
    return _rate_limit_exceeded_handler(request, exc)

app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# --- Middleware Stack ---
app.add_middleware(LanguageMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routing ---
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "message": f"Welcome to the {settings.PROJECT_NAME} Backend",
        "status": "online",
        "api_version": settings.VERSION
    }