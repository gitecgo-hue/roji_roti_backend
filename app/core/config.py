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
    DATABASE_NAME: str  

    # --- SMS Gateway (2Factor) ---
    SMS_PROVIDER: str = "2factor"
    TWO_FACTOR_API_KEY: Optional[str] = None
    TWO_FACTOR_TEMPLATE_ID: Optional[str] = None
    TWO_FACTOR_SENDER_ID: Optional[str] = None

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
    OLA_MAPS_API_KEY: str 
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- Email (SMTP) ---
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None

    # --- Referral Amount ---
    REFERRAL_BONUS_AMOUNT: int = 50

    # Pydantic v2 Configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()