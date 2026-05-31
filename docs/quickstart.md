# Quick Start

## Install

```bash
# Library + CLI
pip install -e ".[stac]"

# Add the HTTP API server
pip install -e ".[api,stac]"
```

The `stac` extra pulls in `pystac-client` and `planetary-computer`, needed by
the many providers served via STAC + COG. The `api` extra pulls in FastAPI and
Uvicorn.

## 1. Explore providers

```bash
cas providers                 # list all registered provider slugs
cas datasets -p copernicus_dem  # list datasets for one provider
```

## 2. Extract from the CLI

```bash
# Mean elevation over a point
cas extract \
  -g '{"type":"Point","coordinates":[-96.5,39.0]}' \
  -d copernicus_dem:elevation

# Cross-provider DEM comparison over a catchment
cas extract \
  -g @my_catchment.geojson \
  -d copernicus_dem:elevation \
  -d nasadem:elevation \
  -d alos_dem:elevation
```

## 3. Run the API

```bash
uvicorn cas.api.app:create_app --factory --reload
# → http://localhost:8000/docs   (interactive OpenAPI UI)
```

## 4. Use the Python SDK

```python
from cas.client import CASClient

with CASClient("http://localhost:8000") as cas:
    # Discover the catalog
    for p in cas.providers(limit=1000).providers:
        print(p.slug, p.protocol)

    # Extract
    resp = cas.extract(
        geometry={"type": "Point", "coordinates": [-96.5, 39.0]},
        dataset_ids=["copernicus_dem:elevation", "isric_soilgrids:clay_0-5cm"],
    )
    for r in resp.results:
        print(r.dataset_id, r.value, r.units, r.quality)
```

See [Python SDK](sdk.md) for the full guide.
