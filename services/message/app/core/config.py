from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL:             str
    INTERNAL_SECRET:          str
    SECRET_KEY:               str
    USER_SERVICE_URL:         str = "http://user-service:8002"
    PRODUCE_SERVICE_URL:      str = "http://produce-service:8003"
    NOTIFICATION_SERVICE_URL: str = "http://notification-service:8008"

    class Config:
        env_file = ".env"


settings = Settings()