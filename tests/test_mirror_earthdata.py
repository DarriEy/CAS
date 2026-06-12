# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Earthdata (URS) credential-flow tests — hermetic via respx.

The mocked redirect chain reproduces the live-verified NSIDC dance:
data host 302 → ``urs.earthdata.nasa.gov/oauth/authorize`` (credentials
applied here and only here for Basic auth) → 302 back to the data host with
an authorization code → 200 with the payload.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from cas.core.exceptions import MirrorAuthError
from cas.mirror import earthdata
from cas.mirror.earthdata import earthdata_credentials, earthdata_stream

DATA_URL = "https://daacdata.invalid/pub/DATASETS/region.zip"
URS_URL = "https://urs.earthdata.nasa.gov/oauth/authorize?client_id=x"
CODE_URL = "https://daacdata.invalid/oauth/redirect_uri?code=abc123"
PAYLOAD = b"zip-bytes-payload"


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """Tests control credentials explicitly; never read the real ~/.netrc."""
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    monkeypatch.delenv("EARTHDATA_USERNAME", raising=False)
    monkeypatch.delenv("EARTHDATA_PASSWORD", raising=False)
    monkeypatch.setattr(earthdata, "_netrc_credentials", lambda: None)


def _read_stream(url: str) -> bytes:
    with earthdata_stream(url) as response:
        return b"".join(response.iter_bytes())


class TestCredentialDiscovery:
    def test_no_credentials(self):
        assert earthdata_credentials() is None

    def test_token_wins(self, monkeypatch):
        monkeypatch.setenv("EARTHDATA_TOKEN", "tok")
        monkeypatch.setenv("EARTHDATA_USERNAME", "u")
        monkeypatch.setenv("EARTHDATA_PASSWORD", "p")
        assert earthdata_credentials() == ("token", "tok")

    def test_netrc_beats_env_userpass(self, monkeypatch):
        monkeypatch.setattr(earthdata, "_netrc_credentials", lambda: ("nu", "np"))
        monkeypatch.setenv("EARTHDATA_USERNAME", "u")
        monkeypatch.setenv("EARTHDATA_PASSWORD", "p")
        assert earthdata_credentials() == ("netrc", ("nu", "np"))

    def test_env_userpass_fallback(self, monkeypatch):
        monkeypatch.setenv("EARTHDATA_USERNAME", "u")
        monkeypatch.setenv("EARTHDATA_PASSWORD", "p")
        assert earthdata_credentials() == ("basic", ("u", "p"))


class TestURSDance:
    @respx.mock
    def test_basic_credentials_walk_the_redirect_chain(self, monkeypatch):
        monkeypatch.setattr(earthdata, "_netrc_credentials", lambda: ("user", "pass"))
        seen_auth: dict[str, str | None] = {}

        def data_first(request):
            seen_auth["data_first"] = request.headers.get("authorization")
            return httpx.Response(302, headers={"location": URS_URL})

        def urs(request):
            seen_auth["urs"] = request.headers.get("authorization")
            return httpx.Response(302, headers={"location": CODE_URL})

        def code(request):
            seen_auth["code"] = request.headers.get("authorization")
            return httpx.Response(200, content=PAYLOAD)

        respx.get(DATA_URL).mock(side_effect=data_first)
        respx.get(URS_URL).mock(side_effect=urs)
        respx.get(CODE_URL).mock(side_effect=code)

        assert _read_stream(DATA_URL) == PAYLOAD
        # Basic credentials touch ONLY the URS host — never the data host.
        assert seen_auth["data_first"] is None
        assert seen_auth["urs"] is not None and seen_auth["urs"].startswith("Basic ")
        assert seen_auth["code"] is None

    @respx.mock
    def test_relative_redirect_location_is_resolved(self, monkeypatch):
        monkeypatch.setattr(earthdata, "_netrc_credentials", lambda: ("user", "pass"))
        respx.get(DATA_URL).mock(
            return_value=httpx.Response(302, headers={"location": "/oauth/redirect_uri?code=abc123"})
        )
        respx.get(CODE_URL).mock(return_value=httpx.Response(200, content=PAYLOAD))
        assert _read_stream(DATA_URL) == PAYLOAD

    @respx.mock
    def test_bearer_token_sent_on_every_hop(self, monkeypatch):
        monkeypatch.setenv("EARTHDATA_TOKEN", "sekrit-token")
        seen: list[str | None] = []

        def record(request):
            seen.append(request.headers.get("authorization"))
            if len(seen) == 1:
                return httpx.Response(302, headers={"location": CODE_URL})
            return httpx.Response(200, content=PAYLOAD)

        respx.get(DATA_URL).mock(side_effect=record)
        respx.get(CODE_URL).mock(side_effect=record)
        assert _read_stream(DATA_URL) == PAYLOAD
        assert seen == ["Bearer sekrit-token", "Bearer sekrit-token"]

    @respx.mock
    def test_401_without_credentials_is_actionable(self):
        respx.get(DATA_URL).mock(return_value=httpx.Response(401))
        with pytest.raises(MirrorAuthError) as exc:
            _read_stream(DATA_URL)
        msg = str(exc.value)
        assert "urs.earthdata.nasa.gov/users/new" in msg
        assert "EARTHDATA_TOKEN" in msg
        assert "~/.netrc" in msg
        assert "never stored" in msg

    @respx.mock
    def test_401_with_rejected_credentials_names_the_source(self, monkeypatch):
        monkeypatch.setattr(earthdata, "_netrc_credentials", lambda: ("user", "wrong"))
        respx.get(DATA_URL).mock(
            return_value=httpx.Response(302, headers={"location": URS_URL})
        )
        respx.get(URS_URL).mock(return_value=httpx.Response(401))
        with pytest.raises(MirrorAuthError) as exc:
            _read_stream(DATA_URL)
        msg = str(exc.value)
        assert "rejected" in msg
        assert "netrc" in msg
        # The password itself never leaks into the error.
        assert "wrong" not in msg

    @respx.mock
    def test_redirect_loop_bails_out(self, monkeypatch):
        monkeypatch.setenv("EARTHDATA_TOKEN", "tok")
        respx.get(DATA_URL).mock(
            return_value=httpx.Response(302, headers={"location": DATA_URL})
        )
        with pytest.raises(MirrorAuthError, match="exceeded"):
            _read_stream(DATA_URL)

    @respx.mock
    def test_http_error_propagates(self, monkeypatch):
        monkeypatch.setenv("EARTHDATA_TOKEN", "tok")
        respx.get(DATA_URL).mock(return_value=httpx.Response(503))
        with pytest.raises(httpx.HTTPStatusError):
            _read_stream(DATA_URL)
