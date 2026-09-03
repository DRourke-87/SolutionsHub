from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field(default="dev", description="dev | test | prod")
    app_name: str = "SolutionsHub"
    secret_key: str = Field(default="dev-only-insecure-secret-key-change-me", min_length=16)
    base_url: str = "http://localhost:8000"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/solutionshub"

    # Sign-in
    allowed_email_domains: str = "amentum.com,global.amentum.com,amentumcms.com"
    bootstrap_admin_email: str | None = None
    magic_link_ttl_minutes: int = 15
    session_sliding_hours: int = 8
    session_absolute_hours: int = 24
    remember_me_days: int = 30
    login_rate_per_email_per_hour: int = 5
    login_rate_per_ip_per_hour: int = 30
    verify_failures_per_ip_per_hour: int = 10

    # Email
    email_backend: str = "console"  # console | acs
    acs_endpoint: str | None = None
    acs_connection_string: str | None = None
    acs_sender: str = "DoNotReply@example.com"

    # Attachments
    storage_backend: str = "local"  # local | azure
    local_storage_path: str = "./var/uploads"
    azure_storage_connection_string: str | None = None  # key-based auth (default in the Bicep deployment)
    azure_storage_account_url: str | None = None  # alternative: managed identity via DefaultAzureCredential
    azure_storage_container: str = "attachments"
    max_attachments_per_submission: int = 10
    max_attachment_mb: int = 25
    allowed_attachment_extensions: str = "pdf,doc,docx,xls,xlsx,ppt,pptx,txt,csv,md,png,jpg,jpeg,gif,svg,zip,vsdx,mp4"

    # Workflow timing (business days unless stated)
    reminder_owner_days: int = 5
    reminder_reviewer_pool_days: int = 3
    reminder_assigned_reviewer_days: int = 5
    reminder_approver_days: int = 5
    reminder_publisher_days: int = 5
    review_cycle_months: int = 6
    review_notice_days_before: int = 14

    scheduler_enabled: bool = True
    applicationinsights_connection_string: str | None = None
    log_level: str = "INFO"

    @field_validator("allowed_email_domains", "allowed_attachment_extensions")
    @classmethod
    def _normalise_csv(cls, v: str) -> str:
        return ",".join(p.strip().lower().lstrip("@.") for p in v.split(",") if p.strip())

    @property
    def allowed_domains(self) -> set[str]:
        return {d for d in self.allowed_email_domains.split(",") if d}

    @property
    def allowed_extensions(self) -> set[str]:
        return {e for e in self.allowed_attachment_extensions.split(",") if e}

    @property
    def is_prod(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.lower().startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
