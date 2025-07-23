from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    DATABASE_URL: str
    JWT_SECRET: str
    JWT_ALGORITHM: str
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str
    GOOGLE_REDIRECT_URI: str
    RESEND_API_KEY: str
    GROQ_API_KEY: str

    FRONTEND_URL: str 
    BACKEND_URL: str
    EMAIL_FROM: str

    S3_BUCKET_NAME: str

    PGDATABASE: str
    PGUSER: str
    PGPASSWORD: str
    PGHOST: str
    PGPORT: int

    REDIS_PORT: int
    REDIS_HOST: str
    REDIS_PASSWORD: str
    REDIS_USERNAME: str
    SESSION_SECRET_KEY: str

    model_config = SettingsConfigDict(
        env_file=".env", 
        extra="ignore",
        env_ignore_existing=True,
    )


settings = Settings()


broker_connection_retry_on_startup = True
