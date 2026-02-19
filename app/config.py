from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str

    openai_api_key: str = ""
    openai_model: str = "gpt-5.2"

    # Bitrix24 (shared app — singleton client)
    bitrix_client_id: str = ""
    bitrix_client_secret: str = ""
    bitrix_domain: str = ""
    bitrix_refresh_token: str = ""

    # Jira (shared URL)
    jira_url: str = ""

    # Database
    db_path: str = "data/arkadyjarvis.db"

    # Scheduler
    summary_hour: int = 19
    summary_minute: int = 0
    timezone: str = "Asia/Novosibirsk"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
