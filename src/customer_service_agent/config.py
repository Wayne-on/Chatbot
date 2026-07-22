from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded only at the service boundary."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    model_name: str | None = None
    model_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    model_base_url: str | None = None
    model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    model_timeout: float = Field(default=30.0, gt=0.0)
    model_max_retries: int = Field(default=2, ge=0, le=10)
    model_failure_cooldown_seconds: float = Field(default=30.0, ge=0.0, le=300.0)
    model_thinking_enabled: bool = False
    model_routing_mode: Literal["ambiguous_only", "new_scene"] = "ambiguous_only"

    spike_enabled: bool = True
    spike_task_timeout_seconds: float = Field(default=180.0, ge=15.0, le=600.0)
    spike_max_concurrency: int = Field(default=2, ge=1, le=10)
    spike_max_active_runs: int = Field(default=4, ge=1, le=20)
    spike_max_stored_runs: int = Field(default=50, ge=5, le=500)
    spike_run_ttl_seconds: int = Field(default=3600, ge=300, le=86400)
    spike_recursion_limit: int = Field(default=50, ge=10, le=100)

    business_backend: Literal["mock", "http"] = "mock"
    business_api_base_url: str | None = None
    business_service_token: SecretStr | None = None
    business_query_max_retries: int = Field(default=2, ge=0, le=5)

    checkpoint_database_url: SecretStr | None = None
    log_level: str = "INFO"
    max_scene_retries: int = Field(default=3, ge=1, le=10)

    @property
    def model_enabled(self) -> bool:
        return bool(self.model_name and self.effective_model_api_key)

    @property
    def effective_model_api_key(self) -> SecretStr | None:
        return self.model_api_key or self.deepseek_api_key

    @model_validator(mode="after")
    def validate_http_backend(self) -> "Settings":
        if self.business_backend == "http" and not self.business_api_base_url:
            raise ValueError("BUSINESS_API_BASE_URL is required when BUSINESS_BACKEND=http")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
