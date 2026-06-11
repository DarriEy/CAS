# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Guard against drift between the committed inventory and the live registry.

`inventory/providers.yaml` (and the provider counts quoted in the docs) are
generated snapshots of the runtime connector registry. This offline test fails
CI whenever a connector is added or removed without regenerating the inventory
via `cas export-inventory`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from cas.core.registry import discover, list_providers

INVENTORY = Path(__file__).resolve().parents[1] / "inventory" / "providers.yaml"


def test_inventory_matches_live_registry() -> None:
    discover()
    registry_slugs = set(list_providers())

    entries = yaml.safe_load(INVENTORY.read_text())
    inventory_slugs = {entry["slug"] for entry in entries}

    missing = sorted(registry_slugs - inventory_slugs)
    stale = sorted(inventory_slugs - registry_slugs)
    assert not missing and not stale, (
        f"inventory/providers.yaml is out of date — regenerate with "
        f"`cas export-inventory`. Missing from inventory: {missing}; "
        f"no longer registered: {stale}"
    )


def test_inventory_header_count_matches_live_registry() -> None:
    discover()
    header = INVENTORY.read_text().splitlines()[1]
    match = re.fullmatch(r"# Total providers: (\d+)", header)
    assert match, f"Unexpected inventory header line: {header!r}"
    claimed = int(match.group(1))
    actual = len(list_providers())
    assert claimed == actual, (
        f"Inventory header claims {claimed} providers but the registry has "
        f"{actual} — regenerate with `cas export-inventory`"
    )
