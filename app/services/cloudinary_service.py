import cloudinary
import cloudinary.uploader
from fastapi import UploadFile
import os
import re
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


async def delete_file(file_url: str) -> bool:
    """
    Extracts the public_id from a Cloudinary URL and deletes the file.
    Automatically handles both images and raw documents (PDFs).
    """
    if not file_url:
        return False

    try:
        # Regex to extract the resource type (image/raw) and the path after the version number
        # Example URL: https://res.cloudinary.com/demo/image/upload/v161234/folder/file.jpg
        match = re.search(r'/(image|raw|video)/upload/(?:v\d+/)?(.+)$', file_url)
        
        if not match:
            print("Invalid Cloudinary URL format.")
            return False
            
        r_type = match.group(1)
        file_path = match.group(2)
        
        # Cloudinary Rule: 
        # For images/videos, the public_id does NOT include the file extension.
        # For raw files (PDFs), the public_id MUST include the file extension.
        if r_type in ["image", "video"]:
            # Strip the extension (e.g., "folder/file.jpg" -> "folder/file")
            public_id = file_path.rsplit('.', 1)[0]
        else:
            # Keep the exact path for raw files (e.g., "folder/file.pdf")
            public_id = file_path

        # Instruct Cloudinary to destroy the file
        response = cloudinary.uploader.destroy(
            public_id,
            resource_type=r_type
        )
        
        return response.get("result") == "ok"
        
    except Exception as e:
        print(f"Failed to delete file from Cloudinary: {str(e)}")
        return False