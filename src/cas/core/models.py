# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Canonical data models for the CAS pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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


class Protocol(StrEnum):
    WCS = "wcs"
    STAC_COG = "stac_cog"
    OPENDAP = "opendap"
    REST = "rest"
    S3_DIRECT = "s3_direct"


class ProviderStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


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
    geometry: Geometry
    dataset_ids: list[str] = Field(min_length=1)
    time_range: TimeRange | None = None
    aggregation: AggregationMethod = AggregationMethod.MEAN
    target_crs: str = "EPSG:4326"

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
