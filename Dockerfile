# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
# Multi-stage build for the CAS API service.

# ── Build stage: produce a wheel ────────────────────────────────────
FROM python:3.12-slim AS build

WORKDIR /build
RUN pip install --no-cache-dir build
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m build --wheel --outdir /dist

# ── Runtime stage ───────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# rasterio/pyproj ship manylinux wheels with bundled GDAL/PROJ, so no
# system GDAL is needed; libexpat is pulled in transitively. Keep slim.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 cas
WORKDIR /app

COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[api,stac]" && rm -f /tmp/*.whl

USER cas
EXPOSE 8000

# Defaults are safe for internal use; flip CAS_AUTH_ENABLED / CAS_RATE_LIMIT_ENABLED
# and set CAS_API_KEYS / CAS_CORS_ORIGINS for public deployments.
CMD ["uvicorn", "cas.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
