from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    INTERNAL_SECRET: str
    AUTH_SERVICE_URL:         str = "http://localhost:8001/docs"
    INGEST_SERVICE_URL:       str = "http://data-ingestion-service:8004"
    REC_SERVICE_URL:          str = "http://recommendation-service:8002"
    NOTIFICATION_SERVICE_URL: str = "http://notification_service:8007"

    class Config:
        env_file = ".env"


settings = Settings()
