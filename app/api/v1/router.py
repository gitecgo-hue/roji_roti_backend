from fastapi import APIRouter
from app.api.v1.endpoints import employees, employers, auth, ivr, jobs, ratings, admin, payments

api_router = APIRouter()

# Attach the authentication endpoints
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])

# Attach the admin endpoints
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])

# Attach the employees endpoints
api_router.include_router(employees.router, prefix="/employees", tags=["Employees"])

# Attach the employers endpoints
api_router.include_router(employers.router, prefix="/employers", tags=["Employers"])

# Attach the jobs endpoints
api_router.include_router(jobs.router, prefix="/jobs", tags=["Jobs"])

# Attach the ratings endpoints
api_router.include_router(ratings.router, prefix="/ratings", tags=["Ratings"])

# Attach the payments endpoints
api_router.include_router(payments.router, prefix="/payments", tags=["Payments"])

# Attach the IVR endpoints
api_router.include_router(ivr.router, prefix="/ivr", tags=["IVR"])