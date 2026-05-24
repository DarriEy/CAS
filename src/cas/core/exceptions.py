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


class GeometryError(CASError):
    """Input geometry is invalid or unsupported."""


class ExtractionError(CASError):
    """Extraction engine encountered an error."""


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
