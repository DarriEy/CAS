# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""CAS exception hierarchy."""

from __future__ import annotations


class CASError(Exception):
    """Base exception for all CAS errors."""


class ConnectorError(CASError):
    """Raised when a data provider connector fails."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class RateLimitError(ConnectorError):
    """Provider rate-limited us — triggers automatic retry."""


class DataFormatError(ConnectorError):
    """Provider response doesn't match expected format."""


class ProtocolError(ConnectorError):
    """Protocol-level operation failed (WCS, STAC, OPeNDAP)."""


class RasterUnsupportedError(ConnectorError):
    """Provider/protocol cannot produce raster output (capability error).

    v1 raster mode covers STAC/COG and WCS connectors only; every other
    protocol is stats-only and raster requests to it raise this error.
    """


class GeometryError(CASError):
    """Input geometry is invalid or unsupported."""


class ExtractionError(CASError):
    """Extraction engine encountered an error."""


class RequestLimitError(CASError):
    """Request exceeds a configured limit (too many datasets, too many vertices)."""


class QCError(CASError):
    """QC validation failed."""


class CacheError(CASError):
    """Metadata cache operation failed."""


class RegistrationRequiredError(ConnectorError):
    """Provider requires registration/API key that is not configured."""

    def __init__(self, provider: str, registration_url: str, instructions: str) -> None:
        self.registration_url = registration_url
        self.instructions = instructions
        super().__init__(
            provider,
            f"Registration required. Register at: {registration_url}\n{instructions}",
        )


# ── Curated mirror tier ─────────────────────────────────────────────


class MirrorError(CASError):
    """Base exception for the curated-mirror tier."""


class MirrorDatasetNotFoundError(MirrorError):
    """Requested dataset (or pinned version) is not in the mirror registry."""


class MirrorOfflineError(MirrorError):
    """Materialization required but CAS_MIRROR_OFFLINE is set."""


class MirrorWriteError(MirrorError):
    """Mirror root is not writable and the dataset is not materialized."""


class MirrorIntegrityError(MirrorError):
    """Checksum or size mismatch against the registry or manifest.

    Carries ``expected`` and ``actual`` hashes — never silently accepted.
    """

    def __init__(self, message: str, *, expected: str | None = None, actual: str | None = None) -> None:
        self.expected = expected
        self.actual = actual
        if expected or actual:
            message = f"{message} (expected sha256={expected}, actual sha256={actual})"
        super().__init__(message)


class MirrorLicenseError(MirrorError):
    """Dataset license terms require explicit acknowledgment that is absent."""


class MirrorDependencyError(MirrorError):
    """The optional [mirror] dependencies (geopandas/pyarrow) are missing."""
