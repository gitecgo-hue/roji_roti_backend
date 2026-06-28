from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # --- Core Project Settings ---
    PROJECT_NAME: str = "Roji Roti API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = True
    
    # --- Security & Auth ---
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days

    # --- Database Settings ---
    MONGODB_URL: str
    DATABASE_NAME: str  # Required field from snippet 2

    # --- SMS Gateway (2Factor) ---
    SMS_PROVIDER: str = "2factor"
    TWO_FACTOR_API_KEY: Optional[str] = None
    TWO_FACTOR_TEMPLATE_ID: Optional[str] = None
    TWO_FACTOR_SENDER_ID: Optional[str] = None

    # --- Twilio Backup ---
    TWILIO_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None

    # --- Razorpay (Payments) ---
    RAZORPAY_KEY_ID: Optional[str] = None
    RAZORPAY_KEY_SECRET: Optional[str] = None
    RAZORPAY_WEBHOOK_SECRET: Optional[str] = None

    # --- Amazon S3 (Storage) ---
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "ap-south-1"  # Mumbai region is best for India
    AWS_S3_BUCKET_NAME: Optional[str] = None

    # --- External APIs ---
    GOOGLE_MAPS_API_KEY: Optional[str] = None
    GST_VERIFY_API_KEY: Optional[str] = None

    # --- Email (SMTP) ---
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None

    # Pydantic v2 Configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"  # Permits extra fields in .env without crashing
    )

settings = Settings()