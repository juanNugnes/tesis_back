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
    adult_auth_jwt_secret: SecretStr | None = None
    adult_auth_jwt_exp_minutes: int = Field(default=60, gt=0)
    adult_otp_ttl_minutes: int = Field(default=10, gt=0)
    adult_otp_max_attempts: int = Field(default=5, gt=0)
    expose_dev_otp: bool = False
    auto_approve_representative_verification: bool = False
    device_auth_jwt_secret: SecretStr | None = None
    device_auth_jwt_exp_minutes: int = Field(default=43_200, gt=0)
    device_bootstrap_ttl_minutes: int = Field(default=10, gt=0)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

