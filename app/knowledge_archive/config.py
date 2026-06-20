from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_user_id: int = Field(alias="TELEGRAM_ALLOWED_USER_ID")
    openrouter_api_key: str = Field(alias="OPENROUTER_API_KEY")
    openrouter_text_model: str = Field(
        default="deepseek/deepseek-v4-flash",
        alias="OPENROUTER_TEXT_MODEL",
    )
    openrouter_reasoning_model: str = Field(
        default="z-ai/glm-5.2",
        alias="OPENROUTER_REASONING_MODEL",
    )
    openrouter_vision_model: str = Field(
        default="minimax/minimax-m3",
        alias="OPENROUTER_VISION_MODEL",
    )
    openrouter_embedding_model: str = Field(
        default="google/gemini-embedding-2",
        alias="OPENROUTER_EMBEDDING_MODEL",
    )
    openrouter_embedding_dimensions: int = Field(
        default=1024,
        alias="OPENROUTER_EMBEDDING_DIMENSIONS",
    )
    embeddings_enabled: bool = Field(default=True, alias="EMBEDDINGS_ENABLED")
    database_url: str = Field(alias="DATABASE_URL")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    public_base_url: str | None = Field(default=None, alias="PUBLIC_BASE_URL")
    instagram_cookies_file: Path | None = Field(
        default=Path("/app/data/secrets/instagram-cookies.txt"),
        alias="INSTAGRAM_COOKIES_FILE",
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
