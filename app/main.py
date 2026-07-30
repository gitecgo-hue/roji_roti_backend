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

# List all the domains your frontend will run on
origins = [
    "http://localhost:8080",      
    "http://127.0.0.1:8080",
    "https://your-frontend-domain.com"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],  # Allows all headers
)

# --- Routing ---
app.include_router(api_router, prefix="/api/v1")

@app.head("/", include_in_schema=False)
@app.get("/", include_in_schema=False, response_class=HTMLResponse)
async def root_screen():
    """Welcome landing page for the API."""
    
    html_content = f"""
    <!DOCTYPE html>
    <html>
        <head>
            <title>{settings.PROJECT_NAME} API</title>
            <style>
                body {{
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    background-color: #f8fafc;
                    margin: 0;
                }}
                .card {{
                    text-align: center;
                    background: white;
                    padding: 50px;
                    border-radius: 12px;
                    box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
                    max-width: 500px;
                }}
                h1 {{ color: #1e293b; margin-bottom: 10px; }}
                p {{ color: #64748b; margin-bottom: 30px; line-height: 1.5; }}
                .btn {{
                    display: inline-block;
                    padding: 12px 24px;
                    background-color: #10b981; /* A nice success green */
                    color: white;
                    text-decoration: none;
                    border-radius: 6px;
                    font-weight: 600;
                    transition: background-color 0.2s;
                }}
                .btn:hover {{ background-color: #059669; }}
                .version {{ margin-top: 30px; font-size: 12px; color: #94a3b8; }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Welcome to {settings.PROJECT_NAME}</h1>
                <p>The backend services are running successfully. Click below to explore and test the endpoints.</p>
                <a href="/docs" class="btn">View API Documentation</a>
                <div class="version">Version: {settings.VERSION}</div>
            </div>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)