"""Environment-driven configuration.

Assignment section 8: secrets, API keys and credentials must not be hardcoded.
Everything here is read from the environment (or a local ``.env`` that is
git-ignored); ``.env.example`` documents the full set.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---------------------------------------------------
    app_name: str = "EMI Collection Voice Agent"
    environment: str = "development"
    log_level: str = "INFO"
    #: MUST match the Time Zone configured on the Gnani agent. Relative dates
    #: ("today", "tomorrow") are resolved against this, so a mismatch between
    #: the agent's zone and ours puts PTP dates off by a day.
    timezone: str = "America/New_York"

    #: Public base URL of this service (the tunnel, in local dev). Gnani
    #: cannot reach localhost, so callbacks must be registered against this.
    public_base_url: str = "http://localhost:8000"

    # --- Gnani Agents Console ------------------------------------------
    # Note: the Agents Console is a separate product surface from the raw
    # STT/TTS APIs below, with its own credential. The API-key page at
    # app.gnani.ai/voice only issues STT- and TTS-scoped keys, neither of
    # which authenticates against the Agents Console.
    # Real base discovered by inspecting the console's own network traffic;
    # point at http://localhost:9100 to run against tests/mock_gnani instead.
    gnani_base_url: str = "https://api.inya.ai"
    gnani_api_key: SecretStr = SecretStr("")
    #: The console uses this value as both `agentId` and `bot_id`.
    gnani_agent_id: str = ""
    gnani_environment: str = "production"

    #: Undocumented endpoints, captured from the Agents Console UI.
    #: `{agent_id}` is substituted at request time.
    gnani_precall_path: str = "/analytics/add_pre_call_variables"
    gnani_trigger_path: str = "/genbots/trigger_call/v3/{agent_id}"

    gnani_timeout_seconds: float = 10.0
    gnani_max_retries: int = 3

    # --- Gnani speech APIs (api.vachana.ai) -----------------------------
    # Scoped per-capability: the console issues one key per scope, so STT and
    # TTS are distinct credentials. Both use the X-API-Key-ID header.
    # Used for supplementary component evidence, not the call flow itself.
    vachana_base_url: str = "https://api.vachana.ai"
    gnani_stt_api_key: SecretStr = SecretStr("")
    gnani_tts_api_key: SecretStr = SecretStr("")

    # --- Security -------------------------------------------------------
    #: Shared secret Gnani must present on the post-call webhook.
    webhook_api_key: SecretStr = SecretStr("")

    # --- Storage --------------------------------------------------------
    #: "mongo" (assignment-preferred, what docker-compose runs) or "json"
    #: (single-file fallback so the full loop runs with no infrastructure).
    storage_backend: str = "mongo"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "emi_voice_agent"
    json_store_path: str = "data/calls.json"

    # --- Business defaults ----------------------------------------------
    default_language: str = "en-US"
    currency: str = "INR"
    lender_name: str = "ICICI Bank"
    #: Turns of grace before an unanswered prompt is treated as RNR.
    max_ptp_days_ahead: int = Field(
        default=90,
        description="Reject promise-to-pay dates further out than this.",
    )


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so config is parsed once per process."""
    return Settings()
