# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Provider registry — discovers and manages connector plugins."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cas.connectors.base import BaseConnector

_REGISTRY: dict[str, type[BaseConnector]] = {}


def register(slug: str):
    """Decorator to register a connector class under a provider slug."""

    def wrapper(cls: type[BaseConnector]) -> type[BaseConnector]:
        _REGISTRY[slug] = cls
        return cls

    return wrapper


def get_connector(slug: str) -> type[BaseConnector]:
    if slug not in _REGISTRY:
        raise KeyError(f"No connector registered for provider '{slug}'")
    return _REGISTRY[slug]


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


def discover() -> None:
    """Import all connector modules to trigger registration."""
    import importlib
    import pkgutil

    import cas.connectors as pkg

    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name not in ("base",) and not info.ispkg:
            importlib.import_module(f"cas.connectors.{info.name}")
