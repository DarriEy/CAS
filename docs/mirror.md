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

Later slices add the geofabric path-delivery datasets (HydroBASINS,
MERIT-Basins, TDX-Hydro, NWS NextGen), which carry verbatim license notices
and, for HydroBASINS, the acknowledgment prompt.
