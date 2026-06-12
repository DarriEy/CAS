# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""NASA Earthdata Login (URS) credential flow for mirror downloads (design §4).

NSIDC's ``daacdata.apps.nsidc.org`` (RGI 7.0) gates downloads behind Earthdata
Login. The flow is redirect-based: the data request bounces to
``urs.earthdata.nasa.gov`` to authenticate, then back to the data host with an
authorization code and a session cookie (verified live against the RGI 7.0
regional-files directory: 302 → URS ``/oauth/authorize`` → 302 back with
``code=…`` → 200).

httpx strips ``Authorization`` on cross-host redirects, so this module walks
the redirect chain manually and applies credentials per host:

- a **bearer token** (``EARTHDATA_TOKEN``) is valid on every hop — NASA hosts
  accept it directly;
- **Basic** credentials (``~/.netrc`` machine ``urs.earthdata.nasa.gov``, or
  ``EARTHDATA_USERNAME``/``EARTHDATA_PASSWORD``) are applied **only** on the
  URS host, never leaked to other hosts.

Credential precedence matches the CFS/native-handler convention:
``EARTHDATA_TOKEN`` → ``~/.netrc`` → ``EARTHDATA_USERNAME``/``PASSWORD``.
A 401 with no credentials raises an actionable :class:`MirrorAuthError`
(how to get an account, where credentials go). Credentials are **never**
written to manifests or logs (design §4: the mirror fetches with the *user's*
Earthdata credentials; creds passed in, never stored).
"""

from __future__ import annotations

import base64
import netrc
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import structlog

from cas.core.exceptions import MirrorAuthError

logger = structlog.get_logger(__name__)

URS_HOST = "urs.earthdata.nasa.gov"
_URS_REGISTER = "https://urs.earthdata.nasa.gov/users/new"
_MAX_REDIRECTS = 10
_TIMEOUT = httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0)

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def _netrc_credentials() -> tuple[str, str] | None:
    """``~/.netrc`` credentials for the URS host, if present and parseable."""
    try:
        auth = netrc.netrc().authenticators(URS_HOST)
    except (FileNotFoundError, netrc.NetrcParseError):
        return None
    if auth and auth[0] and auth[2]:
        return (auth[0], auth[2])
    return None


def earthdata_credentials() -> tuple[str, object] | None:
    """Detect Earthdata credentials.

    Returns ``("token", str)``, ``("netrc"|"basic", (user, password))``, or
    ``None``. Precedence: ``EARTHDATA_TOKEN`` → ``~/.netrc`` →
    ``EARTHDATA_USERNAME``/``EARTHDATA_PASSWORD``.
    """
    import os

    token = os.environ.get("EARTHDATA_TOKEN")
    if token:
        return ("token", token)
    userpass = _netrc_credentials()
    if userpass:
        return ("netrc", userpass)
    user = os.environ.get("EARTHDATA_USERNAME")
    pw = os.environ.get("EARTHDATA_PASSWORD")
    if user and pw:
        return ("basic", (user, pw))
    return None


def missing_credentials_error(url: str) -> MirrorAuthError:
    """The actionable no-credentials refusal (design §4 credential pattern)."""
    return MirrorAuthError(
        f"Downloading {url} requires a (free) NASA Earthdata Login account, "
        f"and no credentials were found.\n"
        f"  1. Register at {_URS_REGISTER}\n"
        f"  2. Provide credentials via ONE of:\n"
        f"     - export EARTHDATA_TOKEN=<token>  (URS → My Profile → Generate Token)\n"
        f"     - ~/.netrc line: machine {URS_HOST} login <user> password <pass>\n"
        f"     - EARTHDATA_USERNAME and EARTHDATA_PASSWORD environment variables\n"
        f"CAS uses your credentials only to download on your behalf; they are "
        f"never stored in the mirror or its manifests."
    )


def _basic_header(userpass: tuple[str, str]) -> str:
    raw = f"{userpass[0]}:{userpass[1]}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _hop_headers(url: str, kind: str | None, value: object | None) -> dict[str, str]:
    """Authorization header for one redirect hop, per the host rules above."""
    if kind == "token":
        return {"Authorization": f"Bearer {value}"}
    if kind in ("netrc", "basic") and httpx.URL(url).host == URS_HOST:
        return {"Authorization": _basic_header(value)}  # type: ignore[arg-type]
    return {}


@contextmanager
def earthdata_stream(url: str) -> Iterator[httpx.Response]:
    """Stream an Earthdata-gated URL, walking the URS redirect dance.

    Yields the final streamed ``httpx.Response`` (status already raised on
    HTTP errors). Raises :class:`MirrorAuthError` on 401/403 — actionable
    when no credentials exist, diagnostic when they were rejected.
    """
    cred = earthdata_credentials()
    kind, value = cred if cred else (None, None)

    with httpx.Client(follow_redirects=False, timeout=_TIMEOUT) as client:
        target = url
        for _hop in range(_MAX_REDIRECTS):
            with client.stream("GET", target, headers=_hop_headers(target, kind, value)) as response:
                if response.status_code in _REDIRECT_CODES:
                    location = response.headers.get("location", "")
                    if not location:
                        raise MirrorAuthError(
                            f"Redirect from {target} carried no Location header."
                        )
                    target = str(httpx.URL(target).join(location))
                    logger.debug("earthdata.redirect", to=httpx.URL(target).host)
                    continue
                if response.status_code in (401, 403):
                    if cred is None:
                        raise missing_credentials_error(url)
                    raise MirrorAuthError(
                        f"NASA Earthdata rejected the configured credentials "
                        f"(HTTP {response.status_code} from {httpx.URL(target).host}, "
                        f"credential source: {kind}). Check the account/token at "
                        f"https://{URS_HOST} and retry."
                    )
                response.raise_for_status()
                yield response
                return
        raise MirrorAuthError(
            f"Earthdata redirect chain for {url} exceeded {_MAX_REDIRECTS} hops."
        )
