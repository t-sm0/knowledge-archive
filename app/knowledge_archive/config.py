from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_user_id: int = Field(alias="TELEGRAM_ALLOWED_USER_ID")
    openrouter_api_key: str = Field(alias="OPENROUTER_API_KEY")
    openrouter_text_model: str = Field(default="openai/gpt-4.1-mini", alias="OPENROUTER_TEXT_MODEL")
    openrouter_vision_model: str = Field(default="openai/gpt-4.1-mini", alias="OPENROUTER_VISION_MODEL")
    database_url: str = Field(alias="DATABASE_URL")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    public_base_url: str | None = Field(default=None, alias="PUBLIC_BASE_URL")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()

