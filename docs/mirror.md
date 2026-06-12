# Curated mirror tier (in-process)

CAS serves attribute statistics by **passthrough** (no storage; every stats
request hits the upstream provider), rasters by **bbox subsetting** at
request time, and a small set of bulk-download-only vector datasets via
**curated local mirrors** — version-pinned, checksummed copies materialized
on *your* disk, on first use or by explicit sync. The hosted HTTP service
remains stats-only: it neither stores nor redistributes mirrored data.

That three-tier identity statement is the design's north star. The mirror
tier is a **client-side reproducibility feature**, not a data service: it
gives pinned versions, manifests, and checksums for datasets the community
otherwise re-downloads ad hoc.

!!! warning "No hosting, ever"
    Storage lives on the user's end. CAS is only ever a download client plus
    a local subsetter — it never hosts or redistributes mirror data, and the
    HTTP API mounts **no** mirror endpoints. A local mirror is download-client
    behavior (CAS acting as your agent), legally identical to what a manual
    download would be.

## Storage model

Mirrors live under `CAS_MIRROR_DIR` (default `~/.cas/mirror`). Each dataset
version gets its own directory with a manifest and a converted query layer:

```
$CAS_MIRROR_DIR/
  index.json                  # mirror-wide: datasets present, totals
  wokam/1.0/
    manifest.json
    wokam_v1.0.parquet        # hilbert-sorted GeoParquet query layer
```

At materialization time CAS downloads the source archive to a `*.part` temp
file while streaming a sha256, atomically renames it, extracts **only** the
needed vector layer, and converts that layer to a hilbert-sorted GeoParquet
file (GeoParquet 1.1 with a per-row-group bbox covering column, row groups of
65 536 rows). The archive and the extracted shapefile are then dropped — the
parquet is ~3× smaller and bbox reads prune row groups instead of scanning —
and their checksums are recorded in the manifest.

### Concurrency and read-only roots

Materialization takes an **exclusive `fcntl` lock** on the dataset directory.
A second process (e.g. another calibration worker) arriving mid-download
blocks on the lock, then finds the finished manifest and returns without
re-downloading — lazy first-use under parallel workers is one download, not a
thundering herd.

A non-writable mirror root is fine for **reads** of an already-materialized
dataset (the HPC pattern: an admin syncs into a read-only group share). Trying
to *materialize* into a read-only root fails with an actionable error naming
`cas mirror sync` and the group-admin path.

### Versioning and integrity

A mirror dataset id is the pair `(slug, version)`; requests default to the
pinned version and `slug==version` pins explicitly. There is no "latest"
alias. These are static releases — no TTL, ever; a version is only superseded,
never stale.

Upstreams here publish no checksums, so the archive sha256 is
**trust-on-first-fetch**: computed at first materialization and recorded into
the local manifest. Once a maintainer bakes an expected sha256 into the
shipped registry, TOFU upgrades to verified and any mismatch becomes a hard
`MirrorIntegrityError` carrying both hashes — an upstream silently replacing a
file under the same version is never silently accepted.

## CLI

```console
$ cas mirror sync wokam            # materialize explicitly (HPC pre-staging, CI)
$ cas mirror sync glhymps==2.0     # pin a version
$ cas mirror status                # per-dataset disk use, version, license, checksum state
$ cas mirror verify [wokam]        # full sha256 re-checksum against the manifest
$ cas mirror remove wokam          # reclaim disk
```

`sync` is the same code path as lazy first-use materialization — run it on a
network-connected node (e.g. an HPC login node) to pre-stage data for offline
compute nodes. Set `CAS_MIRROR_OFFLINE=1` to turn materialization into a hard
error (compute-node safety); reads of already-materialized data still work.
Set `CAS_MIRROR_AUTO_MATERIALIZE=false` to forbid lazy download-on-first-use.

### License acknowledgment

Some datasets (none in this slice, but the mechanism ships now — e.g.
HydroBASINS in a later slice) require explicit license acknowledgment before
CAS downloads them on your behalf. `cas mirror sync` surfaces the terms and
records acceptance with a timestamp in the manifest; it never accepts
silently. In non-interactive contexts pass `--accept-licenses` or set
`CAS_MIRROR_ACCEPT_LICENSES=slug1,slug2`. The lazy in-process path **refuses**
un-acknowledged datasets with an actionable error rather than prompting.

## In-process subset query

```python
import cas

result = cas.mirror_subset_sync(
    "wokam",
    bbox=(9.0, 46.0, 13.0, 47.5),     # (min_lon, min_lat, max_lon, max_lat), EPSG:4326
    output_dir="/path/to/out",
)
print(result.path)            # .../wokam_v1.0_subset.gpkg
print(result.feature_count)
print(result.attribution)     # carried onto the result and into the gpkg metadata
print(result.provenance)
```

The bbox is expanded by the dataset's default buffer (0.1° for GLHYMPS and
HydroLAKES, 0.5° for WOKAM) unless `buffer_deg` overrides it, reprojected to
the layer's source CRS for the filter, and features intersecting it are
returned whole (no clipping) as a single-layer **GeoPackage** — the format
SYMFLUENCE opens today. `columns=` projects attributes on read (the mirror
keeps all source columns); empty results are valid (e.g. WOKAM outside karst
regions). The first query lazily materializes the dataset unless offline mode
is set. An `await cas.mirror_subset(...)` async form is also exported.

## Unit-structured datasets and lazy region selection

Some datasets are distributed as regional units rather than one global
archive. CAS materializes only the units a query (or an explicit
`cas mirror sync slug:unit`) needs, under `units/<unit>/`, and the manifest
accumulates units as they land. A `slug:unit` spec names a unit
(`rgi7:11`, `rgi7==7.0:06`); `cas mirror sync rgi7` without a unit syncs every
region. For subset datasets a **bbox → unit** resolver picks the intersecting
units lazily, so `mirror_subset("rgi7", bbox=...)` pulls only the regions the
domain touches (and produces an honest empty result, downloading nothing, when
the bbox is unglaciated).

## RGI 7.0 — NASA Earthdata credential flow

RGI 7.0 glacier outlines are distributed by NSIDC behind **Earthdata Login**.
CAS fetches with **your** credentials (it is your download agent; credentials
are never written to the manifest or logged). The redirect dance —
`daacdata.apps.nsidc.org` → `urs.earthdata.nasa.gov` → back with an auth code —
is walked explicitly; a bearer token rides every hop, while Basic credentials
are applied only on the URS host.

Provide credentials by any one of (precedence order):

```console
$ export EARTHDATA_TOKEN=<token>            # URS → My Profile → Generate Token
# or ~/.netrc:  machine urs.earthdata.nasa.gov login <user> password <pass>
# or:  export EARTHDATA_USERNAME=<user> EARTHDATA_PASSWORD=<pass>
```

Without credentials a sync/subset fails with an actionable `MirrorAuthError`
(where to register at <https://urs.earthdata.nasa.gov/users/new>, where
credentials go). RGI 7.0 has **19** first-order regions (units `01`–`19`);
the bbox→region table lives in `cas.mirror.units`. Rasterization and HRU
intersection stay in the consumer (SYMFLUENCE).

```python
import cas
# Iceland domain — pulls only region 06 (~2.3 MB), CC-BY 4.0
result = cas.mirror_subset_sync("rgi7", bbox=(-25, 63, -13, 67), output_dir="/tmp/rgi")
```

## Stats over a mirror — GLHYMPS `extract()`

A mirror-backed dataset can also serve **zonal statistics** through the normal
extraction engine (`cas.extract` / `extract_sync` / `batch`), not just
`mirror_subset`. GLHYMPS is the motivating case: its old HTTP connector was
disabled (pygeoglim's real API is CONUS-only), and the mirror resurrects it
with **global** coverage.

```python
import cas

req = cas.AttributeRequest(
    geometry={"type": "Polygon", "coordinates": [[[7, 45], [11, 45], [11, 47], [7, 47], [7, 45]]]},
    dataset_ids=["glhymps:permeability", "glhymps:porosity"],
)
resp = cas.extract_sync(req)
for r in resp.results:
    print(r.dataset_id, r.value, r.units, r.coverage_fraction)
```

`glhymps:permeability` (column `logK_Ice`), `glhymps:permeability_permafrost_free`
(`logK_Ferr`) and `glhymps:porosity` (`Porosity`) are **area-weighted means**
over the query polygon. GLHYMPS's source CRS is already World Cylindrical
Equal Area, so intersection areas are measured directly in the layer CRS (the
generic path reprojects to an equal-area CRS centred on the query).
`coverage_fraction` is the intersected-area fraction — the honest "how much of
your polygon the mirror covered" signal; a query off coverage returns
`MISSING`. A point query returns the value of the covering polygon. The first
call lazily materializes the GLHYMPS mirror (a multi-GB download); pre-stage
with `cas mirror sync glhymps` on a networked node.

Mirror-backed connectors report **manifest-integrity health**, never a network
probe (design §5): HEALTHY when the mirror is materialized and intact,
DEGRADED when not yet synced ("not synced" is not an outage), DOWN on an
integrity failure. The scheduled Provider Health Check and the reachability
sweep both skip mirror connectors' (non-existent) network endpoints.

## Geofabrics — path delivery, not subsetting

Geofabrics (river-network + catchment topology) follow a **different contract**
from attribute vectors. CAS materializes **topology-complete versioned units**
and delivers **verified local paths** — it never bbox-clips them. A bbox cannot
guarantee upstream closure, so clipping a geofabric before the consumer's
upstream-trace would silently truncate drainage area (a correctness
regression). The trace itself (NetworkX ancestors; the NWS `network` table)
stays in the consumer — SYMFLUENCE's `GeofabricSubsetter`, which already reads
the `.shp`/`.gpkg`/`.parquet` formats the mirror produces.

```python
import cas

# Deliver one topology-complete unit by id (returns a list — one result per unit)
(res,) = cas.mirror_fetch_sync("merit_basins", unit="7")
res.path, res.paths["catchments"], res.paths["rivernet"], res.format

# Or resolve the unit(s) from a domain bbox / pour point
results = cas.mirror_fetch_sync("tdx_hydro", bbox=(-72, -13, -70, -11))
results = cas.mirror_fetch_sync("nws_hydrofabric", point=(40.0, -105.0))
```

`mirror_fetch` (sync + async) takes exactly one selector — `unit=`, `units=`,
`bbox=`, or `point=` — lazily materializes the resolved unit(s), and returns a
`MirrorFetchResult` per unit carrying `paths` (role → path), `format`,
`notice_path` (the verbatim license notice when one is mandated),
`license`/`attribution`/`citation`/`disclaimer`, and `provenance`. With
`output_dir=` the files (and notice) are copied there; otherwise the in-mirror
paths are returned (true path delivery, no copy).

Unit schemes and selection:

| Dataset | Version | Unit scheme | Selector | Format delivered |
|---|---|---|---|---|
| HydroBASINS | v1c | continental region × Pfaf level (`na_lev06`) | explicit unit; `bbox` via region table | GeoPackage (from shapefile) |
| MERIT-Basins | 1 | Pfaf-L1 region (codes 1–9) | `bbox`/`point` via Pfaf table | GeoPackage pair (catchments + rivernet) |
| TDX-Hydro / GEOGLOWS | v2 | VPU (~125) | `bbox` via the vpu-boundaries index | GeoParquet (catchments) + GeoPackage (streams), as upstream ships |
| NWS NextGen | v2.2 | single CONUS unit | `unit="conus"` / any CONUS `point` | GeoPackage (extracted from the tar.gz) |

`cas mirror sync tdx_hydro` **without a unit is refused** — the global set is
~25–40 GB; name the VPU(s) you need (`cas mirror sync tdx_hydro:714`) or let
`mirror_fetch(..., bbox=...)` resolve them. HydroBASINS likewise can't be
fully synced (region × 12 levels); name a unit.

### Geofabric disk costs (surfaced by `cas mirror sync`/`status` before download)

| Dataset | Per-unit download | Materialized | Notes |
|---|---|---|---|
| HydroBASINS v1c | 0.05–0.3 GB / (region, level) | ~0.1–0.3 GB | varies sharply with Pfaf level |
| MERIT-Basins | 0.4 GB (catch) + 0.15 GB (riv) / region | ~1 GB / region; ~8 GB all 9 | |
| TDX-Hydro / GEOGLOWS | 0.1–0.3 GB / VPU | ~0.5 GB / VPU; ~25–40 GB global (refused) | per-VPU lazy mandatory |
| NWS NextGen | 1.6 GB tar.gz | ~7 GB GeoPackage (~9 GB transient) | single CONUS unit |

### HydroBASINS license acknowledgment + Exhibit B

HydroBASINS v1c is **not** CC-BY: it is distributed under the bespoke WWF
*HydroSHEDS v1 License Agreement* (design §8). CAS therefore requires a
**one-time acknowledgment** before downloading (interactive prompt,
`--accept-licenses`, or `CAS_MIRROR_ACCEPT_LICENSES=hydrobasins`; the lazy
`mirror_fetch` path refuses outright otherwise), and copies the verbatim
**Exhibit B "Required Attributions"** notice
(`src/cas/mirror/notices/hydrosheds_v1_exhibit_b.txt`, extracted verbatim from
the HydroSHEDS TechDoc v1.4) next to every materialized unit, referencing it in
the manifest and on the fetch result.

## Datasets and licenses

License verdicts below are verbatim from the design's verification pass for a
**local-only** mirror (CAS = download client; no hosting). Attribution strings
are embedded in every subset output's metadata and carried on the result.

| Dataset | Version | License (verified) | Auth | Units | Attribution / obligation |
|---|---|---|---|---|---|
| GLHYMPS | 2.0 | CC-BY 4.0 (Borealis record, `termsOfUse: none`) — verified | none | global | Cite Huscroft et al. 2018 + DOI 10.5683/SP2/TTJNIU |
| HydroLAKES | 1.0 | CC-BY 4.0 — verified | none | global | Cite Messager et al. 2016 |
| WOKAM | 1 | BGR GSTC; GeoNutzV likely prevails but **unconfirmed by BGR for this product** | none | global | License field = "BGR terms (GeoNutzV-eligible, unconfirmed)"; attribution "Datenquelle: WHYMAP WOKAM, © BGR Berlin, IAH Reading, KIT Karlsruhe, UNESCO Paris 2017"; never republish the layer |
| RGI | 7.0 | CC-BY 4.0 — verified; distribution Earthdata-gated (live-probed 401→URS) | **Earthdata** | 19 regions | NSIDC citation with access date (filled from `retrieved_at`); doi:10.5067/f6jmovy5navz |

| HydroBASINS | v1c | WWF *HydroSHEDS v1 License Agreement* — **NOT CC-BY** (verified) | none | path / region×level | One-time acknowledgment + verbatim Exhibit B notice next to every unit |
| MERIT-Basins | 1 | ODbL-1.0 **OR** CC-BY-NC-4.0 (dual; user's choice) | none | path / Pfaf-L1 | `license-fork:odbl-or-cc-by-nc` flag; never side-door MERIT-Hydro rasters |
| TDX-Hydro / GEOGLOWS | v2 | TDX-Hydro CC-BY-**SA** 4.0 (© NGA); GEOGLOWS dist. CC-BY 4.0 | none | path / VPU | `share-alike` flag; carry BOTH notices |
| NWS NextGen | v2.2 | not stated at source (live-probed); upstream NOAA-OWP/Lynker ODbL 1.0 assumed | none | path / CONUS | `license-unverified-at-source`; PROVISIONAL-data disclaimer |
