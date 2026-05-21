"""Environment / settings for the public subsidy-brain build.

All integrations (Google Sheets, Drive, LINE WORKS, scheduler) are optional in
this public build. Only the Anthropic API key is needed to drive the demo.
"""

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Anthropic Claude API (the only credential strictly required for the demo)
    anthropic_api_key: str = Field(default="", description="Anthropic API Key")

    # Perplexity API (optional — used by fact_checker for live citations)
    perplexity_api_key: str = Field(default="", description="Perplexity API Key")

    # Google APIs (optional in the public build; used only when the private
    # orchestrator is wired to Sheets/Drive)
    google_service_account_json: str = Field(default="config/service_account.json")
    google_sheet_id: str = Field(default="")
    google_drive_root_folder_id: str = Field(default="")

    # App
    app_env: str = Field(default="development")
    app_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    # Claude defaults
    default_model: str = Field(default="claude-sonnet-4-6")
    default_max_tokens: int = Field(default=4096)
    default_temperature: float = Field(default=0.3)

    # Self-improving skill store (the learning layer is part of the private
    # roadmap; off by default in the public build)
    skill_injection_enabled: bool = Field(default=False)

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
