# Provider Catalog

CAS registers **214 active providers** spanning DEM/elevation, soil, land cover,
hydrology, vegetation/canopy, geology, and biodiversity — global flagships plus
**162 national/regional providers across 34 countries**.

The catalog is **discoverable at runtime** — you never need to trust a static
list:

=== "HTTP"

    ```bash
    curl -s 'localhost:8000/api/v1/providers?limit=1000'
    curl -s localhost:8000/api/v1/providers/copernicus_dem
    ```

=== "SDK"

    ```python
    from cas.client import CASClient
    with CASClient("http://localhost:8000") as cas:
        for p in cas.providers(limit=1000).providers:
            print(p.slug, p.protocol)
    ```

=== "CLI"

    ```bash
    cas providers
    cas datasets -p copernicus_dem
    ```

The complete machine-readable catalog (resolution, bbox, license, variables) is
also committed at `inventory/providers.yaml` and regenerated with
`cas export-inventory`.

## By category (headline datasets)

Roughly: DEM/Elevation ~46, Soil ~46, Land Cover ~46, Hydrology/Water ~35,
Vegetation/Canopy ~20, Geology ~7, plus biodiversity/ecology and other thematic
layers.

### DEM / Elevation

| Provider | Slug | Resolution | Coverage |
| --- | --- | --- | --- |
| Copernicus DEM GLO-30 | `copernicus_dem` | 30m | Global |
| Copernicus DEM GLO-90 | `cop_dem_90` | 90m | Global |
| USGS 3DEP | `usgs_3dep` | 10m | US |
| NASADEM (SRTM) | `nasadem` | 30m | 56S–60N |
| ALOS World 3D | `alos_dem` | 30m | Global |
| ASTER GDEM v3 | `aster_gdem` | 30m | Global (Earthdata login) |
| ArcticDEM | `arctic_dem` | 10m | >50N |
| REMA (Antarctica) | `rema` | 8m | <53S |
| GEBCO Bathymetry | `gebco` | 500m | Global |

Plus ~32 national/regional DEMs (Australia, Canada HRDEM, Japan GSI, most of Europe).

### Soil

| Provider | Slug | Resolution | Coverage |
| --- | --- | --- | --- |
| ISRIC SoilGrids 2.0 | `isric_soilgrids` | 250m | Global |
| SoilGrids derived (OCS / WRB) | `soilgrids_derived` | 250m | Global |
| OpenLandMap | `openlandmap` | 250m | Global |
| SSURGO / gNATSGO | `ssurgo`, `gnatsgo` | 30m | US |
| SLGA | `slga` | 90m | Australia |

### Land Cover

| Provider | Slug | Resolution | Coverage |
| --- | --- | --- | --- |
| ESA WorldCover | `esa_worldcover` | 10m | Global |
| Dynamic World | `dynamic_world` | 10m | Global |
| Impact Observatory LULC | `io_lulc`, `io_lulc_9class` | 10m | Global |
| MODIS MCD12Q1 | `modis_lc` | 500m | Global |
| CORINE Land Cover | `corine_lc` | 100m | Europe |
| NLCD | `nlcd` | 30m | US |
| MapBiomas (Brazil) | `mapbiomas_brazil` | 30m | Brazil |
| DEA Africa cropland / NDVI / mangroves | `dea_africa_*` | varies | Africa |

### Hydrology, Vegetation, Geology, Biodiversity

`merit_hydro`, `jrc_gsw`, `modis_snow`, `permafrost`, `ramsar_wetlands`;
`canopy_height` (ETH 10m), `hansen_forest`, biomass layers; national bedrock /
lithology / quaternary geology; `biodiversity`, `mobi`, `brazil_biomes`.

## National coverage

162 of the 214 providers are country/region-specific across 34 countries — with
the deepest coverage in Germany (20), USA (18), Ireland (13), Switzerland (12),
Australia (10), the UK (10), and Norway (9). Most European, North American, and
Australian connectors are open (OGC WCS/WMS or STAC+COG); a handful require free
registration.

!!! tip
    Counts here are a point-in-time snapshot. The authoritative live list is
    always `cas providers` / `GET /api/v1/providers`.
