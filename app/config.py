from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: SecretStr

    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-5.2"

    openrouter_api_key: SecretStr = SecretStr("")

    # Bitrix24 (shared app — singleton client)
    bitrix_client_id: str = ""
    bitrix_client_secret: SecretStr = SecretStr("")
    bitrix_domain: str = ""
    bitrix_refresh_token: str = ""

    # Jira (integration user)
    jira_url: str = ""
    jira_username: str = ""
    jira_password: SecretStr = SecretStr("")

    # Encryption
    encryption_key: str = ""

    # Database
    db_path: str = "data/arkadyjarvis.db"

    # Scheduler
    summary_hour: int = 19
    summary_minute: int = 0
    timezone: str = "Asia/Novosibirsk"

    @field_validator("summary_hour")
    @classmethod
    def validate_summary_hour(cls, v: int) -> int:
        if not 0 <= v <= 23:
            raise ValueError(f"summary_hour must be 0–23, got {v}")
        return v

    @field_validator("summary_minute")
    @classmethod
    def validate_summary_minute(cls, v: int) -> int:
        if not 0 <= v <= 59:
            raise ValueError(f"summary_minute must be 0–59, got {v}")
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (KeyError, Exception):
            raise ValueError(f"Invalid IANA timezone: {v}")
        return v

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
