from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Child Usage Analysis API"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/tesis"
    api_v1_prefix: str = "/api/v1"
    device_link_code_ttl_minutes: int = Field(default=10, gt=0)
    device_link_code_hash_secret: SecretStr = SecretStr("development-only-change-before-production")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

