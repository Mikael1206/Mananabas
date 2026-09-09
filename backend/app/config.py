from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    llm_provider: str = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Whisper
    whisper_model_size: str = "small"
    whisper_device: str = "cpu"

    # Storage
    media_dir: str = "./media"
    database_url: str = "sqlite:///./clipforge.db"

    # Clip generation
    max_clips_per_job: int = 5
    clip_min_seconds: int = 20
    clip_max_seconds: int = 90


settings = Settings()
