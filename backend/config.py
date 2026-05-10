"""Centralised settings, loaded from environment variables.

All other modules import from here. Never read os.environ directly outside this file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Top-level application settings.

    Loaded from `.env` in development. In production, values come from the
    container environment.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM providers ---
    # Strategy: Groq (free tier) primary, local Ollama on host as fallback.
    # No paid APIs in the project.
    groq_api_key: SecretStr = Field(default=SecretStr(""))
    groq_model_primary: str = "llama-3.1-8b-instant"
    groq_model_cheap: str = "llama-3.1-8b-instant"  # for Tier-1 single-fact lookups

    # Ollama runs on the host (not in docker-compose). Backend container reaches
    # it via host.docker.internal; on host runs use localhost.
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct-q5_K_M"
    ollama_model_heavy: str = "qwen2.5:14b-instruct-q4_K_M"  # opt-in, not default

    # Provider routing: which provider services which role.
    llm_primary_provider: str = "groq"      # groq | ollama
    llm_fallback_provider: str = "ollama"   # groq | ollama | none

    # --- SEC EDGAR ---
    # IMPORTANT: SEC fair-use mandates a real contact string. Code that hits
    # EDGAR must validate this is non-default before making the first request.
    sec_user_agent: str = "REPLACE_ME Your Name your.email@example.com"

    # --- Postgres ---
    database_url: str = (
        "postgresql+asyncpg://ffa:ffa_dev_password_change_me@localhost:5432/filings"
    )

    # --- Qdrant ---
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: SecretStr = Field(default=SecretStr(""))
    qdrant_collection: str = "filings_chunks"

    # --- Langfuse ---
    langfuse_host: str = "http://localhost:3001"
    langfuse_public_key: SecretStr = Field(default=SecretStr(""))
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""))

    # --- Embeddings / reranker ---
    embedding_model: str = "BAAI/bge-large-en-v1.5"
    reranker_model: str = "BAAI/bge-reranker-large"
    embedding_device: str = "auto"  # auto | cpu | cuda

    # --- Runtime ---
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    log_level: str = "INFO"
    environment: str = "development"

    # --- Ticker universe ---
    tickers: str = (
        "MSFT,AAPL,GOOGL,AMZN,META,NVDA,TSLA,AMD,INTC,CRM,ORCL,"
        "JPM,BAC,WMT,COST,JNJ,PFE,CAT,XOM,LLY"
    )
    filing_years: str = "2020,2021,2022,2023,2024"

    @property
    def ticker_list(self) -> list[str]:
        return [t.strip().upper() for t in self.tickers.split(",") if t.strip()]

    @property
    def fiscal_year_list(self) -> list[int]:
        return [int(y.strip()) for y in self.filing_years.split(",") if y.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def assert_sec_user_agent_set(self) -> None:
        """Raise if SEC_USER_AGENT still has the placeholder value.

        Call this at the top of any function that hits EDGAR. SEC will IP-ban
        on bad / missing User-Agent headers, better to fail fast locally.
        """
        if self.sec_user_agent.startswith("REPLACE_ME"):
            raise RuntimeError(
                "SEC_USER_AGENT not configured. "
                "Set it in .env to 'Your Name your.email@example.com' before "
                "making EDGAR requests. SEC fair-use compliance is mandatory."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor, settings are immutable for the process lifetime."""
    return Settings()


# Convenience constants
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
RAW_FILINGS_DIR: Path = DATA_DIR / "raw"
