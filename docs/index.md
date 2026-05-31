# CAS — Community Attribute Service

**Harmonized, QC'd, passthrough access to global geospatial attribute datasets**
— DEM/elevation, soil, land cover, hydrology, vegetation, geology, and biodiversity.

CAS is **not a data warehouse**. It is a thin quality-control and harmonization
layer that pulls from upstream providers on demand, validates responses, and
returns consistent results. Give it a geometry and one or more dataset IDs; it
fans out to the relevant providers, subsets server-side, computes zonal
statistics, runs QC, and hands back a uniform result.

## Why CAS?

- **214 active providers** across 34 countries — global flagships plus deep
  national coverage, all behind one consistent interface.
- **Passthrough, not storage** — no stale local copies; every request reflects
  the live upstream.
- **Harmonized output** — the same `AttributeResult` shape whether the source is
  a WCS server, a STAC+COG, or an OPeNDAP endpoint.
- **Built-in QC** — range checks, coverage thresholds, and cross-provider
  consistency warnings.
- **Three ways in** — a [CLI](cli.md), an [HTTP API](api.md), and a typed
  [Python SDK](sdk.md).

## Pick your interface

=== "Python SDK"

    ```python
    from cas.client import CASClient

    with CASClient("http://localhost:8000") as cas:
        resp = cas.extract(
            geometry={"type": "Point", "coordinates": [-96.5, 39.0]},
            dataset_ids=["copernicus_dem:elevation"],
        )
        print(resp.results[0].value, resp.results[0].units)
    ```

=== "HTTP API"

    ```bash
    curl -s localhost:8000/api/v1/extract -H 'content-type: application/json' -d '{
      "geometry": {"type": "Point", "coordinates": [-96.5, 39.0]},
      "dataset_ids": ["copernicus_dem:elevation"]
    }'
    ```

=== "CLI"

    ```bash
    cas extract \
      -g '{"type":"Point","coordinates":[-96.5,39.0]}' \
      -d copernicus_dem:elevation
    ```

## Where to next

- New here? Start with the [Quick Start](quickstart.md).
- Building an app? Read the [Python SDK](sdk.md) guide and the
  [SDK Reference](reference.md).
- Integrating over HTTP? See the [HTTP API](api.md) and the live
  `/docs` (Swagger UI) on a running instance.
- Want the full dataset list? See the [Provider Catalog](catalog.md).
