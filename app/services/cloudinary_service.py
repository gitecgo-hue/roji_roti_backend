import cloudinary
import cloudinary.uploader
from fastapi import UploadFile
import os
from dotenv import load_dotenv

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

async def upload_file(file: UploadFile, folder_name: str = "general") -> str:
    """
    Reads an uploaded file and sends it to Cloudinary.
    Dynamically handles both images (JPG, PNG) and raw documents (PDF).
    """
    try:
        file_content = await file.read()
        
        # Determine the correct Cloudinary resource_type
        # PDFs must be uploaded as "raw"
        if file.content_type == "application/pdf":
            r_type = "raw"
        elif file.content_type.startswith("image/"):
            r_type = "image"
        else:
            raise ValueError(f"Unsupported file type: {file.content_type}")

        # Upload to Cloudinary with the specific resource_type
        upload_result = cloudinary.uploader.upload(
            file_content,
            folder=folder_name,
            resource_type=r_type
        )
        
        return upload_result.get("secure_url")
        
    except Exception as e:
        raise ValueError(f"Failed to upload file to Cloudinary: {str(e)}")