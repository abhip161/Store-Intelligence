from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STORE_INTEL_", env_file=".env.example")

    env: str = "development"
    database_path: str = "./data/store_intelligence.db"
    log_level: str = "INFO"
    default_timezone: str = "UTC"
    stale_feed_minutes: int = 10
    pos_conversion_window: int | None = Field(default=None, ge=0)
    pos_conversion_window_minutes: int = Field(default=5, ge=0)
    dwell_emit_seconds: int = 30
    max_ingest_batch_size: int = Field(default=500, ge=1, le=500)

    @property
    def effective_pos_conversion_window_minutes(self) -> int:
        return (
            self.pos_conversion_window
            if self.pos_conversion_window is not None
            else self.pos_conversion_window_minutes
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
