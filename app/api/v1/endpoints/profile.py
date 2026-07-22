from fastapi import APIRouter, UploadFile, File, HTTPException, status
from bson import ObjectId

# --- Import Services ---
from app.utils.storage import StorageService

# --- Import Models ---
from app.models.employee import Employee
from app.models.employer import Employer

router = APIRouter()

# =====================================================================
# --- 3. UPLOAD PROFILE PICTURE ---   
# =====================================================================

@router.post("/{user_type}/{user_id}/upload-photo")
async def upload_profile_picture(
    user_type: str,
    user_id: str,
    file: UploadFile = File(...)
):
    """
    Receives a photo, validates it, compresses it via AWS S3, 
    and updates the user's profile.
    """
    # 1. Security Check: Is it actually an image?
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Invalid file format. Please upload an image."
        )

    # 2. Verify the User Exists
    try:
        obj_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid User ID format")

    if user_type == "employer":
        user = await Employer.get(obj_id)
    elif user_type == "employee":
        user = await Employee.get(obj_id)
    else:
        raise HTTPException(status_code=400, detail="User type must be 'employer' or 'employee'")

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # 3. Magic Time: Compress and Upload to AWS S3
    # We create separate folders in S3 to keep things organized
    folder_name = f"{user_type}_profiles" 
    image_url = await StorageService.upload_image(file=file, folder=folder_name)

    if not image_url:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to upload image to the server."
        )

    # 4. Save the new AWS URL to MongoDB
    await user.update({"$set": {"profile_picture_url": image_url}})

    return {
        "message": "Profile picture updated successfully!",
        "profile_picture_url": image_url
    }