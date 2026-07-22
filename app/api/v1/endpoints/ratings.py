from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

# --- Import Models ---
from app.models.rating import Rating
from app.models.employee import Employee
from app.models.employer import Employer

# --- Import Dependencies ---
from app.api.dependencies import get_current_employer

router = APIRouter()

# --- Pydantic Schemas ---

class RatingCreate(BaseModel):
    """
    Schema for submitting 1-5 star feedback [cite: 215-216, 318].
    """
    employee_id: str = Field(..., description="The ID of the worker being rated [cite: 316]")
    rating_value: int = Field(..., ge=1, le=5, description="1 to 5 stars [cite: 216, 318]")
    comment: Optional[str] = Field(None, max_length=500, description="Feedback text [cite: 219, 319]")

# --- Endpoints ---

@router.post("/submit", status_code=status.HTTP_201_CREATED)
async def submit_worker_rating(
    data: RatingCreate,
    current_employer: Employer = Depends(get_current_employer)
):
    """
    Allows an employer to rate a worker after hiring.
    Triggers the mathematical recalculation engine to update the worker's 
    average profile rating [cite: 222-223].
    """
    # 1. Verify worker exists in the platform [cite: 259]
    worker = await Employee.get(data.employee_id)
    if not worker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Worker profile not found."
        )

    # 2. Prevent duplicate ratings from the same employer for the same worker
    # This maintains the integrity of the search ranking system [cite: 222]
    existing_rating = await Rating.find_one(
        Rating.employee_id == data.employee_id,
        Rating.employer_id == str(current_employer.id)
    )
    if existing_rating:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="You have already submitted feedback for this worker."
        )

    # 3. Save the new rating record 
    new_rating = Rating(
        employee_id=data.employee_id,
        employer_id=str(current_employer.id),
        rating_value=data.rating_value,
        comment=data.comment
    )
    await new_rating.insert()

    # 4. Mathematical Recalculation Engine 
    # Fetch all previous ratings to determine the new average profile rating [cite: 223]
    all_ratings = await Rating.find(Rating.employee_id == data.employee_id).to_list()
    
    if all_ratings:
        total_score = sum(r.rating_value for r in all_ratings)
        # Update the worker document with the rounded average 
        worker.rating = round(total_score / len(all_ratings), 1)
        await worker.save()

    return {
        "message": "Rating and feedback submitted successfully.",
        "new_average": worker.rating,
        "rating_id": str(new_rating.id)
    }