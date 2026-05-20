from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL:           str
    INTERNAL_SECRET:        str
    USER_SERVICE_URL:       str = "http://user_service:8002"
    REDIS_URL:              str = "redis://redis:6379/0"
    CLOUDINARY_CLOUD_NAME:  str
    CLOUDINARY_API_KEY:     str
    CLOUDINARY_API_SECRET:  str
    ML_GATEWAY_URL:         str = ""  # Set to http://ml-gateway:8000 when ML stack is running
    NOTIFICATION_SERVICE_URL: str = "http://notification_service:8007"

    class Config:
        env_file = ".env"

settings = Settings()