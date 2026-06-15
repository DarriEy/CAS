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
from pathlib import Path
from typing import Annotated, Any

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

    # ── Curated mirror tier (CAS_MIRROR_*) ──────────────────────────
    mirror_dir: Path = Path("~/.cas/mirror")
    """Mirror root (``CAS_MIRROR_DIR``). Per-user by default; point it at a
    read-only group share to consume an admin-synced mirror."""
    mirror_offline: bool = False
    """``CAS_MIRROR_OFFLINE`` — materialization becomes a hard error instead
    of a download (compute-node safety). Reads of materialized data proceed."""
    mirror_auto_materialize: bool = True
    """Set False to forbid lazy download-on-first-use; only an explicit
    ``cas mirror sync`` materializes datasets."""
    mirror_accept_licenses: Annotated[list[str], NoDecode] = []
    """``CAS_MIRROR_ACCEPT_LICENSES`` — slugs of acknowledgment-requiring
    datasets whose license terms the user pre-accepts (comma-separated),
    for non-interactive sync."""

    @field_validator("mirror_dir", mode="after")
    @classmethod
    def _expand_mirror_dir(cls, v: Path) -> Path:
        return v.expanduser()

    @field_validator("cors_origins", "api_keys", "mirror_accept_licenses", mode="before")
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


_overrides: dict[str, Any] = {}
"""Programmatic overrides applied on top of the environment (see configure)."""


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton (cached).

    Values are read from ``CAS_``-prefixed environment variables, overlaid
    with any programmatic overrides applied via :func:`configure`.
    """
    return Settings(**_overrides)


def configure(**overrides: object) -> Settings:
    """Apply runtime configuration overrides and refresh the settings singleton.

    Because :func:`get_settings` is cached, environment variables are normally
    read **once**, at first use — env vars set after CAS has been imported and
    used are silently ignored. Embedding frameworks (e.g. a SYMFLUENCE adapter)
    can call ``cas.configure(...)`` at any time to (re)configure CAS:

    - keyword arguments name :class:`Settings` fields directly and take
      precedence over the environment (validated by pydantic);
    - calling ``configure()`` with no arguments simply clears the cache so the
      current environment is re-read.

    Overrides accumulate across calls. The next extraction picks up the new
    settings (the engine's result cache is rebuilt against the refreshed
    settings on first use).

    Returns the refreshed :class:`Settings` instance.

    >>> import cas
    >>> cas.configure(provider_timeout_s=60, result_cache_ttl_s=0)
    """
    unknown = sorted(set(overrides) - set(Settings.model_fields))
    if unknown:
        valid = ", ".join(sorted(Settings.model_fields))
        raise TypeError(
            f"Unknown CAS setting(s): {', '.join(unknown)}. Valid settings: {valid}"
        )
    _overrides.update(overrides)
    get_settings.cache_clear()
    return get_settings()
