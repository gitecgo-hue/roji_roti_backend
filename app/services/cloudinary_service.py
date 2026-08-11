import os
import re
import cloudinary
import cloudinary.uploader
from fastapi import UploadFile
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
    Uploads a file to Cloudinary.
    Forces the exact file format (PDF, JPG, PNG) to prevent Cloudinary 
    from corrupting files during auto-detection from raw bytes.
    """
    try:
        # 1. ALWAYS reset cursor to the beginning before reading
        await file.seek(0)
        file_content = await file.read()
        
        # 2. Extract the file extension securely
        # Default to 'pdf' if unknown, but normally it is inside file.filename
        ext = "pdf"
        if file.filename and "." in file.filename:
            ext = file.filename.rsplit(".", 1)[-1].lower()

        # 3. Upload to Cloudinary with an explicit format!
        # This is the crucial fix: passing the exact format stops Cloudinary 
        # from guessing and corrupting the PDF structure.
        upload_result = cloudinary.uploader.upload(
            file_content,
            folder=folder_name,
            resource_type="auto",
            format=ext 
        )
        
        return upload_result.get("secure_url")
        
    except Exception as e:
        raise ValueError(f"Failed to upload file to Cloudinary: {str(e)}")


async def delete_file(file_url: str):
    """
    Extracts the public ID from a Cloudinary URL and deletes the old file to save space.
    """
    if not file_url or "cloudinary.com" not in file_url:
        return
        
    try:
        # Example URL: https://res.cloudinary.com/.../upload/v12345/resumes/abc123xyz.pdf
        parts = file_url.split('/upload/')
        if len(parts) == 2:
            path_part = parts[1]
            
            # Remove the version tag (e.g., 'v1786383523/') if it exists
            if path_part.startswith('v') and '/' in path_part:
                path_part = path_part.split('/', 1)[1]
            
            # Remove the file extension to get the raw public_id
            public_id = path_part.rsplit('.', 1)[0]
            
            # Destroy the file. We try both 'image' and 'raw' because PDFs 
            # can be saved as either depending on how they were uploaded.
            cloudinary.uploader.destroy(public_id, resource_type="image")
            cloudinary.uploader.destroy(public_id, resource_type="raw")
    except Exception as e:
        print(f"Failed to delete old file from Cloudinary: {e}")