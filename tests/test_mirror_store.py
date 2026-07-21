# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Materialization engine tests: download → checksum → extract → convert →
manifest, plus locking, TOFU, verify, read-only roots, and removal.

Hermetic: tiny synthetic shapefile zips served via respx."""

from __future__ import annotations

import errno
import json
import os
import stat
import threading
from unittest.mock import MagicMock

import httpx
import pytest
import respx

pytest.importorskip("geopandas")
pytest.importorskip("pyarrow")

import cas
from cas.core.exceptions import MirrorIntegrityError, MirrorWriteError
from cas.mirror import (
    dataset_dir,
    ensure_materialized,
    is_materialized,
    load_manifest,
    manifest_path,
    query_layer_path,
    remove,
    status,
    verify,
)
from cas.mirror.models import MANIFEST_SCHEMA_VERSION

from .mirror_utils import FAKE_COLUMNS, FAKE_URL, build_fake_zip, make_fake_dataset, registered


@pytest.fixture
def mirror_root(tmp_path):
    """A fresh mirror root configured for the duration of one test."""
    from cas.core.config import _overrides, get_settings

    saved = dict(_overrides)
    root = tmp_path / "mirror"
    cas.configure(mirror_dir=root)
    yield root
    _overrides.clear()
    _overrides.update(saved)
    get_settings.cache_clear()


@pytest.fixture
def fake_zip(tmp_path) -> bytes:
    return build_fake_zip(tmp_path)


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


class TestMaterializeHappyPath:
    @respx.mock
    def test_materialize_writes_parquet_and_manifest(self, mirror_root, fake_zip):
        route = respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        with registered(make_fake_dataset()) as ds:
            path = ensure_materialized(ds)

            assert path == query_layer_path(ds)
            assert path.is_file()
            assert path.parent == mirror_root / "fakeveg" / "1.0"
            assert is_materialized(ds)

            manifest = load_manifest(ds)
            assert manifest.manifest_schema == MANIFEST_SCHEMA_VERSION
            assert manifest.slug == "fakeveg"
            assert manifest.version == "1.0"
            assert manifest.source_urls == [FAKE_URL]
            assert manifest.feature_count == 4
            assert manifest.columns == FAKE_COLUMNS
            assert manifest.crs  # source CRS recorded
            assert manifest.citation == ds.citation
            assert manifest.license.attribution == "Fake Data Consortium 2026"
            assert manifest.retrieved_at is not None
            assert manifest.cas_version == cas.__version__

            # Conversion provenance (design §2/§3).
            conv = manifest.conversion
            assert conv.parameters["sort"] == "hilbert"
            assert conv.parameters["row_group_size"] == 65536
            assert conv.parameters["write_covering_bbox"] is True
            assert set(conv.tool_versions) == {"geopandas", "pyogrio", "pyarrow"}

            # TOFU checksum recorded for the archive (registry sha was None).
            archive = manifest.archives[0]
            assert archive.sha256 == _sha256(fake_zip)
            assert archive.size_bytes == len(fake_zip)
            assert archive.sha256_source == "tofu"

            # Only the needed layer was kept, and that fact is recorded.
            assert all("data." in m for m in manifest.kept_members)
            assert not any("cave_points" in m for m in manifest.kept_members)
            assert not any("README" in m for m in manifest.kept_members)

            # The parquet itself is hashed into the manifest.
            (record,) = manifest.files
            assert record.path == path.name
            assert record.role == "query_layer"
            assert record.size_bytes == path.stat().st_size

            # Archive and extracted shapefile are dropped post-conversion.
            leftovers = {f.name for f in path.parent.iterdir()}
            assert leftovers <= {path.name, "manifest.json", ".lock"}

        assert route.call_count == 1

    @respx.mock
    def test_second_call_is_local_only(self, mirror_root, fake_zip):
        route = respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        with registered(make_fake_dataset()) as ds:
            first = ensure_materialized(ds)
            second = ensure_materialized(ds)
            assert first == second
            assert route.call_count == 1

    @respx.mock
    def test_index_json_updated(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        with registered(make_fake_dataset()) as ds:
            ensure_materialized(ds)
            index = json.loads((mirror_root / "index.json").read_text())
            assert index["datasets"][0]["slug"] == "fakeveg"
            assert index["total_size_bytes"] > 0

            remove(ds)
            index = json.loads((mirror_root / "index.json").read_text())
            assert index["datasets"] == []


class TestIntegrity:
    @respx.mock
    def test_registry_checksum_match_is_verified(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        ds = make_fake_dataset(sha256=_sha256(fake_zip))
        with registered(ds):
            ensure_materialized(ds)
            manifest = load_manifest(ds)
            assert manifest.archives[0].sha256_source == "registry"
            row = next(r for r in status() if r.slug == "fakeveg")
            assert row.checksum_state == "registry-verified"

    @respx.mock
    def test_registry_checksum_mismatch_is_hard_error(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        bad = "0" * 64
        ds = make_fake_dataset(sha256=bad)
        with registered(ds):
            with pytest.raises(MirrorIntegrityError) as exc:
                ensure_materialized(ds)
            # Both hashes surface; nothing plausible is left behind.
            assert bad in str(exc.value)
            assert _sha256(fake_zip) in str(exc.value)
            assert not is_materialized(ds)
            ddir = dataset_dir(ds)
            assert not list(ddir.glob("*.part"))
            assert not list(ddir.glob("*.zip"))

    @respx.mock
    def test_verify_clean_and_corrupted(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        with registered(make_fake_dataset()) as ds:
            path = ensure_materialized(ds)
            assert verify(ds) == []

            with open(path, "ab") as fh:
                fh.write(b"corruption")
            problems = verify(ds)
            assert any("sha256 mismatch" in p for p in problems)
            assert any("size mismatch" in p for p in problems)

    def test_verify_not_materialized(self, mirror_root):
        with registered(make_fake_dataset()) as ds:
            problems = verify(ds)
            assert problems and "not materialized" in problems[0]

    @respx.mock
    def test_unparsable_manifest_treated_as_not_materialized(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        with registered(make_fake_dataset()) as ds:
            ensure_materialized(ds)
            manifest_path(ds).write_text("{not json")
            assert not is_materialized(ds)
            assert any("unparsable" in p for p in verify(ds))


class TestConcurrency:
    @respx.mock
    def test_two_threads_one_download_both_succeed(self, mirror_root, fake_zip):
        route = respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        results: list = []
        errors: list = []

        with registered(make_fake_dataset()) as ds:
            def work():
                try:
                    results.append(ensure_materialized(ds))
                except Exception as exc:  # noqa: BLE001 — collected for assertion
                    errors.append(exc)

            threads = [threading.Thread(target=work) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)

            assert not errors
            assert len(results) == 2
            assert results[0] == results[1]
            assert results[0].is_file()
            # The lock + post-lock manifest re-check make this one download.
            assert route.call_count == 1


class TestReadOnlyRoot:
    def test_unmaterialized_in_readonly_root_is_actionable(self, mirror_root, monkeypatch):
        mirror_root.mkdir(parents=True, exist_ok=True)
        # chmod does not make a directory non-writable on Windows. Patch the
        # platform permission probe so this exercises the OS decision on all hosts.
        monkeypatch.setattr("cas.mirror.store.os.access", lambda path, mode: False)
        with registered(make_fake_dataset(slug="roveg")) as ds:
            with pytest.raises(MirrorWriteError) as exc:
                ensure_materialized(ds)
            msg = str(exc.value)
            assert "cas mirror sync roveg==1.0" in msg
            assert "administrator" in msg

    @respx.mock
    def test_materialized_then_readonly_reads_proceed(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        with registered(make_fake_dataset()) as ds:
            path = ensure_materialized(ds)
            ddir = dataset_dir(ds)
            for d in (mirror_root, mirror_root / "fakeveg", ddir):
                d.chmod(stat.S_IRUSR | stat.S_IXUSR)
            try:
                # The read path returns without any write attempt.
                assert ensure_materialized(ds) == path
            finally:
                for d in (mirror_root, mirror_root / "fakeveg", ddir):
                    d.chmod(stat.S_IRWXU)


class TestAcknowledgmentMaterialization:
    @respx.mock
    def test_env_acceptance_materializes_and_records(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        cas.configure(mirror_accept_licenses=["ackveg"])
        ds = make_fake_dataset(slug="ackveg", requires_acknowledgment=True)
        with registered(ds):
            ensure_materialized(ds)
            manifest = load_manifest(ds)
            (ack,) = manifest.acknowledgments
            assert ack.license == "FAKE-ACK-1.0"
            assert ack.accepted_via == "env"
            assert ack.accepted_at is not None

    @respx.mock
    def test_explicit_acceptance_records_via(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        ds = make_fake_dataset(slug="ackveg", requires_acknowledgment=True)
        with registered(ds):
            ensure_materialized(ds, explicit=True, licenses_accepted=True, ack_via="interactive")
            (ack,) = load_manifest(ds).acknowledgments
            assert ack.accepted_via == "interactive"


class TestStatusAndRemove:
    @respx.mock
    def test_status_rows(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        with registered(make_fake_dataset()) as ds:
            rows = {r.slug: r for r in status()}
            assert rows["fakeveg"].materialized is False
            assert rows["fakeveg"].checksum_state == "unverified"
            assert rows["glhymps"].license == "CC-BY-4.0"
            assert rows["wokam"].license_flags == ["unconfirmed"]

            ensure_materialized(ds)
            row = next(r for r in status() if r.slug == "fakeveg")
            assert row.materialized is True
            assert row.disk_bytes > 0
            assert row.checksum_state == "tofu"

    @respx.mock
    def test_remove_reclaims_disk(self, mirror_root, fake_zip):
        respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
        with registered(make_fake_dataset()) as ds:
            ensure_materialized(ds)
            assert remove(ds) is True
            assert not dataset_dir(ds).exists()
            assert not (mirror_root / "fakeveg").exists()
            assert remove(ds) is False
            assert not is_materialized(ds)

    def test_download_failure_leaves_no_partial_state(self, mirror_root):
        with respx.mock:
            respx.get(FAKE_URL).mock(return_value=httpx.Response(503))
            with registered(make_fake_dataset()) as ds:
                with pytest.raises(httpx.HTTPStatusError):
                    ensure_materialized(ds)
                assert not is_materialized(ds)
                assert not list(dataset_dir(ds).glob("*.part"))

    def test_download_can_recover_after_transient_failure(self, mirror_root, fake_zip):
        """A failed acquisition must leave the dataset retryable, not poisoned."""
        with registered(make_fake_dataset()) as ds:
            with respx.mock:
                respx.get(FAKE_URL).mock(return_value=httpx.Response(503))
                with pytest.raises(httpx.HTTPStatusError):
                    ensure_materialized(ds)

            with respx.mock:
                respx.get(FAKE_URL).mock(return_value=httpx.Response(200, content=fake_zip))
                path = ensure_materialized(ds)

            assert path.is_file()
            assert is_materialized(ds)
            assert not list(dataset_dir(ds).glob("*.part"))


def test_exclusive_file_lock_creates_lock_file(tmp_path):
    from cas.mirror.store import _exclusive_file_lock

    path = tmp_path / ".lock"
    with _exclusive_file_lock(path):
        assert path.is_file()


@pytest.mark.skipif(os.name != "nt", reason="Windows locking behavior")
def test_windows_lock_reraises_permanent_error(tmp_path, monkeypatch):
    from cas.mirror import store

    permanent = OSError(5, "invalid handle")
    monkeypatch.setattr(store.msvcrt, "locking", MagicMock(side_effect=permanent))
    with pytest.raises(OSError, match="invalid handle"), store._exclusive_file_lock(tmp_path / ".lock"):
        pass


@pytest.mark.skipif(os.name != "nt", reason="Windows locking behavior")
def test_windows_lock_contention_has_timeout(tmp_path, monkeypatch):
    from cas.mirror import store

    contended = OSError(errno.EACCES, "lock held")
    monkeypatch.setattr(store.msvcrt, "locking", MagicMock(side_effect=contended))
    with (
        pytest.raises(TimeoutError, match="Timed out waiting"),
        store._exclusive_file_lock(tmp_path / ".lock", timeout_s=0),
    ):
        pass


def test_locking_backend_matches_host_os():
    import os

    from cas.mirror import store

    if os.name == "nt":
        assert hasattr(store.msvcrt, "LK_LOCK") and hasattr(store.msvcrt, "LK_UNLCK")
        assert not hasattr(store, "fcntl")
    else:
        assert hasattr(store.fcntl, "LOCK_EX") and hasattr(store.fcntl, "LOCK_UN")


def test_disk_bytes_helper(tmp_path):
    from cas.mirror.store import _disk_bytes

    (tmp_path / "a").write_bytes(b"12345")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b").write_bytes(b"123")
    assert _disk_bytes(tmp_path) == 8
