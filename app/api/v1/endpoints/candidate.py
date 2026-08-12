from fastapi import APIRouter, Depends, HTTPException, status
from beanie import PydanticObjectId

# --- Model Imports ---
from app.models.employee import Employee
from app.models.employer import Employer

# --- Schema Imports ---
from app.schemas.candidate import PublicCandidateResponse

# --- Auth Dependency Import ---
from app.api.dependencies import get_admin_or_employer

router = APIRouter()

@router.get("/{employee_id}", status_code=status.HTTP_200_OK)
async def get_employee_profile_by_id(
    employee_id: str,
    current_user = Depends(get_admin_or_employer)
):
    employee = await Employee.get(PydanticObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Not found")

    # Return a custom object or dictionary matching PublicCandidateResponse
    return {
        "id": str(employee.id),
        "name": employee.name,
        "email": employee.email,
        "phone": employee.phone,
        "skills": getattr(employee, "skills", []),
        "total_experience": getattr(employee, "total_experience", 0.0),
        "resume_url": getattr(employee, "resume_url", None),
        "city": employee.location.city if employee.location else None,
        "summary": getattr(employee, "summary", None),
        "email": getattr(employee, "email", None),
        "avatar": getattr(employee, "avatar", None),
        "location": getattr(employee, "location", None),
        "skills": getattr(employee, "skills", []),
        "work_experience": getattr(employee, "work_experience", []),
        "education": getattr(employee, "education", []),
        "expected_salary": getattr(employee, "expected_salary", None),
        "availability": getattr(employee, "availability", None),
        "preferences": getattr(employee, "preferences", None),
        "social_links": getattr(employee, "social_links", None)
    }