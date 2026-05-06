import boto3
import logging
from PIL import Image
from io import BytesIO
from botocore.exceptions import ClientError
from fastapi import UploadFile
from app.core.config import settings

logger = logging.getLogger(__name__)

class StorageService:
    # Initialize the S3 Client
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION  # <--- CHANGED THIS LINE
    )

    @staticmethod
    async def upload_image(file: UploadFile, folder: str = "profiles") -> str:
        """
        Compresses an uploaded image to WebP format, uploads it to Amazon S3, 
        and returns the public URL.
        """
        try:
            # 1. Read file and open with Pillow
            image_data = await file.read()
            img = Image.open(BytesIO(image_data))

            # 2. Convert to RGB if necessary (handles PNG/RGBA transparency)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # 3. Resize and Compress
            img.thumbnail((800, 800)) # Max dimension 800px to save bandwidth
            output_buffer = BytesIO()
            
            # Save as WEBP at 75% quality for excellent mobile performance
            img.save(output_buffer, format="WEBP", quality=75) 
            output_buffer.seek(0)

            # 4. Generate a safe S3 Path
            safe_filename = file.filename.split('.')[0].replace(" ", "_")
            file_key = f"{folder}/{safe_filename}.webp"
            
            # 5. Upload to AWS S3
            StorageService.s3_client.put_object(
                Bucket=settings.AWS_S3_BUCKET_NAME,
                Key=file_key,
                Body=output_buffer,
                ContentType="image/webp",
                ACL="public-read"
            )
            
            # 6. Construct the public file URL
            url = f"https://{settings.AWS_S3_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{file_key}"
            return url

        except ClientError as e:
            logger.error(f"S3 Upload Error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected Storage/Compression Error: {e}")
            return None