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
    gemini_model: str = "gemini-3.6-flash"

    # Whisper
    whisper_model_size: str = "small"
    whisper_device: str = "cpu"
    # Empty string = auto-detect. Set to e.g. "tl" or "en" to force a language.
    whisper_language: str = ""

    # Storage
    media_dir: str = "./media"
    database_url: str = "sqlite:///./mananabas.db"

    # Clip generation
    max_clips_per_job: int = 5
    clip_min_seconds: int = 20
    clip_max_seconds: int = 90

    def llm_key_error(self) -> str | None:
        """Return a user-facing error if LLM config is unusable, else None.

        Startup must not raise: Railway restarts the replica if importing
        settings crashes, which shows up as a successful build then CRASHED.
        """
        provider = self.llm_provider.lower()
        providers = {
            "openai": ("OPENAI_API_KEY", self.openai_api_key),
            "anthropic": ("ANTHROPIC_API_KEY", self.anthropic_api_key),
            "gemini": ("GEMINI_API_KEY", self.gemini_api_key),
        }
        if provider not in providers:
            return (
                f"Unknown LLM_PROVIDER '{self.llm_provider}'. "
                f"Expected one of: {', '.join(providers)}."
            )
        env_var, key = providers[provider]
        if not key:
            return (
                f"{env_var} is not set. Add it in Railway Variables "
                f"(or .env locally)."
            )
        return None

    def require_llm(self) -> None:
        err = self.llm_key_error()
        if err:
            raise ValueError(err)


settings = Settings()
