from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL:              str
    INTERNAL_SECRET:           str
    PRODUCE_SERVICE_URL:       str = "http://produce-service:8003"
    USER_SERVICE_URL:          str = "http://user-service:8002"
    PAYMENT_SERVICE_URL:       str = "http://payment-service:8005"
    NOTIFICATION_SERVICE_URL:  str = "http://notification-service:8008"
    KAFKA_BOOTSTRAP_SERVERS:   str = ""  # Set to kafka:9092 when ML stack is running
    KAFKA_TRANSACTION_TOPIC:   str = "soko.transactions"

    class Config:
        env_file = ".env"


settings = Settings()