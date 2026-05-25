# CAS — Community Attribute Service

Harmonized access to global geospatial attribute datasets (DEM, soil, land cover, climate, vegetation) through a community-driven, open-source passthrough service.

CAS is **not a data warehouse** — it's a QC layer and one-stop-shop that pulls from upstream providers on-demand, validates responses, and returns harmonized results.

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
POST /api/v1/extract     — Extract attributes for a geometry
GET  /api/v1/datasets    — List available datasets
GET  /api/v1/providers   — List registered providers
GET  /health             — Service health check
```

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

### DEM / Elevation (10 providers)

| Provider | Resolution | Coverage | Access |
|----------|-----------|----------|--------|
| Copernicus DEM GLO-30 | 30m | Global | Open |
| Copernicus DEM GLO-90 | 90m | Global | Open |
| USGS 3DEP | 10m | US | Open |
| NASADEM (SRTM) | 30m | 56S–60N | Open |
| ALOS World 3D | 30m | Global | Open |
| ArcticDEM | 10m | >50N | Open |
| IslandsDEM | 10m | Iceland | Open |
| OpenTopography | 30–90m | Global (7 DEMs) | API key (free) |
| MERIT DEM | 90m | Global | Registration |
| TanDEM-X | 90m | Global | Registration |
| FABDEM | 30m | Global | Open (non-commercial) |

### Soil

| Provider | Resolution | Coverage | Access |
|----------|-----------|----------|--------|
| ISRIC SoilGrids 2.0 | 250m | Global | Open |

### Land Cover

| Provider | Resolution | Coverage | Access |
|----------|-----------|----------|--------|
| ESA WorldCover | 10m | Global | Open |

See `inventory/providers.yaml` for the full catalog of planned providers.

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

Pan-African SRTM derivatives and ESA WorldCover via WCS.

Access may be restricted by region. If you receive 403 errors:
1. Check https://www.digitalearthafrica.org/ for current access policies
2. DEA services may require access from African IP ranges or API registration

### Sweden DEM/Soil (Lantmateriet)

Swedish national elevation and soil data.

1. Register at https://www.lantmateriet.se/sv/geodata/vara-produkter/
2. Generate an API key
3. Set the key:
```bash
export CAS_LANTMATERIET_API_KEY=your_key
```

### New Zealand LRIS (Landcare Research)

New Zealand land resource data (soil, land cover).

1. Register at https://lris.scinfo.org.nz/
2. Create an API key from your account settings
3. Set the key:
```bash
export CAS_LRIS_API_KEY=your_key
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
pytest tests/ -v
```

## License

MIT
