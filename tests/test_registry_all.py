# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Tests that all registered providers are discoverable, have valid metadata,
and can list datasets without errors."""

from __future__ import annotations

import pytest

from cas.core.registry import discover, get_connector, list_providers


class TestAllProviders:
    """Verify every registered provider passes basic sanity checks."""

    @pytest.fixture(autouse=True)
    def _discover(self):
        discover()

    def test_provider_count(self):
        providers = list_providers()
        assert len(providers) >= 230, f"Expected 230+ providers, got {len(providers)}"

    def test_all_providers_have_required_attributes(self):
        for slug in list_providers():
            cls = get_connector(slug)
            assert hasattr(cls, "slug"), f"{slug} missing slug"
            assert hasattr(cls, "display_name"), f"{slug} missing display_name"
            assert hasattr(cls, "base_url"), f"{slug} missing base_url"
            assert hasattr(cls, "protocol"), f"{slug} missing protocol"
            assert cls.slug == slug, f"{slug} slug mismatch: {cls.slug}"

    def test_all_providers_instantiate(self):
        for slug in list_providers():
            cls = get_connector(slug)
            instance = cls()
            assert instance is not None, f"{slug} failed to instantiate"

    @pytest.mark.asyncio
    async def test_all_providers_list_datasets(self):
        """Every provider should return a non-empty list of datasets."""
        failures = []
        for slug in list_providers():
            cls = get_connector(slug)
            try:
                async with cls() as conn:
                    datasets = await conn.list_datasets()
                    if not datasets:
                        failures.append(f"{slug}: returned empty datasets")
                    for ds in datasets:
                        assert ds.id.startswith(slug + ":"), (
                            f"{slug}: dataset ID '{ds.id}' doesn't start with '{slug}:'"
                        )
                        assert ds.provider == slug, (
                            f"{slug}: dataset provider '{ds.provider}' != '{slug}'"
                        )
                        assert ds.resolution_m > 0, f"{slug}: invalid resolution"
                        assert len(ds.variables) > 0, f"{slug}: no variables"
            except Exception as e:
                failures.append(f"{slug}: {e}")
        if failures:
            pytest.fail("Provider failures:\n" + "\n".join(failures))

    def test_no_duplicate_slugs(self):
        providers = list_providers()
        assert len(providers) == len(set(providers)), "Duplicate provider slugs found"

    def test_all_slugs_are_lowercase(self):
        for slug in list_providers():
            assert slug == slug.lower(), f"Provider slug '{slug}' is not lowercase"
