from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COMPLIANCE_", env_file=".env")

    environment: str = "development"
    database_url: str = "sqlite:///./compliance.db"
    auth_mode: Literal["development", "oidc"] = "development"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    model_provider: str = "deterministic"
    model_base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4.1-mini"
    model_api_key: SecretStr | None = None
    model_input_cost_per_million: float = Field(default=0, ge=0)
    model_output_cost_per_million: float = Field(default=0, ge=0)
    confidence_threshold: float = Field(default=0.7, ge=0, le=1)
    max_document_bytes: int = Field(default=262144, ge=1024, le=10485760)
    analysis_lease_seconds: int = Field(default=120, ge=30, le=3600)
    analysis_max_attempts: int = Field(default=3, ge=1, le=10)
    notification_webhook_url: str | None = None
    notification_webhook_secret: SecretStr | None = None
    notification_lease_seconds: int = Field(default=60, ge=15, le=600)
    notification_max_attempts: int = Field(default=5, ge=1, le=20)


@lru_cache
def get_settings() -> Settings:
    return Settings()
