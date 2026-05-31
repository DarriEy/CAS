# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Central runtime configuration for CAS.

All settings are read from environment variables prefixed with ``CAS_``
(e.g. ``CAS_PROVIDER_TIMEOUT_S``, ``CAS_AUTH_ENABLED``). Hardening features
(auth, rate limiting) are **off by default** so the same image runs as an
internal tool or a public service depending only on env configuration.

List-valued settings accept either JSON (``["a","b"]``) or a comma-separated
string (``a,b``) for ergonomic shell/Docker use.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAS_", extra="ignore")

    # ── Timeouts (seconds) ──────────────────────────────────────────
    provider_timeout_s: float = 30.0
    """Per-provider extraction deadline; a slow upstream becomes a warning."""
    request_timeout_s: float = 120.0
    """Whole-request backstop deadline."""

    # ── Request limits ──────────────────────────────────────────────
    max_datasets_per_request: int = 50
    max_polygon_vertices: int = 10_000

    # ── Caches ──────────────────────────────────────────────────────
    result_cache_ttl_s: float = 600.0
    result_cache_max_entries: int = 10_000
    metadata_cache_ttl_s: float = 3600.0

    # ── CORS ────────────────────────────────────────────────────────
    cors_origins: Annotated[list[str], NoDecode] = ["*"]

    # ── Auth (off by default) ───────────────────────────────────────
    auth_enabled: bool = False
    api_keys: Annotated[list[str], NoDecode] = []

    # ── Rate limiting (off by default) ──────────────────────────────
    rate_limit_enabled: bool = False
    rate_limit_per_minute: int = 60

    @field_validator("cors_origins", "api_keys", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        """Accept comma-separated strings or JSON arrays from the environment."""
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if s.startswith("["):
                import json

                return json.loads(s)
            return [item.strip() for item in s.split(",") if item.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached)."""
    return Settings()
