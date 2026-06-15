# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Canonical data models for the CAS pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class TemporalType(StrEnum):
    STATIC = "static"
    ANNUAL = "annual"
    MONTHLY = "monthly"
    DAILY = "daily"
    HOURLY = "hourly"
    CLIMATOLOGY = "climatology"


class DataType(StrEnum):
    CONTINUOUS = "continuous"
    CATEGORICAL = "categorical"


class AggregationMethod(StrEnum):
    MEAN = "mean"
    MEDIAN = "median"
    MIN = "min"
    MAX = "max"
    STD = "std"
    SUM = "sum"
    MAJORITY = "majority"
    MINORITY = "minority"
    UNIQUE = "unique"
    DISTRIBUTION = "distribution"


class QualityFlag(StrEnum):
    GOOD = "good"
    SUSPECT = "suspect"
    PARTIAL = "partial"
    MISSING = "missing"
    DEGRADED = "degraded"
    ESTIMATED = "estimated"


class OutputMode(StrEnum):
    """What an extraction request produces.

    ``STATS`` (the default) is the classic CAS product: scalar zonal
    statistics per geometry. ``RASTER`` returns the underlying gridded data
    itself as a GeoTIFF on disk — available through the in-process Python
    facade only (``cas.extract_raster`` / ``cas.extract_raster_sync``),
    never over the HTTP API.
    """

    STATS = "stats"
    RASTER = "raster"


class RasterResampling(StrEnum):
    """Resampling kernels accepted by raster-mode requests."""

    NEAREST = "nearest"
    BILINEAR = "bilinear"
    CUBIC = "cubic"
    AVERAGE = "average"


class Protocol(StrEnum):
    WCS = "wcs"
    WFS = "wfs"
    STAC_COG = "stac_cog"
    OPENDAP = "opendap"
    REST = "rest"
    S3_DIRECT = "s3_direct"


class ProviderStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    AUTH_GATED = "auth_gated"  # service exists but needs credentials we don't have
    UNKNOWN = "unknown"  # genuinely unclassified — should be empty in a clean baseline


# ── Dataset Metadata ────────────────────────────────────────────────


class BoundingBox(BaseModel):
    min_lon: float = Field(ge=-180, le=180)
    min_lat: float = Field(ge=-90, le=90)
    max_lon: float = Field(ge=-180, le=180)
    max_lat: float = Field(ge=-90, le=90)


class TemporalExtent(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    temporal_type: TemporalType = TemporalType.STATIC


class Variable(BaseModel):
    name: str
    units: str
    description: str = ""
    data_type: DataType = DataType.CONTINUOUS
    nodata_value: float | None = None
    valid_range: tuple[float, float] | None = None


class Dataset(BaseModel):
    id: str = Field(description="CAS-internal ID: {provider}:{dataset_name}")
    provider: str
    name: str
    description: str = ""
    variables: list[Variable]
    resolution_m: float
    crs: str = "EPSG:4326"
    bbox: BoundingBox
    temporal: TemporalExtent = Field(default_factory=TemporalExtent)
    protocol: Protocol
    license: str = ""
    citation: str = ""


# ── Request / Response ──────────────────────────────────────────────


class Geometry(BaseModel):
    type: str = Field(pattern="^(Point|Polygon|MultiPolygon)$")
    coordinates: list

    @property
    def is_point(self) -> bool:
        return self.type == "Point"


class TimeRange(BaseModel):
    start: datetime
    end: datetime


class AttributeRequest(BaseModel):
    """Extraction request.

    Two mutually exclusive shapes, selected by ``output``:

    - ``output="stats"`` (default, unchanged): per-geometry zonal statistics.
      Requires ``geometry``; ``bbox``/``target_resolution`` are rejected.
    - ``output="raster"``: bbox-mode — a rectangular domain (not per-HRU
      geometry) extracted as one GeoTIFF. Requires ``bbox`` and exactly one
      dataset id; ``geometry`` is rejected. In-process only (see
      ``cas.extract_raster_sync``); the HTTP API rejects it.

    v1 raster limitation: ``target_crs`` must stay at its ``"EPSG:4326"``
    default — raster output is a native-CRS passthrough (EPSG:4326 for the
    STAC/COG and WCS sources supported in v1); reprojection is not
    implemented.
    """

    geometry: Geometry | None = None
    bbox: BoundingBox | None = None
    dataset_ids: list[str] = Field(min_length=1)
    time_range: TimeRange | None = None
    aggregation: AggregationMethod = AggregationMethod.MEAN
    target_crs: str = "EPSG:4326"
    output: OutputMode = OutputMode.STATS
    target_resolution: float | None = Field(
        default=None,
        gt=0,
        description="Raster-only: output pixel size in units of the source CRS "
        "(degrees for EPSG:4326). Default: native resolution.",
    )
    resampling: RasterResampling = RasterResampling.NEAREST

    @model_validator(mode="after")
    def _validate_output_mode(self) -> AttributeRequest:
        if self.output is OutputMode.STATS:
            if self.geometry is None:
                raise ValueError("output='stats' requires 'geometry'")
            if self.bbox is not None:
                raise ValueError(
                    "bbox-mode requests are raster-only; pass 'geometry' for "
                    "output='stats', or set output='raster' (in-process only)"
                )
            if self.target_resolution is not None:
                raise ValueError("'target_resolution' applies to output='raster' only")
        else:
            if self.bbox is None:
                raise ValueError(
                    "output='raster' requires 'bbox' (a rectangular domain); "
                    "per-HRU geometries are stats-only"
                )
            if self.geometry is not None:
                raise ValueError(
                    "output='raster' is bbox-mode only; pass 'bbox' instead of 'geometry'"
                )
            if len(self.dataset_ids) != 1:
                raise ValueError("output='raster' takes exactly one dataset_id per request")
            if self.bbox.min_lon >= self.bbox.max_lon or self.bbox.min_lat >= self.bbox.max_lat:
                raise ValueError("'bbox' must have min_lon < max_lon and min_lat < max_lat")
            if self.target_crs != "EPSG:4326":
                raise ValueError(
                    "v1 raster output is native-CRS passthrough (EPSG:4326 for the "
                    "supported STAC/WCS sources); 'target_crs' reprojection is not implemented"
                )
        return self

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [-96.6, 39.0], [-96.5, 39.0],
                            [-96.5, 39.1], [-96.6, 39.1], [-96.6, 39.0],
                        ]],
                    },
                    "dataset_ids": ["isric_soilgrids:clay_0-5cm"],
                    "aggregation": "mean",
                }
            ]
        }
    }


class AttributeResult(BaseModel):
    dataset_id: str
    variable: str
    value: float | dict[str, float] | None = None
    units: str
    aggregation: AggregationMethod
    quality: QualityFlag
    coverage_fraction: float = Field(ge=0.0, le=1.0)
    pixel_count: int
    timestamp: datetime | None = None
    provider: str
    elapsed_ms: int = 0
    provenance: str = ""
    valid_range: tuple[float, float] | None = None


class AttributeResponse(BaseModel):
    request_id: str
    geometry_hash: str
    results: list[AttributeResult]
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: int


class RasterResult(BaseModel):
    """Result of an in-process raster extraction (``output="raster"``).

    The raster itself is written to a caller-supplied directory as a GeoTIFF;
    this model carries the **path**, never the bytes — CAS stores nothing.

    Acceptance contract (what SYMFLUENCE discretization consumes): the file
    at ``path`` is a *domain-bbox, tile-mosaicked, native-resolution,
    EPSG:4326 GeoTIFF with correct nodata*. ``transform`` is the first six
    affine coefficients (a, b, c, d, e, f) and ``shape`` is (rows, cols) of
    the written band.
    """

    dataset_id: str
    provider: str
    variable: str = ""
    units: str = ""
    path: Path
    crs: str
    transform: tuple[float, float, float, float, float, float]
    shape: tuple[int, int]
    nodata: float | None = None
    license: str = ""
    provenance: str = ""
    elapsed_ms: int = 0


# ── Batch Request / Response ──────────────────────────────────────


class BatchAttributeRequest(BaseModel):
    geometries: list[Geometry] = Field(min_length=1, max_length=1000)
    dataset_ids: list[str] = Field(min_length=1)
    time_range: TimeRange | None = None
    aggregation: AggregationMethod = AggregationMethod.MEAN
    target_crs: str = "EPSG:4326"


class BatchAttributeResponse(BaseModel):
    request_id: str
    responses: list[AttributeResponse]
    total_geometries: int
    total_results: int
    elapsed_ms: int


# ── Health Check ────────────────────────────────────────────────────


class HealthCheckResult(BaseModel):
    provider: str
    status: ProviderStatus
    response_time_ms: int | None = None
    last_checked: datetime
    datasets_available: int = 0
    test_value: float | None = None
    expected_range: tuple[float, float] | None = None
    error: str | None = None


# ── Catalog API responses ───────────────────────────────────────────


class ProviderSummary(BaseModel):
    """Registry-level provider metadata (no upstream call required)."""

    slug: str
    name: str
    protocol: str
    base_url: str


class ProviderDetail(ProviderSummary):
    """A provider plus its full dataset metadata."""

    datasets: list[Dataset] = Field(default_factory=list)


class ProviderListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    count: int
    providers: list[ProviderSummary]


class DatasetListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    count: int
    datasets: list[Dataset]
