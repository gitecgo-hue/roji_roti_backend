import logging
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

def initialize_exception_handlers(app: FastAPI) -> None:
    """
    Registers global error catchers across the entire FastAPI instance.
    Guarantees every error returns a clean, structured JSON response.
    """

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Catches explicit raise HTTPException() calls from routers."""
        logger.warning(f"HTTP {exc.status_code} on {request.method} {request.url.path} - Detail: {exc.detail}")
        
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": "error",
                "error_type": "HTTPException",
                "message": exc.detail
            }
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Catches invalid schema formats, missing fields, or incorrect Pydantic types."""
        logger.error(f"Validation failure on {request.method} {request.url.path} - Errors: {exc.errors()}")
        
        # Format field locations to be highly readable for the frontend developer
        formatted_errors = []
        for error in exc.errors():
            # e.g., converts ('body', 'location', 'latitude') -> "location -> latitude"
            field_location = " -> ".join(str(x) for x in error.get("loc", []) if x != "body")
            error_message = error.get("msg", "Invalid data format")
            
            if field_location:
                formatted_errors.append(f"Field '{field_location}': {error_message}")
            else:
                formatted_errors.append(error_message)

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "status": "error",
                "error_type": "ValidationError",
                "message": "The data payload provided failed validation constraints.",
                "details": formatted_errors
            }
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """
        The Master Safety Net. Catches unhandled runtime crashes 
        (e.g., database connection down, division by zero, null pointer bugs).
        """
        # exc_info=True prints the full traceback inside your secure backend logs
        logger.critical(f"Unhandled system crash on {request.method} {request.url.path} - Exception: {str(exc)}", exc_info=True)
        
        # NEVER expose raw database trace logs to the public client for security reasons
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "status": "error",
                "error_type": "InternalServerError",
                "message": "An unexpected system malfunction occurred. Our engineering team has been notified."
            }
        )