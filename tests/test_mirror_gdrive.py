# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Google Drive public-file download flow tests (MERIT-Basins distribution).

Hermetic: the Drive endpoints are mocked with respx — direct content, the
virus-scan confirm interstitial (form fields captured from a live probe,
2026-06-12), and the quota interstitial.
"""

from __future__ import annotations

import httpx
import pytest
import respx

pytest.importorskip("geopandas")
pytest.importorskip("pyarrow")

from cas.core.exceptions import MirrorError
from cas.mirror.gdrive import gdrive_stream, is_gdrive_url

from .mirror_utils import build_fake_zip, make_fake_dataset, registered

UC_URL = "https://drive.google.com/uc?export=download&id=FAKEID"
USERCONTENT_URL = "https://drive.usercontent.google.com/download"

# Captured live from the MERIT-Basins pfaf_9 interstitial (2026-06-12).
INTERSTITIAL = """<html><head><title>Google Drive - Virus scan warning</title></head>
<body><form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
<input type="submit" id="uc-download-link" value="Download anyway"/>
<input type="hidden" name="id" value="FAKEID"/>
<input type="hidden" name="export" value="download"/>
<input type="hidden" name="confirm" value="t"/>
<input type="hidden" name="uuid" value="44ac62aa-2012-4e0c-bc54-2d5fb669b817"/>
</form></body></html>"""

QUOTA_PAGE = """<html><head><title>Google Drive - Quota exceeded</title></head>
<body>Sorry, you can't view or download this file at this time. Too many users
have viewed or downloaded this file recently.</body></html>"""


def _read_all(url: str) -> bytes:
    with gdrive_stream(url) as response:
        return b"".join(response.iter_bytes())


class TestGdriveUrlDetection:
    def test_drive_hosts_detected(self):
        assert is_gdrive_url(UC_URL)
        assert is_gdrive_url(USERCONTENT_URL + "?id=x")
        assert not is_gdrive_url("https://data.hydrosheds.org/file/x.zip")
        assert not is_gdrive_url("https://example.com/drive.google.com/x")


class TestGdriveStream:
    @respx.mock
    def test_direct_content_passthrough(self):
        respx.get(UC_URL).mock(
            return_value=httpx.Response(
                200, content=b"PK\x03\x04zipbytes",
                headers={"content-type": "application/octet-stream"},
            )
        )
        assert _read_all(UC_URL) == b"PK\x03\x04zipbytes"

    @respx.mock
    def test_virus_scan_interstitial_walked(self):
        respx.get(UC_URL).mock(
            return_value=httpx.Response(
                200, content=INTERSTITIAL.encode(),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        )
        confirmed = respx.get(USERCONTENT_URL).mock(
            return_value=httpx.Response(
                200, content=b"REALBYTES",
                headers={"content-type": "application/octet-stream"},
            )
        )
        assert _read_all(UC_URL) == b"REALBYTES"
        params = httpx.URL(str(confirmed.calls[0].request.url)).params
        assert params["id"] == "FAKEID"
        assert params["confirm"] == "t"
        assert params["uuid"] == "44ac62aa-2012-4e0c-bc54-2d5fb669b817"

    @respx.mock
    def test_quota_interstitial_is_actionable(self):
        respx.get(UC_URL).mock(
            return_value=httpx.Response(
                200, content=QUOTA_PAGE.encode(),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        )
        with pytest.raises(MirrorError) as exc:
            _read_all(UC_URL)
        msg = str(exc.value)
        assert "quota" in msg.lower()
        assert "cas mirror import" in msg

    @respx.mock
    def test_quota_after_confirm_is_actionable(self):
        respx.get(UC_URL).mock(
            return_value=httpx.Response(
                200, content=INTERSTITIAL.encode(),
                headers={"content-type": "text/html"},
            )
        )
        respx.get(USERCONTENT_URL).mock(
            return_value=httpx.Response(
                200, content=QUOTA_PAGE.encode(),
                headers={"content-type": "text/html"},
            )
        )
        with pytest.raises(MirrorError, match="cas mirror import"):
            _read_all(UC_URL)

    @respx.mock
    def test_unrecognized_html_is_actionable(self):
        respx.get(UC_URL).mock(
            return_value=httpx.Response(
                200, content=b"<html><body>permission denied maybe?</body></html>",
                headers={"content-type": "text/html"},
            )
        )
        with pytest.raises(MirrorError, match="cas mirror import"):
            _read_all(UC_URL)


class TestStoreRoutesDriveUrls:
    @respx.mock
    def test_materialization_through_interstitial(self, mirror_root, tmp_path):
        """End-to-end: a Drive-hosted source materializes via the confirm flow."""
        from cas.mirror import ensure_materialized, is_materialized, load_manifest

        zip_bytes = build_fake_zip(tmp_path)
        respx.get(UC_URL).mock(
            return_value=httpx.Response(
                200, content=INTERSTITIAL.encode(),
                headers={"content-type": "text/html"},
            )
        )
        respx.get(USERCONTENT_URL).mock(
            return_value=httpx.Response(
                200, content=zip_bytes,
                headers={"content-type": "application/octet-stream"},
            )
        )
        ds = make_fake_dataset(url=UC_URL)
        with registered(ds):
            ensure_materialized(ds)
            assert is_materialized(ds)
            (archive,) = load_manifest(ds).archives
            assert archive.url == UC_URL
            assert archive.sha256_source == "tofu"
