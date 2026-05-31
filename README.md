# CAS — Community Attribute Service

Harmonized access to global geospatial attribute datasets (DEM, soil, land cover, climate, vegetation) through a community-driven, open-source passthrough service.

CAS is **not a data warehouse** — it's a QC layer and one-stop-shop that pulls from upstream providers on-demand, validates responses, and returns harmonized results.

CAS ships **214 active providers** spanning DEM/elevation, soil, land cover, hydrology, vegetation/canopy, geology, and biodiversity — including global/flagship datasets plus **162 national/regional providers across 34 countries**. Every provider listed below is registered in the runtime connector registry and exercised by the end-to-end health sweep; run `cas providers` to see the live list.

**Status**: Alpha (v0.1.0)

## Quick Start

```bash
pip install -e ".[dev,stac]"

# List registered providers
cas providers

# List available datasets from a provider
cas datasets -p isric_soilgrids

# Extract mean clay content for a polygon
cas extract \
  -g '{"type":"Polygon","coordinates":[[[-96.6,39],[-96.5,39],[-96.5,39.1],[-96.6,39.1],[-96.6,39]]]}' \
  -d isric_soilgrids:clay_0-5cm

# Cross-provider DEM comparison
cas extract \
  -g @my_catchment.geojson \
  -d copernicus_dem:elevation \
  -d usgs_3dep:elevation \
  -d nasadem:elevation \
  -d alos_dem:elevation

# Multi-attribute extraction
cas extract \
  -g @my_catchment.geojson \
  -d copernicus_dem:elevation \
  -d isric_soilgrids:clay_0-5cm \
  -d esa_worldcover:land_cover

# Run health checks
cas health
```

## API

```bash
pip install -e ".[dev,api,stac]"
uvicorn cas.api.app:create_app --factory --reload
```

```
POST /api/v1/extract        — Extract attributes for a geometry
POST /api/v1/extract/batch  — Extract attributes for many geometries
GET  /api/v1/datasets       — List available datasets (paginated)
GET  /api/v1/providers      — List registered providers (paginated)
GET  /health                — Liveness check + result-cache stats
GET  /metrics               — Prometheus metrics exposition
GET  /docs                  — Interactive OpenAPI docs
```

`/datasets` and `/providers` accept `limit` (1–1000, default 100) and `offset`
query params and return `{total, limit, offset, count, ...}`. Catalog responses
are served from an in-memory metadata cache (TTL `CAS_METADATA_CACHE_TTL_S`).
Every response carries an `X-Request-ID` header; errors use a consistent envelope:

```json
{"error": {"type": "request_limit", "message": "...", "request_id": "abc123"}}
```

### Configuration

All runtime config is read from `CAS_`-prefixed environment variables. Hardening
features are **off by default** — the same image runs internal or public depending
only on env:

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAS_PROVIDER_TIMEOUT_S` | `30` | Per-provider extraction deadline (slow upstream → warning) |
| `CAS_REQUEST_TIMEOUT_S` | `120` | Whole-request backstop deadline |
| `CAS_MAX_DATASETS_PER_REQUEST` | `50` | Reject oversized requests (422) |
| `CAS_MAX_POLYGON_VERTICES` | `10000` | Reject overly complex geometries (422) |
| `CAS_RESULT_CACHE_TTL_S` / `CAS_RESULT_CACHE_MAX_ENTRIES` | `600` / `10000` | Result cache tuning |
| `CAS_METADATA_CACHE_TTL_S` | `3600` | Catalog cache TTL |
| `CAS_CORS_ORIGINS` | `*` | Allowed origins (comma-separated or JSON) |
| `CAS_AUTH_ENABLED` / `CAS_API_KEYS` | `false` / — | Require `X-API-Key` from a comma-separated allowlist |
| `CAS_RATE_LIMIT_ENABLED` / `CAS_RATE_LIMIT_PER_MINUTE` | `false` / `60` | Per-caller fixed-window rate limit (per process) |

### Deployment (Docker)

```bash
docker build -t cas-api .
docker run -p 8000:8000 \
  -e CAS_AUTH_ENABLED=true -e CAS_API_KEYS=key1,key2 \
  -e CAS_RATE_LIMIT_ENABLED=true \
  cas-api
```

The rate limiter is in-memory and per-process; for multi-replica deployments,
enforce limits at the ingress/gateway instead.

## Architecture

```
Geometry in → CAS engine → fan out to providers → server-side subset → zonal stats → QC → results out
```

- **Passthrough**: No data storage. Every request goes to the upstream provider.
- **Plugin connectors**: Each provider is a self-contained module with `@register` decorator.
- **Protocol mixins**: WCS, STAC+COG, OPeNDAP — compose into connectors via multiple inheritance.
- **Zonal statistics**: Continuous (mean/median/min/max/std) and categorical (majority/distribution).
- **QC validation**: Range checks, coverage thresholds, cross-provider consistency.
- **Daily CI health checks**: Verify providers are up with known test polygons.

## Implemented Providers

CAS registers **214 active providers**. The tables below list the headline global/flagship
datasets per category; the national breadth (162 country/region-specific providers across 34 countries)
is summarized in [National providers by country](#national-providers-by-country). The complete
machine-readable catalog (resolution, bbox, license, variables) lives in
`inventory/providers.yaml` and is regenerated with `cas export-inventory`. Get the live list any
time with `cas providers`.

Roughly by category: DEM/Elevation ~46, Soil ~46, Land Cover ~46, Hydrology/Water ~35,
Vegetation/Canopy ~20, Geology ~7, plus Biodiversity/Ecology and other thematic layers.

### DEM / Elevation — global & flagship

| Provider | Slug | Resolution | Coverage | Access |
|----------|------|-----------|----------|--------|
| Copernicus DEM GLO-30 | `copernicus_dem` | 30m | Global | Open |
| Copernicus DEM GLO-90 | `cop_dem_90` | 90m | Global | Open |
| USGS 3DEP | `usgs_3dep` | 10m | US | Public domain |
| NASADEM (SRTM) | `nasadem` | 30m | 56S–60N | Public domain |
| ALOS World 3D | `alos_dem` | 30m | Global | JAXA (research) |
| ASTER GDEM v3 | `aster_gdem` | 30m | Global | NASA Earthdata login |
| ArcticDEM | `arctic_dem` | 10m | >50N | Open (PGC) |
| REMA (Antarctica) | `rema` | 8m | <53S | Open (PGC) |
| ETOPO 2022 (topo+bathy) | `etopo_2022` | ~2km | Global | Open (NOAA) |
| GEBCO Bathymetry | `gebco` | 500m | Global | Open (GEBCO) |
| OpenTopography | `opentopography` | 30–90m | Global | API key (free) |
| MERIT DEM | `merit_dem` | 90m | Global | Registration (CC-BY-NC) |
| TanDEM-X 90m | `tandem_x` | 90m | Global | Registration (DLR) |

Plus ~32 national/regional DEMs (Australia, Canada HRDEM, Japan GSI, and most of Europe).

### Soil — global & flagship

| Provider | Slug | Resolution | Coverage | Access |
|----------|------|-----------|----------|--------|
| ISRIC SoilGrids 2.0 | `isric_soilgrids` | 250m | Global | CC-BY-4.0 |
| SoilGrids derived (OCS / WRB) | `soilgrids_derived` | 250m | Global | CC-BY-4.0 |
| OpenLandMap | `openlandmap` | 250m | Global | CC-BY-SA-4.0 |
| SSURGO / gNATSGO | `ssurgo`, `gnatsgo` | 30m | US | Public domain |
| POLARIS | `polaris` | 30m | US | CC-BY-NC-4.0 |
| SLGA | `slga` | 90m | Australia | CC-BY-4.0 |

Plus ~40 national soil products (Germany soil-quality/texture/water/erosion suite, Ireland,
France, Netherlands, Nordics, Brazil, India, Mexico, Argentina, and more).

### Land Cover — global & flagship

| Provider | Slug | Resolution | Coverage | Access |
|----------|------|-----------|----------|--------|
| ESA WorldCover | `esa_worldcover` | 10m | Global | CC-BY-4.0 |
| ESA CCI Land Cover | `esa_cci_lc` | 300m | Global | ESA CCI |
| Dynamic World | `dynamic_world` | 10m | Global | CC-BY-4.0 (GEE) |
| Esri 10m LULC | `esri_lulc` | 10m | Global | CC-BY-4.0 |
| Impact Observatory LULC | `io_lulc` | 10m | Global | CC-BY-4.0 |
| Impact Observatory LULC (9-class) | `io_lulc_9class` | 10m | Global | CC-BY-4.0 |
| MODIS MCD12Q1 | `modis_lc` | 500m | Global | Open (NASA) |
| GHSL Human Settlement | `ghsl` | 100m | Global | CC-BY-4.0 |
| MS Building Footprints | `ms_buildings` | ~1m | Global | ODbL |
| CORINE Land Cover | `corine_lc` | 100m | Europe | Copernicus |
| NLCD | `nlcd` | 30m | US | Public domain |

Plus ~35 national/regional land-cover & cropland products (USDA CDL, NRCan, DEA Africa, and
national maps for ~25 countries).

### Hydrology / Water (~35)

`merit_hydro` (global flow/accumulation), `jrc_gsw` (Global Surface Water), `modis_snow`,
`permafrost` (global), `ramsar_wetlands`, plus national hydrography, groundwater, aquifer,
flood, lake, catchment and runoff layers (USGS NHD/WBD, Switzerland rivers/glaciers, Ireland,
UK, Spain, Belgium, Finland, Australia water observations).

### Vegetation / Canopy (~20)

`canopy_height` (ETH 10m), `hansen_forest` (Global Forest Change), `chloris_biomass`,
`hgb_biomass`, `alos_fnf`, plus national forest height/volume/species, fractional cover,
EEA High-Resolution Layers (tree/leaf/woody/grassland), and Norway vegetation/landslide layers.

### Geology (~7)

National bedrock, lithology, and quaternary geology for Belgium, Estonia, Greece,
Norway, Portugal, and Spain.

### Biodiversity / Ecology & other

`biodiversity` (global Biodiversity Intactness), `mobi` (US biodiversity importance),
`brazil_biomes`, `hrea` (electricity access), `mtbs` (US burn severity).

### National providers by country

162 of the 214 providers are country/region-specific (DEM, soil, land cover, hydrology, geology),
across 34 countries. Counts:

| Country | Providers | Country | Providers |
|---------|:---------:|---------|:---------:|
| Germany | 20 | Czechia | 2 |
| USA | 18 | Denmark | 2 |
| Ireland | 13 | Estonia | 2 |
| Switzerland | 12 | India | 2 |
| Australia | 10 | Lithuania | 2 |
| UK | 10 | Mexico | 2 |
| Norway | 9 | Portugal | 2 |
| Belgium | 7 | Sweden | 2 |
| Spain | 7 | Argentina | 1 |
| Finland | 6 | Austria | 1 |
| Canada | 5 | Colombia | 1 |
| France | 3 | Ethiopia | 1 |
| Netherlands | 5 | Greece | 1 |
| Slovenia | 3 | Indonesia | 1 |
| Brazil | 2 | Japan | 1 |
| Croatia | 2 | Luxembourg, Nigeria, Peru | 1 each |

Most European, North American, and Australian connectors are **open** (OGC WCS/WMS or
STAC+COG). A handful require free registration — see below.

## Providers Requiring Registration

Some providers require free registration before use. CAS will display clear instructions when you attempt to use them without credentials.

### OpenTopography (free API key)

Provides server-side subsetting for SRTM, COP30, COP90, NASADEM, AW3D30, EU_DTM.

1. Register at https://portal.opentopography.org/
2. Go to My Account → API Keys → Request API Key
3. Set the key:
```bash
export CAS_OPENTOPOGRAPHY_API_KEY=your_key
```

### MERIT DEM (University of Tokyo)

Global 90m hydrologically adjusted DEM (noise, canopy, speckle removed).

1. Visit https://hydro.iis.u-tokyo.ac.jp/~yamadai/MERIT_DEM/
2. Fill out the registration form
3. A download password will be emailed to you
4. Set credentials:
```bash
export CAS_MERIT_USER=your_email
export CAS_MERIT_PASSWORD=your_password
```

### TanDEM-X 90m (DLR)

Global 90m DEM from radar interferometry. Ellipsoidal heights (WGS84).

1. Register at https://sso.eoc.dlr.de/pwm-tdmdem90
2. Set credentials:
```bash
export CAS_TANDEMX_USER=your_email
export CAS_TANDEMX_PASSWORD=your_password
```

### Google Earth Engine / Dynamic World (Google Cloud)

Global 10m near-real-time land cover from Sentinel-2.

1. Create a Google Cloud project at https://console.cloud.google.com/
2. Enable the Earth Engine API
3. Create a service account with Earth Engine scope
4. Download the JSON key file
5. Set the path:
```bash
export CAS_GEE_SERVICE_ACCOUNT_KEY=/path/to/service-account-key.json
```
Or authenticate interactively: `earthengine authenticate`

### Finland DEM 2m (Maanmittauslaitos)

National high-resolution DEM from the Finnish Land Survey.

1. Register at https://www.maanmittauslaitos.fi/rajapinnat/api-avaimen-ohje
2. Create a free API key
3. Set the key:
```bash
export CAS_MML_API_KEY=your_key
```

### Denmark DEMs (Dataforsyningen / SDFI)

National DHM 0.4m and terrain data from the Danish Agency for Data Supply.

1. Register at https://dataforsyningen.dk/
2. Create a user and generate an API token
3. Set the token:
```bash
export CAS_DATAFORSYNINGEN_TOKEN=your_token
```

### Germany DGM200 (BKG)

National 200m DEM from the German Federal Agency for Cartography.

1. Register at https://gdz.bkg.bund.de/
2. Request a UUID access token (free for open data services)
3. Set the token:
```bash
export CAS_BKG_UUID=your_uuid
```

### Copernicus CORINE Land Cover (Copernicus Dataspace)

European land cover at 100m from the Copernicus programme.

1. Register at https://dataspace.copernicus.eu/
2. Generate an API token
3. Set the token:
```bash
export CAS_COPERNICUS_TOKEN=your_token
```

### Digital Earth Africa (DEA)

Pan-African SRTM derivatives, ESA WorldCover, fractional cover, water observations, cropland extent, and NDVI climatology via WCS.

Access may be restricted by region. If you receive 403 errors:
1. Check https://www.digitalearthafrica.org/ for current access policies
2. DEA services may require access from African IP ranges or API registration

### NASA Earthdata (ASTER GDEM, MODIS)

Some NASA products (e.g. `aster_gdem`, MODIS via `modis_lc`) require a free Earthdata login.

1. Register at https://urs.earthdata.nasa.gov/
2. Set credentials:
```bash
export CAS_EARTHDATA_USER=your_username
export CAS_EARTHDATA_PASSWORD=your_password
```

## Adding a Provider

1. Create `src/cas/connectors/my_provider.py`
2. Subclass `BaseConnector`, implement `list_datasets()` and `extract()`
3. Decorate with `@register("my_provider")`
4. Add entry to `inventory/providers.yaml`
5. Create `tests/connectors/test_my_provider.py`

```python
@register("my_provider")
class MyProviderConnector(WCSMixin, BaseConnector):
    slug = "my_provider"
    display_name = "My Provider"
    base_url = "https://api.example.com"
    protocol = "wcs"

    async def list_datasets(self) -> list[Dataset]:
        ...

    async def extract(self, dataset_id, geometry, time_range=None) -> AttributeResult:
        ...
```

For providers requiring registration, use `RegistrationRequiredError` with clear instructions:

```python
from cas.core.exceptions import RegistrationRequiredError

class MyGatedConnector(BaseConnector):
    def _get_credentials(self):
        key = os.environ.get("CAS_MY_PROVIDER_KEY", "")
        if not key:
            raise RegistrationRequiredError(
                self.slug,
                "https://provider.example.com/register",
                "Register for a free API key, then:\n  export CAS_MY_PROVIDER_KEY=your_key",
            )
        return key
```

## Development

```bash
pip install -e ".[dev,stac]"
ruff check src/ tests/
mypy src/cas/ --ignore-missing-imports
pytest tests/ -v                       # unit tests (no network)
```

### End-to-end extraction checks

Tests marked `network` run a real `extract()` against live upstream
providers and are excluded from the default run. Each provider is tested
over a **coverage-derived** test polygon — a small area inside the
provider's own declared coverage (see `cas.monitor.test_geometry`), so
country-specific connectors are exercised over data they actually serve
rather than a single fixed point.

```bash
pytest tests/test_e2e_extract.py -m network -v          # sweep all providers
pytest tests/test_e2e_extract.py -m network -k usgs_3dep # one provider

cas health                  # CLI equivalent: end-to-end sweep + summary
cas health -s usgs_3dep     # single provider
cas health --strict         # exit non-zero if any provider is down
cas verify                  # fast endpoint reachability only (no extraction)
```

## License

MIT
