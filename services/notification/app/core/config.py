from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    INTERNAL_SECRET: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"

    # Africa's Talking
    AT_USERNAME: str
    AT_API_KEY: str
    AT_SENDER_ID: str = "Soko"

    # Other services
    USER_SERVICE_URL: str = "http://user_service:8002"

    class Config:
        env_file = ".env"


settings = Settings()