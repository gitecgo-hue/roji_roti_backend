from fastapi import APIRouter, Depends, HTTPException, status, Query
from beanie import PydanticObjectId

# --- Model Imports ---
from app.models.employee import Employee
from app.models.employer import Employer

# --- Schema Imports ---
from app.schemas.candidate import PublicCandidateResponse

# --- Auth Dependency Import ---
from app.api.dependencies import get_admin_or_employer

# --- Translation Utility ---
from app.utils.helpers import apply_translations

router = APIRouter()

@router.get("/{employee_id}", status_code=status.HTTP_200_OK)
async def get_employee_profile_by_id(
    employee_id: str,
    lang: str = Query("en"), 
    current_user = Depends(get_admin_or_employer)
):
    employee = await Employee.get(PydanticObjectId(employee_id))
    if not employee:
        raise HTTPException(status_code=404, detail="Not found")

    # 1. Dump the entire model to get the exact database structure
    emp_dict = employee.model_dump()

    # 2. Apply translations to the deeply nested dictionary
    translated_profile = apply_translations(emp_dict, getattr(employee, "translations", {}), lang)
    
    # 3. Format the response for the frontend
    translated_profile["id"] = str(employee.id)
    
    # Safely extract the translated city to the top level
    if "location" in translated_profile and translated_profile["location"]:
        translated_profile["city"] = translated_profile["location"].get("city")
    else:
        translated_profile["city"] = None
        
    return translated_profile