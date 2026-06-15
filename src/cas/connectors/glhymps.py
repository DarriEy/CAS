# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""GLHYMPS connector — global hydrogeology (permeability, porosity).

GLobal HYdrogeology MaPS v2.0: near-surface permeability and porosity derived
from the Global Lithological Map (Huscroft et al., 2018).

This connector is **mirror-backed** (design §5/header decision 3): it reads the
curated local GLHYMPS GeoParquet mirror (``CAS_MIRROR_DIR``) and returns
area-weighted zonal statistics through the normal extraction engine. It
replaces the previous HTTP connector, which was disabled because it called a
non-existent ``pygeoglim.get_glhymps(lat, lon)`` and pygeoglim's real API is
CONUS-only — the mirror gives true **global** coverage.

Variables (GLHYMPS columns, design §0): permeability ``logK_Ice`` (the
ice-sheet-corrected log permeability the SYMFLUENCE geology processor consumes)
and ``logK_Ferr`` (permafrost-free variant), and ``Porosity``. Permeability is
log10(m²); porosity is a fraction. Controls baseflow generation, groundwater
recharge, and subsurface storage.
"""

from __future__ import annotations

import structlog

from cas.connectors.protocols.mirror import (
    MirrorVariable,
    MirrorVectorConfig,
    MirrorVectorConnector,
)
from cas.core.models import BoundingBox, DataType
from cas.core.registry import register

logger = structlog.get_logger()


@register("glhymps")
class GLHYMPSConnector(MirrorVectorConnector):
    slug = "glhymps"
    display_name = "GLHYMPS 2.0 (Hydrogeology)"

    _config = MirrorVectorConfig(
        slug="glhymps",
        mirror_spec="glhymps==2.0",
        display_name="GLHYMPS 2.0 — global hydrogeology",
        resolution_m=10000,
        category="hydrology",
        bbox=BoundingBox(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90),
        variables=[
            MirrorVariable(
                name="permeability",
                column="logK_Ice",
                units="log10(m2)",
                description="Near-surface log permeability, ice-sheet corrected "
                            "(controls baseflow/recharge)",
                valid_range=(-20.0, -8.0),
                data_type=DataType.CONTINUOUS,
            ),
            MirrorVariable(
                name="permeability_permafrost_free",
                column="logK_Ferr",
                units="log10(m2)",
                description="Near-surface log permeability, permafrost-free variant",
                valid_range=(-20.0, -8.0),
                data_type=DataType.CONTINUOUS,
            ),
            MirrorVariable(
                name="porosity",
                column="Porosity",
                units="fraction",
                description="Near-surface porosity (controls subsurface storage)",
                valid_range=(0.0, 0.6),
                data_type=DataType.CONTINUOUS,
            ),
        ],
    )
