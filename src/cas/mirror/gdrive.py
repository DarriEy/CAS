# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Google Drive public-file download flow (no API key).

MERIT-Basins moved off ``hydrology.princeton.edu`` (DNS NXDOMAIN since 2026)
to Google Drive folders linked from reachhydro.org, with Globus as the only
alternative. The Drive *files* are public and have stable ids, but files
larger than the virus-scan limit (~100 MB) answer a plain GET with an HTML
interstitial ("Google Drive can't scan this file for viruses") carrying a
confirm form instead of the bytes. This module walks that dance:

1. GET the canonical ``drive.google.com/uc?export=download&id=…`` URL.
2. If the response is the file (non-HTML), stream it.
3. If it is the virus-scan interstitial, parse the form (action +
   hidden ``id``/``export``/``confirm``/``uuid`` fields — no API key, no
   cookies needed for public files) and stream the form target.
4. If it is any other HTML page — the documented quota interstitial
   ("too many users have viewed or downloaded this file") or a permission
   page — fail with an actionable :class:`MirrorError` naming the
   ``cas mirror import`` escape hatch.

Verified live against the reachhydro MERIT-Basins folder (2026-06-12):
interstitial form fields and the ``drive.usercontent.google.com/download``
action as implemented below.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import structlog

from cas.core.exceptions import MirrorError

logger = structlog.get_logger(__name__)

_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)

_GDRIVE_HOSTS = ("drive.google.com", "drive.usercontent.google.com")

# The interstitial is a small page; anything bigger is not one.
_MAX_INTERSTITIAL_BYTES = 256 * 1024

_FORM_ACTION_RE = re.compile(r'<form[^>]*\baction="([^"]+)"', re.IGNORECASE)
_FORM_FIELD_RE = re.compile(
    r'<input[^>]*\bname="([^"]+)"[^>]*\bvalue="([^"]*)"', re.IGNORECASE
)


def is_gdrive_url(url: str) -> bool:
    """Whether a mirror source URL is served from Google Drive."""
    return httpx.URL(url).host in _GDRIVE_HOSTS


def gdrive_quota_error(url: str) -> MirrorError:
    return MirrorError(
        f"Google Drive is rate-limiting downloads of {url} "
        f"('download quota exceeded' interstitial). This clears on Drive's "
        f"side, typically within 24 hours — retry later, or obtain the file "
        f"yourself (e.g. via the reachhydro.org Globus collection) and stage "
        f"it with `cas mirror import`."
    )


def _parse_interstitial(body: str, url: str) -> tuple[str, dict[str, str]]:
    """Extract (action URL, params) from the virus-scan confirm form."""
    lowered = body.lower()
    if "quota" in lowered or "too many users" in lowered:
        raise gdrive_quota_error(url)
    action = _FORM_ACTION_RE.search(body)
    fields = dict(_FORM_FIELD_RE.findall(body))
    if not action or "id" not in fields:
        raise MirrorError(
            f"Google Drive answered {url} with an HTML page that is neither "
            f"the file nor the known virus-scan confirm form — the file may "
            f"be private, removed, or Drive changed its interstitial. "
            f"Obtain the archive manually and stage it with `cas mirror import`."
        )
    fields.setdefault("export", "download")
    fields.setdefault("confirm", "t")
    return action.group(1), fields


@contextmanager
def gdrive_stream(url: str) -> Iterator[httpx.Response]:
    """Stream a public Google Drive file, handling the confirm interstitial.

    Yields the final streamed ``httpx.Response`` (status already checked).
    Raises an actionable :class:`MirrorError` on the quota interstitial or
    an unrecognized HTML answer.
    """
    with httpx.Client(follow_redirects=True, timeout=_TIMEOUT) as client:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                yield response
                return
            # Interstitial: small HTML page — read it fully and parse.
            body = b""
            for chunk in response.iter_bytes(65536):
                body += chunk
                if len(body) > _MAX_INTERSTITIAL_BYTES:
                    break
        action, fields = _parse_interstitial(
            body.decode("utf-8", errors="replace"), url
        )
        logger.info("mirror.gdrive_confirm", url=url, action=action)
        with client.stream("GET", action, params=fields) as confirmed:
            confirmed.raise_for_status()
            if "text/html" in confirmed.headers.get("content-type", ""):
                # A second interstitial is the quota page in practice.
                raise gdrive_quota_error(url)
            yield confirmed
