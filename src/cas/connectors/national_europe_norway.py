# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Norwegian national data connectors."""

from __future__ import annotations

from cas.connectors.national_wcs import NationalDatasetConfig, NationalWCSConnector
from cas.core.models import BoundingBox, DataType, Variable
from cas.core.registry import register

ELEVATION = Variable(
    name="elevation", units="m", data_type=DataType.CONTINUOUS, valid_range=(-500, 9000)
)


@register("norway_dem")
class NorwayDEMConnector(NationalWCSConnector):
    """Kartverket's national detailed terrain model."""

    slug = "norway_dem"
    display_name = "Norway DTM 1m"
    base_url = "https://wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833"
    protocol = "wcs"
    _config = NationalDatasetConfig(
        slug="norway_dem",
        display_name="Norway DTM 1m (Kartverket)",
        wcs_url="https://wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833",
        coverage_id="nhm_dtm_topo_25833",
        variable=ELEVATION,
        resolution_m=1,
        crs="EPSG:25833",
        bbox=BoundingBox(min_lon=4.5, min_lat=57.9, max_lon=31.2, max_lat=71.2),
        license="CC-0 (Norway Open Data)",
        citation="Kartverket, National Detailed Height Model",
        use_wms=True,
    )
