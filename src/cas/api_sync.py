# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Synchronous convenience wrappers around the async extraction engine.

These run :func:`cas.extract.engine.extract` / ``batch_extract`` to completion
on a fresh event loop via :func:`asyncio.run`. They are intended for scripts,
notebooks, and synchronous embedding frameworks. Calling them from inside a
running event loop raises a clear :class:`RuntimeError` — in async code, await
the underlying coroutines (``cas.extract`` / ``cas.batch_extract``) instead.
"""

from __future__ import annotations

import asyncio

from cas.core.models import (
    AttributeRequest,
    AttributeResponse,
    BatchAttributeRequest,
    BatchAttributeResponse,
)
from cas.extract.engine import batch_extract, extract

__all__ = ["batch_extract_sync", "extract_sync"]


def _check_no_running_loop(sync_name: str, async_name: str) -> None:
    """Raise a helpful error when a sync wrapper is called from async code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return  # no running loop — safe to asyncio.run()
    raise RuntimeError(
        f"cas.{sync_name}() cannot be called from a running event loop; "
        f"use `await cas.{async_name}(...)` instead."
    )


def extract_sync(request: AttributeRequest) -> AttributeResponse:
    """Blocking single-geometry extraction (sync wrapper for :func:`cas.extract`)."""
    _check_no_running_loop("extract_sync", "extract")
    return asyncio.run(extract(request))


def batch_extract_sync(request: BatchAttributeRequest) -> BatchAttributeResponse:
    """Blocking multi-geometry extraction (sync wrapper for :func:`cas.batch_extract`)."""
    _check_no_running_loop("batch_extract_sync", "batch_extract")
    return asyncio.run(batch_extract(request))
