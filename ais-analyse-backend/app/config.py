from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://ais_user:ais_password@localhost:5432/ais_db"
    cors_origins: list[str] = ["http://localhost:5173"]
    max_query_days: int = 30
    default_page_size: int = 20
    max_page_size: int = 100
    prediction_default_minutes: int = 60
    prediction_step_minutes: int = 5

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
