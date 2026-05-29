from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL:             str
    INTERNAL_SECRET:          str

    # PesaPal
    PESAPAL_CONSUMER_KEY:     str
    PESAPAL_CONSUMER_SECRET:  str
    PESAPAL_SANDBOX:          bool = True
    PESAPAL_IPN_URL:          str                   # https://api.yourdomain.com/payments/webhook/pesapal/ipn
    PESAPAL_CALLBACK_URL:     str                   # https://api.yourdomain.com/payments/webhook/pesapal/callback

    # Other services
    ORDER_SERVICE_URL:        str = "http://order-service:8004"
    USER_SERVICE_URL:         str = "http://user-service:8002"
    NOTIFICATION_SERVICE_URL: str = "http://notification-service:8008"
    FRONTEND_URL:             str = "http://localhost:3000"

    class Config:
        env_file = ".env"


settings = Settings()