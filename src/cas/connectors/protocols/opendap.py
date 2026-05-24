# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""OPeNDAP constraint expression builder and response parser mixin."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


class OPeNDAPMixin:
    """Mixin for building OPeNDAP constraint expressions.

    Used by connectors that access data through OPeNDAP/THREDDS endpoints.
    """

    @staticmethod
    def _opendap_constraint(
        variable: str,
        lat_range: tuple[int, int],
        lon_range: tuple[int, int],
        time_index: int | None = None,
    ) -> str:
        parts = [variable]
        if time_index is not None:
            parts.append(f"[{time_index}]")
        parts.append(f"[{lat_range[0]}:{lat_range[1]}]")
        parts.append(f"[{lon_range[0]}:{lon_range[1]}]")
        return "".join(parts)

    async def _opendap_fetch_array(
        self,
        base_url: str,
        constraint: str,
    ) -> bytes:
        url = f"{base_url}.dods?{constraint}"
        return await self._get_bytes(url)  # type: ignore[attr-defined, no-any-return]
