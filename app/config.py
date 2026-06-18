"""Application configuration, loaded from environment / .env.

Defaults are chosen so the service runs out of the box in `mock` LLM mode
(no Azure credentials required). Set LLM_MODE=azure plus the AZURE_* values
to use a real model.
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- LLM ---------------------------------------------------------------
    # "mock" runs a deterministic planner (no external calls); "azure" uses Azure OpenAI.
    llm_mode: str = Field("mock", alias="LLM_MODE")
    azure_openai_endpoint: str | None = Field(None, alias="AZURE_OPENAI_ENDPOINT")
    azure_openai_api_key: str | None = Field(None, alias="AZURE_OPENAI_API_KEY")
    azure_openai_deployment: str = Field("gpt-4o", alias="AZURE_OPENAI_DEPLOYMENT")
    azure_openai_api_version: str = Field("2024-10-21", alias="AZURE_OPENAI_API_VERSION")
    llm_temperature: float = Field(0.0, alias="LLM_TEMPERATURE")

    # ---- Database ----------------------------------------------------------
    database_url: str = Field(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agentic",
        alias="DATABASE_URL",
    )

    # ---- Business rules ----------------------------------------------------
    # Estimated account value at/above which ticket creation needs human approval.
    high_value_threshold: float = Field(10_000.0, alias="HIGH_VALUE_THRESHOLD")

    # ---- External systems --------------------------------------------------
    # If set, enrichment calls hit this URL (?domain=...); otherwise a mock is used.
    enrichment_api_url: str | None = Field(None, alias="ENRICHMENT_API_URL")
    # If set, the CRM client calls HubSpot; otherwise a mock dataset is used.
    hubspot_token: str | None = Field(None, alias="HUBSPOT_TOKEN")

    # ---- Resilience --------------------------------------------------------
    max_retries: int = Field(4, alias="MAX_RETRIES")
    retry_base_delay: float = Field(0.5, alias="RETRY_BASE_DELAY")
    retry_max_delay: float = Field(8.0, alias="RETRY_MAX_DELAY")
    request_timeout: float = Field(10.0, alias="REQUEST_TIMEOUT")


settings = Settings()
