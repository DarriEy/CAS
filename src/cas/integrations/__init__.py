# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Integrations embedding CAS in external frameworks.

Each submodule adapts CAS to one host framework and degrades gracefully when
that framework is not installed, so ``import cas`` never grows a hard
dependency. See :mod:`cas.integrations.symfluence` for the SYMFLUENCE
acquisition-handler plugin.
"""

__all__ = ["symfluence"]
