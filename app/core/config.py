from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Child Usage Analysis API"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/tesis"
    api_v1_prefix: str = "/api/v1"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

