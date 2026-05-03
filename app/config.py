from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: SecretStr

    openrouter_api_key: SecretStr = SecretStr("")
    # Text/audio model for OpenRouter (used for voice transcription, etc.)
    openrouter_model: str = "google/gemini-2.5-pro"
    # HTTP timeout for OpenRouter calls. Long voice transcriptions may need
    # > 120s — crank this up if Gemini truncates replies on big audio files.
    openrouter_timeout: float = 300.0

    # Bitrix24 (shared app — singleton client)
    bitrix_client_id: str = ""
    bitrix_client_secret: SecretStr = SecretStr("")
    bitrix_domain: str = ""
    bitrix_refresh_token: str = ""
    bitrix_telegram_field: str = "UF_USR_1678964886664"  # custom field for Telegram username
    # Email-guest scan (im.user.list.get); raise if your portal has >2000 users
    bitrix_email_guests_scan_max: int = 2000
    bitrix_email_guests_multiplier: int = 3  # max_id = max(total_regular*M, scan_max)

    # Jira (integration user)
    jira_url: str = ""
    jira_username: str = ""
    jira_password: SecretStr = SecretStr("")

    # OpenClaw (Glafira)
    openclaw_url: str = ""
    openclaw_token: SecretStr = SecretStr("")
    openclaw_agent_id: str = "main"

    # Potok.io (Recruiter Anatoly)
    potok_api_token: SecretStr = SecretStr("")
    potok_base_url: str = "https://app.potok.io"

    # Claude CLI
    claude_cli_path: str = "claude"
    claude_model: str = ""  # e.g. "claude-opus-4-6", empty = CLI default
    claude_oauth_client_id: str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

    # Claude CLI auth (passed through to subprocess env, not used by app directly)
    claude_code_oauth_token: str = ""
    claude_refresh_token: str = ""

    # Webhook
    webhook_token: str = ""  # shared secret for incoming B24 webhooks

    # Access control (comma-separated Telegram IDs)
    glafira_allowed: str = ""  # e.g. "33570147,367140321"
    recruiter_allowed: str = ""  # e.g. "33570147,367140321,421632942"

    # Database
    db_path: str = "data/arkadyjarvis.db"

    # Scheduler
    summary_hour: int = 19
    summary_minute: int = 0
    timezone: str = "Asia/Novosibirsk"

    # Wednesday frog meme — sent every Wednesday 10:00 local time. 0 = disabled.
    wednesday_frog_chat_id: int = 0

    # Monday motivational Soviet-style poster — sent every Monday 09:00 local time. 0 = disabled.
    monday_poster_chat_id: int = 0

    # Zabbix monitoring channel — bot listens for Zabbix alerts here
    zabbix_channel_id: int = 0  # e.g. -1001423899560
    zabbix_jira_project: str = "DA"
    zabbix_threshold_hours: int = 24

    # Telethon userbot (for sending messages from recruiter's personal account)
    telethon_api_id: int = 0
    telethon_api_hash: str = ""
    telethon_session: str = ""  # StringSession — generate via scripts/create_userbot_session.py
    recruiter_company: str = "Digital Clouds"  # shown in first contact message to candidate
    recruiter_name: str = ""  # recruiter's first name, e.g. "Артём"

    # Socrates meeting analyser
    ffmpeg_bin: str = "ffmpeg"
    meeting_max_minutes: int = 90  # reject longer recordings (OpenRouter base64 limit)

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
