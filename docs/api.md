# HTTP API

The CAS service is a FastAPI application. A running instance serves an
interactive OpenAPI UI at **`/docs`** and the raw schema at **`/openapi.json`** —
both are generated from the same typed Pydantic models documented here, so they
are always in sync with the deployed version.

```bash
pip install -e ".[api,stac]"
uvicorn cas.api.app:create_app --factory --reload
```

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/extract` | Extract attributes for one geometry |
| `POST` | `/api/v1/extract/batch` | Extract attributes for many geometries |
| `GET` | `/api/v1/datasets` | List available datasets (paginated) |
| `GET` | `/api/v1/providers` | List registered providers (paginated) |
| `GET` | `/api/v1/providers/{slug}` | One provider with full dataset metadata |
| `GET` | `/health` | Liveness check + result-cache stats |
| `GET` | `/metrics` | Prometheus metrics exposition |
| `GET` | `/docs` | Interactive OpenAPI (Swagger) UI |

## Extract

```bash
curl -s localhost:8000/api/v1/extract \
  -H 'content-type: application/json' -d '{
    "geometry": {
      "type": "Polygon",
      "coordinates": [[[-96.6,39],[-96.5,39],[-96.5,39.1],[-96.6,39.1],[-96.6,39]]]
    },
    "dataset_ids": ["isric_soilgrids:clay_0-5cm"],
    "aggregation": "mean"
  }'
```

Response (`AttributeResponse`):

```json
{
  "request_id": "…",
  "geometry_hash": "…",
  "results": [
    {
      "dataset_id": "isric_soilgrids:clay_0-5cm",
      "variable": "clay_0-5cm",
      "value": 23.4,
      "units": "g/kg",
      "aggregation": "mean",
      "quality": "good",
      "coverage_fraction": 1.0,
      "pixel_count": 42,
      "provider": "isric_soilgrids",
      "elapsed_ms": 318,
      "provenance": "…"
    }
  ],
  "warnings": [],
  "elapsed_ms": 320
}
```

## Catalog discovery

List every provider, then drill into one for its datasets, resolution, bbox,
license, citation, and variables:

```bash
curl -s 'localhost:8000/api/v1/providers?limit=1000'
curl -s localhost:8000/api/v1/providers/copernicus_dem
```

`/datasets` and `/providers` accept `limit` (1–1000, default 100) and `offset`
and return `{total, limit, offset, count, …}`. Catalog responses are served from
an in-memory metadata cache (TTL `CAS_METADATA_CACHE_TTL_S`).

## Conventions

- Every response carries an **`X-Request-ID`** header.
- Errors use a consistent envelope:

  ```json
  {"error": {"type": "validation_error", "message": "…", "request_id": "…"}}
  ```

  Common `type` values: `validation_error`, `request_limit`, `unauthorized`,
  `rate_limited`, `not_found`.

## Configuration

All runtime config is read from `CAS_`-prefixed environment variables.
Hardening features are **off by default**, so the same image runs internal or
public depending only on env.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAS_PROVIDER_TIMEOUT_S` | `30` | Per-provider extraction deadline |
| `CAS_REQUEST_TIMEOUT_S` | `120` | Whole-request backstop deadline |
| `CAS_MAX_DATASETS_PER_REQUEST` | `50` | Reject oversized requests (422) |
| `CAS_MAX_POLYGON_VERTICES` | `10000` | Reject overly complex geometries (422) |
| `CAS_METADATA_CACHE_TTL_S` | `3600` | Catalog cache TTL |
| `CAS_CORS_ORIGINS` | `*` | Allowed origins (CSV or JSON) |
| `CAS_AUTH_ENABLED` / `CAS_API_KEYS` | `false` / — | Require `X-API-Key` from an allowlist |
| `CAS_RATE_LIMIT_ENABLED` / `CAS_RATE_LIMIT_PER_MINUTE` | `false` / `60` | Per-caller fixed-window rate limit |

## Deployment (Docker)

```bash
docker build -t cas-api .
docker run -p 8000:8000 \
  -e CAS_AUTH_ENABLED=true -e CAS_API_KEYS=key1,key2 \
  -e CAS_RATE_LIMIT_ENABLED=true \
  cas-api
```

The rate limiter is in-memory and per-process; for multi-replica deployments,
enforce limits at the ingress/gateway instead.
