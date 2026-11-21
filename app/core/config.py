from functools import lru_cache
from ipaddress import ip_network
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    otp_rate_window_minutes: int = Field(default=15, gt=0)
    otp_request_email_limit: int = Field(default=3, gt=0)
    otp_request_ip_limit: int = Field(default=10, gt=0)
    otp_request_cooldown_seconds: int = Field(default=60, gt=0)
    otp_verify_email_limit: int = Field(default=10, gt=0)
    otp_verify_ip_limit: int = Field(default=30, gt=0)
    link_rate_window_minutes: int = Field(default=15, gt=0)
    link_claim_ip_limit: int = Field(default=10, gt=0)
    link_claim_adult_limit: int = Field(default=5, gt=0)
    link_code_issue_profile_limit: int = Field(default=5, gt=0)
    link_code_issue_ip_limit: int = Field(default=20, gt=0)
    profile_create_ip_limit: int = Field(default=10, gt=0)
    otp_trusted_proxy_cidrs: str = ""
    expose_dev_otp: bool = False
    otp_email_provider: Literal["development", "ses"] | None = None
    ses_region: str = "us-east-1"
    ses_sender_email: str | None = None
    ses_timeout_seconds: float = Field(default=5.0, gt=0)
    auto_approve_representative_verification: bool = False
    auto_approve_representative_email_allowlist: str = ""
    device_auth_jwt_secret: SecretStr | None = None
    device_auth_jwt_exp_minutes: int = Field(default=43_200, gt=0)
    device_bootstrap_ttl_minutes: int = Field(default=10, gt=0)
    usage_snapshot_retention_days: int | None = Field(default=None, gt=0)
    f12_evidence_retention_days: int | None = Field(default=None, gt=0)
    analytics_service_url: str = "http://analytics:8765"
    analytics_service_token: SecretStr | None = None
    analytics_timeout_seconds: float = Field(default=5.0, gt=0)
    child_offers_test_enabled: bool = False
    child_offers_test_profile_ids: str = ""
    adaptive_child_offers_enabled: bool = False

    @field_validator("otp_trusted_proxy_cidrs")
    @classmethod
    def validate_otp_trusted_proxy_cidrs(cls, value: str) -> str:
        for part in value.split(","):
            if part.strip():
                ip_network(part.strip(), strict=False)
        return value

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

