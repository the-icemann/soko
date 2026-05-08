from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL:     str
    INTERNAL_SECRET:  str
    REDIS_URL:        str = "redis://redis:6379/1"
    USER_SERVICE_URL: str = "http://user_service:8002"

    # Cloudinary
    CLOUDINARY_CLOUD_NAME: str
    CLOUDINARY_API_KEY:    str
    CLOUDINARY_API_SECRET: str

    class Config:
        env_file = ".env"


settings = Settings()