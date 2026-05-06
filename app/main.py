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
from app.api.v1.router import api_router
from app.api.v1.endpoints import employers, webhooks, payments
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Router Imports
from app.api.v1.endpoints import payments, auth, employees, employers, jobs

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
    # 👇 This is where Beanie gets initialized under the hood! 👇
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

# --- Rate Limiter Configuration ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(payments.router, prefix="/api/v1/payments", tags=["Payments"])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["Jobs"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(employees.router, prefix="/api/v1/employees", tags=["Employees"])
app.include_router(employers.router, prefix="/api/v1/employers", tags=["Employers"])
app.include_router(webhooks.router, prefix="/api/v1/webhooks", tags=["Webhooks"])

@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "message": f"Welcome to the {settings.PROJECT_NAME} Backend",
        "status": "online",
        "api_version": settings.VERSION
    }