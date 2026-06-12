# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""The shipped mirror registry — version-pinned dataset declarations.

A mirror dataset id is the pair ``(slug, version)`` (design §2). Requests
default to the registry's pinned default version; ``slug==version`` pins
explicitly. There is deliberately no "latest" alias — reproducibility over
convenience.

Slice 1 ships the three cleanest attribute vectors (GLHYMPS v2.0,
HydroLAKES v1.0, WOKAM v1). RGI 7.0 (Earthdata credential flow) and the
geofabric path-delivery datasets (HydroBASINS, MERIT-Basins, TDX-Hydro,
NWS NextGen) slot in as later registry entries with per-unit sources.

Checksums: the upstreams here publish none, so registry ``sha256`` starts as
``None`` (trust-on-first-fetch, recorded into the local manifest) and is
baked in once a maintainer has fetched and recorded it — at which point TOFU
upgrades to verified and mismatches become hard errors (design §2).
"""

from __future__ import annotations

from collections.abc import Callable

from cas.core.exceptions import MirrorDatasetNotFoundError, MirrorUnitError
from cas.mirror.models import MirrorDataset, MirrorLicense, MirrorSource

_CC_BY_4 = "https://creativecommons.org/licenses/by/4.0/"

_REGISTRY: dict[str, dict[str, MirrorDataset]] = {}
_DEFAULT_VERSIONS: dict[str, str] = {}

_UNIT_SOURCE_FACTORIES: dict[str, Callable[[MirrorDataset, str], list[MirrorSource]]] = {}
"""slug → factory building per-unit sources for ``dynamic_units`` datasets
(TDX-Hydro VPUs cannot be enumerated statically)."""


def register_mirror_dataset(dataset: MirrorDataset, *, default: bool = True) -> None:
    """Add a dataset declaration to the mirror registry.

    Used by the shipped declarations below; also the extension point for
    later slices (RGI, geofabrics) and for test doubles.
    """
    versions = _REGISTRY.setdefault(dataset.slug, {})
    if dataset.version in versions:
        raise ValueError(f"mirror dataset {dataset.spec} is already registered")
    versions[dataset.version] = dataset
    if default or dataset.slug not in _DEFAULT_VERSIONS:
        _DEFAULT_VERSIONS[dataset.slug] = dataset.version


def unregister_mirror_dataset(slug: str, version: str | None = None) -> None:
    """Remove a registration (test/teardown helper)."""
    if slug not in _REGISTRY:
        return
    if version is None:
        del _REGISTRY[slug]
        _DEFAULT_VERSIONS.pop(slug, None)
        return
    _REGISTRY[slug].pop(version, None)
    if not _REGISTRY[slug]:
        del _REGISTRY[slug]
        _DEFAULT_VERSIONS.pop(slug, None)


def parse_dataset_spec(spec: str) -> tuple[str, str | None]:
    """Split ``slug`` / ``slug==version`` into its parts."""
    if "==" in spec:
        slug, _, version = spec.partition("==")
        return slug.strip(), version.strip() or None
    return spec.strip(), None


def parse_unit_spec(spec: str) -> tuple[str, str | None]:
    """Split ``slug[==version][:unit]`` into (dataset spec, unit).

    e.g. ``"rgi7:11"`` → ``("rgi7", "11")``;
    ``"hydrobasins:na_lev06"`` → ``("hydrobasins", "na_lev06")``.
    """
    base, _, unit = spec.partition(":")
    return base.strip(), unit.strip() or None


def register_unit_source_factory(
    slug: str, factory: Callable[[MirrorDataset, str], list[MirrorSource]]
) -> None:
    """Register a per-unit source builder for a ``dynamic_units`` dataset."""
    _UNIT_SOURCE_FACTORIES[slug] = factory


def sources_for_unit(ds: MirrorDataset, unit: str) -> list[MirrorSource]:
    """Sources for one unit — static registry entries or the dynamic factory."""
    static = ds.sources_for_unit(unit)
    if static:
        return static
    factory = _UNIT_SOURCE_FACTORIES.get(ds.slug)
    if factory is not None:
        return factory(ds, unit)
    known = ds.unit_ids()
    raise MirrorUnitError(
        f"Unknown unit '{unit}' for mirror dataset '{ds.spec}'. "
        f"Known units: {', '.join(known) or '(none declared)'}."
    )


def get_mirror_dataset(spec: str) -> MirrorDataset:
    """Resolve ``slug`` or ``slug==version`` to a registry entry.

    Raises :class:`MirrorDatasetNotFoundError` with the known slugs/versions
    when the spec does not resolve.
    """
    slug, version = parse_dataset_spec(spec)
    versions = _REGISTRY.get(slug)
    if not versions:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise MirrorDatasetNotFoundError(
            f"Unknown mirror dataset '{slug}'. Known datasets: {known}."
        )
    if version is None:
        version = _DEFAULT_VERSIONS[slug]
    if version not in versions:
        known_versions = ", ".join(sorted(versions))
        raise MirrorDatasetNotFoundError(
            f"Unknown version '{version}' for mirror dataset '{slug}'. "
            f"Known versions: {known_versions}."
        )
    return versions[version]


def list_mirror_datasets(*, all_versions: bool = False) -> list[MirrorDataset]:
    """Registered datasets — default (pinned) versions unless ``all_versions``."""
    if all_versions:
        return [ds for versions in _REGISTRY.values() for ds in versions.values()]
    return [_REGISTRY[slug][_DEFAULT_VERSIONS[slug]] for slug in sorted(_REGISTRY)]


# ── Shipped declarations (slice 1) ──────────────────────────────────

register_mirror_dataset(
    MirrorDataset(
        slug="glhymps",
        version="2.0",
        display_name="GLHYMPS 2.0 — GLobal HYdrogeology MaPS",
        description=(
            "Global polygon-based subsurface permeability and porosity "
            "(consolidated and unconsolidated)."
        ),
        sources=[
            MirrorSource(
                url="https://borealisdata.ca/api/access/datafile/71909",
                archive_name="GLHYMPS.zip",
                sha256=None,
                size_bytes_approx=2_600_000_000,  # ~2.4 GB
            ),
        ],
        shapefile_patterns=["*glhymps*.shp", "*.shp"],
        source_crs_note="World Cylindrical Equal Area (delivered in source CRS)",
        default_buffer_deg=0.1,
        known_columns=[
            "Porosity",
            "logK_Ice",
            "logK_Ferr",
            "Porosity_x",
            "logK_Ice_x",
        ],
        license=MirrorLicense(
            license="CC-BY-4.0",
            license_url=_CC_BY_4,
            license_verified=True,  # Borealis record, termsOfUse: none (design §8)
            attribution="GLHYMPS 2.0 (Huscroft et al., 2018), CC-BY 4.0, doi:10.5683/SP2/TTJNIU",
            license_flags=[],
            requires_acknowledgment=False,
        ),
        citation=(
            "Huscroft, J., Gleeson, T., Hartmann, J., & Börker, J. (2018). "
            "Compiling and mapping global permeability of the unconsolidated "
            "and consolidated Earth: GLobal HYdrogeology MaPS 2.0 (GLHYMPS 2.0). "
            "Geophysical Research Letters, 45, 1897-1904. "
            "https://doi.org/10.1002/2017GL075860. Data: https://doi.org/10.5683/SP2/TTJNIU"
        ),
        approx_materialized_bytes=2_200_000_000,
    )
)

register_mirror_dataset(
    MirrorDataset(
        slug="hydrolakes",
        version="1.0",
        display_name="HydroLAKES v1.0 — global lake polygons",
        description="Global lake polygons with volume, depth, residence-time attributes.",
        sources=[
            MirrorSource(
                url="https://data.hydrosheds.org/file/hydrolakes/HydroLAKES_polys_v10_shp.zip",
                archive_name="HydroLAKES_polys_v10_shp.zip",
                sha256=None,
                size_bytes_approx=500_000_000,  # ~0.47 GB
            ),
        ],
        shapefile_patterns=["*hydrolakes_polys_v10*.shp", "*.shp"],
        default_buffer_deg=0.1,
        known_columns=[
            "Hylak_id",
            "Lake_type",
            "Lake_area",
            "Vol_total",
            "Depth_avg",
            "Dis_avg",
            "Res_time",
            "Elevation",
            "Wshd_area",
            "Pour_long",
            "Pour_lat",
        ],
        license=MirrorLicense(
            license="CC-BY-4.0",
            license_url=_CC_BY_4,
            license_verified=True,  # hydrosheds.org post-2021 license update (design §8)
            attribution="HydroLAKES v1.0 (Messager et al., 2016), CC-BY 4.0, https://www.hydrosheds.org",
            license_flags=[],
            requires_acknowledgment=False,
        ),
        citation=(
            "Messager, M.L., Lehner, B., Grill, G., Nedeva, I., & Schmitt, O. (2016). "
            "Estimating the volume and age of water stored in global lakes using a "
            "geo-statistical approach. Nature Communications, 7, 13603. "
            "https://doi.org/10.1038/ncomms13603"
        ),
        approx_materialized_bytes=1_500_000_000,
    )
)

register_mirror_dataset(
    MirrorDataset(
        slug="wokam",
        version="1.0",
        display_name="WOKAM v1 — World Karst Aquifer Map",
        description=(
            "Global karstifiable rock outcrops (carbonate/evaporite/mixed); "
            "empty subset results are normal outside karst regions."
        ),
        sources=[
            MirrorSource(
                url="https://download.bgr.de/bgr/grundwasser/whymap/shp/WHYMAP_WOKAM_v1.zip",
                archive_name="WHYMAP_WOKAM_v1.zip",
                sha256=None,
                size_bytes_approx=22_000_000,  # ~21 MB
            ),
        ],
        # The archive carries karst polygon, cave and spring layers; the
        # consumers (design §0) use the karst polygon layer only.
        shapefile_patterns=["*karst*poly*.shp", "*wokam*.shp", "*.shp"],
        default_buffer_deg=0.5,
        # Design §0 names the carbonate/evaporite classifier "ROCK_TYPE"/"Type";
        # the live v1 distribution's polygon layer carries it lowercased as
        # ``rock_type`` (with a ``RTypeLabel`` text label). The mirror keeps all
        # columns regardless — these document the consumed set for the shim.
        known_columns=["rock_type", "RTypeLabel"],
        license=MirrorLicense(
            license="BGR terms (GeoNutzV-eligible, unconfirmed)",
            license_url="https://www.whymap.org",
            license_verified=False,  # unconfirmed by BGR for this product (design §8)
            attribution=(
                "Datenquelle: WHYMAP WOKAM, © BGR Berlin, IAH Reading, "
                "KIT Karlsruhe, UNESCO Paris 2017"
            ),
            license_flags=["unconfirmed"],
            requires_acknowledgment=False,
        ),
        citation=(
            "Goldscheider, N., Chen, Z., Auler, A.S., et al. (2020). Global "
            "distribution of carbonate rocks and karst water resources. "
            "Hydrogeology Journal, 28, 1661-1677. "
            "https://doi.org/10.1007/s10040-020-02139-5"
        ),
        approx_materialized_bytes=500_000_000,
    )
)


# ── RGI 7.0 (slice 2a) — per-region units behind Earthdata Login ────

# Exact archive names and sizes live-verified against the NSIDC listing
# (authenticated GET of regional_files/RGI2000-v7.0-G/, 2026-06-12).
# RGI 7.0 has 19 first-order regions — not 20; older tooling's "region 20"
# does not exist upstream. The bbox→region table is cas.mirror.units.
_RGI_BASE_URL = (
    "https://daacdata.apps.nsidc.org/pub/DATASETS/nsidc0770_rgi_v7/"
    "regional_files/RGI2000-v7.0-G/"
)

_RGI_REGION_FILES: dict[str, tuple[str, int]] = {
    # unit → (region name slug, approx archive bytes from the NSIDC listing)
    "01": ("alaska", 83_000_000),
    "02": ("western_canada_usa", 24_000_000),
    "03": ("arctic_canada_north", 15_000_000),
    "04": ("arctic_canada_south", 24_000_000),
    "05": ("greenland_periphery", 69_000_000),
    "06": ("iceland", 2_300_000),
    "07": ("svalbard_jan_mayen", 6_100_000),
    "08": ("scandinavia", 5_100_000),
    "09": ("russian_arctic", 3_800_000),
    "10": ("north_asia", 8_900_000),
    "11": ("central_europe", 6_600_000),
    "12": ("caucasus_middle_east", 4_300_000),
    "13": ("central_asia", 92_000_000),
    "14": ("south_asia_west", 50_000_000),
    "15": ("south_asia_east", 26_000_000),
    "16": ("low_latitudes", 4_100_000),
    "17": ("southern_andes", 45_000_000),
    "18": ("new_zealand", 5_100_000),
    "19": ("subantarctic_antarctic_islands", 15_000_000),
}


def rgi_archive_name(unit: str) -> str:
    name, _ = _RGI_REGION_FILES[unit]
    return f"RGI2000-v7.0-G-{unit}_{name}.zip"


register_mirror_dataset(
    MirrorDataset(
        slug="rgi7",
        version="7.0",
        display_name="RGI 7.0 — Randolph Glacier Inventory (glacier outlines)",
        description=(
            "Global glacier outlines (RGI2000-v7.0-G), distributed by NSIDC "
            "as 19 first-order regional shapefiles behind NASA Earthdata "
            "Login. Materialized lazily per region; rasterization and HRU "
            "intersection stay in the consumer (SYMFLUENCE)."
        ),
        sources=[
            MirrorSource(
                url=f"{_RGI_BASE_URL}{rgi_archive_name(unit)}",
                archive_name=rgi_archive_name(unit),
                sha256=None,
                size_bytes_approx=size,
                unit=unit,
                role="outlines",
            )
            for unit, (_name, size) in _RGI_REGION_FILES.items()
        ],
        delivery="subset",
        unit_scheme="RGI first-order region (19 regions, e.g. rgi7:11 Central Europe)",
        unit_processing="geoparquet",
        auth="earthdata",
        shapefile_patterns=["*rgi2000*-g-*.shp", "*.shp"],
        # The native handler clips outlines to the exact domain box (no
        # buffer); subset semantics match it.
        default_buffer_deg=0.0,
        # Design §0: the SYMFLUENCE consumer reads the glacier id
        # (rgi_id/RGIId/glac_id) and the debris-cover fraction when present
        # (the v7.0 G outlines may not carry a debris column; the consumer
        # tolerates absence with debris fraction 0). The mirror keeps ALL
        # source columns regardless.
        known_columns=["rgi_id", "glims_id", "cenlon", "cenlat", "area_km2"],
        license=MirrorLicense(
            license="CC-BY-4.0",
            license_url=_CC_BY_4,
            license_verified=True,  # CC-BY 4.0; distribution Earthdata-gated (design §8)
            attribution=(
                "Randolph Glacier Inventory v7.0 (RGI Consortium, 2023), "
                "CC-BY 4.0, NSIDC doi:10.5067/f6jmovy5navz"
            ),
            license_flags=[],
            requires_acknowledgment=False,
        ),
        # NSIDC requires the access date in citations; {access_date} is
        # filled from the materialization clock and recorded in the manifest
        # (retrieved_at is the authority).
        citation=(
            "RGI Consortium. (2023). Randolph Glacier Inventory - A Dataset "
            "of Global Glacier Outlines, Version 7.0. Boulder, Colorado USA. "
            "NSIDC: National Snow and Ice Data Center. "
            "https://doi.org/10.5067/f6jmovy5navz. Date Accessed {access_date}."
        ),
        approx_materialized_bytes=3_000_000_000,
        disk_note="~3 GB if all 19 regions are synced; a typical domain needs 1-2 regions.",
    )
)


# ════════════════════════════════════════════════════════════════════
#  Geofabrics (slice 2b) — PATH delivery, NOT bbox subsetting
#
#  CAS materializes topology-complete versioned units and delivers verified
#  local paths (design header decisions 1 & 4, §3). Upstream-trace closure
#  stays in the consumer (SYMFLUENCE GeofabricSubsetter). Shapefile-distributed
#  sets are converted to GeoPackage at mirror time (the subsetter reads gpkg);
#  TDX stays parquet+gpkg as upstream ships; NWS stays gpkg.
# ════════════════════════════════════════════════════════════════════


# ── HydroBASINS v1.c ────────────────────────────────────────────────
#
# Units are continental-region × Pfafstetter-level (e.g. na_lev06). The
# WWF HydroSHEDS v1 License Agreement (NOT CC-BY, design §8) requires a
# one-time acknowledgment and the verbatim Exhibit B notice next to the data.
_HYDROBASINS_URL = (
    "https://data.hydrosheds.org/file/HydroBASINS/standard/"
    "hybas_{region}_lev{level:02d}_v1c.zip"
)
_HYDROBASINS_REGIONS = {"af", "ar", "as", "au", "eu", "na", "sa", "si"}


def _parse_hydrobasins_unit(unit: str) -> tuple[str, int]:
    """``na_lev06`` → ``("na", 6)``; validates region and level."""
    region, _, lev = unit.partition("_lev")
    if region not in _HYDROBASINS_REGIONS or not lev.isdigit() or not 1 <= int(lev) <= 12:
        raise MirrorUnitError(
            f"Bad HydroBASINS unit '{unit}'. Expected '<region>_lev<NN>' with "
            f"region in {sorted(_HYDROBASINS_REGIONS)} and level 1-12 "
            f"(e.g. 'na_lev06')."
        )
    return region, int(lev)


def _hydrobasins_sources(ds: MirrorDataset, unit: str) -> list[MirrorSource]:
    region, level = _parse_hydrobasins_unit(unit)
    return [
        MirrorSource(
            url=_HYDROBASINS_URL.format(region=region, level=level),
            archive_name=f"hybas_{region}_lev{level:02d}_v1c.zip",
            sha256=None,
            size_bytes_approx=120_000_000,
            unit=unit,
            role="basins",
        )
    ]


register_unit_source_factory("hydrobasins", _hydrobasins_sources)
register_mirror_dataset(
    MirrorDataset(
        slug="hydrobasins",
        version="1c",
        display_name="HydroBASINS v1c — Pfafstetter sub-basins with topology",
        description=(
            "WWF HydroSHEDS HydroBASINS, continental-region × Pfafstetter-level "
            "units carrying basin polygons AND river topology (HYBAS_ID / "
            "NEXT_DOWN). Path-delivered; the subsetter traces upstream closure."
        ),
        sources=[],  # built per-unit by the factory above
        delivery="path",
        unit_scheme="continental region × Pfafstetter level (e.g. na_lev06)",
        unit_processing="gpkg",
        dynamic_units=True,
        # Shapefile distribution; the layer has the topology columns the
        # subsetter needs (HYBAS_ID, NEXT_DOWN). Keep ALL columns.
        shapefile_patterns=["hybas_*_lev*_v1c.shp", "*.shp"],
        known_columns=["HYBAS_ID", "NEXT_DOWN", "MAIN_BAS", "PFAF_ID", "UP_AREA"],
        notice_file="hydrosheds_v1_exhibit_b.txt",
        license=MirrorLicense(
            license="HydroSHEDS v1 License Agreement (WWF) — NOT CC-BY",
            license_url="https://www.hydrosheds.org/about",
            license_verified=True,  # design §8: bespoke WWF agreement, verified
            attribution=(
                "HydroBASINS v1c (Lehner & Grill, 2013), WWF HydroSHEDS; "
                "see the embedded Exhibit B notice"
            ),
            license_flags=["bespoke-license", "acknowledgment-required"],
            requires_acknowledgment=True,
        ),
        citation=(
            "Lehner, B. & Grill, G. (2013). Global river hydrography and network "
            "routing: baseline data and new approaches to study the world's large "
            "river systems. Hydrological Processes, 27(15), 2171-2186. "
            "HydroBASINS via https://www.hydrosheds.org. Accessed {access_date}."
        ),
        approx_materialized_bytes=500_000_000,
        disk_note="~0.1-0.3 GB per (region, level) zip; varies sharply with level.",
    )
)


# ── MERIT-Basins ────────────────────────────────────────────────────
#
# Nine Pfaf-L1 regions; ONE zip per region carrying BOTH the catchments
# (cat_pfaf_N, no .prj — WGS84 per the official ReadMe) and rivernet
# (riv_pfaf_N) shapefiles. Dual license ODbL-1.0 OR CC-BY-NC-4.0 (design §8)
# → a license-fork flag.
#
# Distribution reality (verified live 2026-06-12): the Princeton host
# (hydrology.princeton.edu) is DNS-dead (NXDOMAIN); reachhydro.org now
# distributes via a public Google Drive folder
# (https://drive.google.com/drive/folders/1uCQFmdxFbjwoT9OYJxw-pXaP8q_GYH1a,
# subfolder zip/pfaf_level_01) and Globus. No authoritative per-file plain
# HTTPS mirror exists (HydroShare hosts a North-America-only derivative;
# Zenodo re-packages carry a narrowed CC-BY-NC-SA license). The Drive *file*
# ids below are stable and public; downloads go through the virus-scan
# confirm flow in cas.mirror.gdrive. Globus remains the manual path —
# `cas mirror import merit_basins <zip> --unit N`. A "v0.7/v1.0_bugfix1"
# distribution (minor coastline fixes) exists in a sibling Drive folder
# (1J8vyqCnSdquY1cRI1PPsXzMBLBXKzzoW) and can land as a future version.
_MERIT_DRIVE_IDS: dict[str, tuple[str, int]] = {
    # unit → (Drive file id, exact content-length probed 2026-06-12)
    "1": ("1GTz5sItFBpMFHeLhBWqwyVlRQgTUeieM", 1_089_741_146),
    "2": ("1Ygj2de11ZDs6l6p90l2WBMZKXbd2dCEr", 746_690_950),
    "3": ("13OLqyc1QtNJs0itJTCfuz3l-AB7aztD_", 574_134_554),
    "4": ("1HhzYKcQ_2WKObvp2xL4cO1aS-sJ3q8G-", 744_792_507),
    "5": ("1udPibrTbyUku3ZIQtjPMTMzOwyvdQS2K", 367_626_481),
    "6": ("1vZKI9V1_kGj9mPD1aoVgN6oCBYlR_bgs", 597_765_953),
    "7": ("1zaJH8r6dFCJL6TmCya12Oq6XKRlNJHMz", 583_538_015),
    "8": ("1Lt78g4ouTRc4j28HB9O5YMltE-N0sJdw", 276_891_052),
    "9": ("1sPcZVKlyABUN41tmCj6LbC0s2-NwVC4P", 181_106_561),
}

# Fetched and recorded by a maintainer (region 9, 2026-06-12) — TOFU upgraded
# to registry-verified for that unit; the rest record on first fetch.
_MERIT_SHA256: dict[str, str | None] = {
    "9": "0803480cd3712ea34bb65c96d24349fb1a3536f9637236578c0007e6518e7d31",
}

register_mirror_dataset(
    MirrorDataset(
        slug="merit_basins",
        version="1",
        display_name="MERIT-Basins — Pfaf-L1 catchments + river network",
        description=(
            "MERIT-Basins (Lin et al., 2019), nine Pfafstetter Level-1 regions "
            "(1 Africa, 2 Europe, 3 North Asia, 4 South Asia, 5 Oceania+South "
            "Asian islands, 6 South America, 7 North America, 8 Arctic, "
            "9 Greenland — per the official ReadMe). One zip per region "
            "carrying the catchments + rivernet shapefile pair (COMID / up1-3 "
            "topology). Path-delivered; the subsetter traces upstream closure. "
            "Distributed via reachhydro.org's Google Drive folder (Globus is "
            "the manual alternative: `cas mirror import`)."
        ),
        sources=[
            MirrorSource(
                url=(
                    "https://drive.google.com/uc?export=download&id="
                    f"{file_id}"
                ),
                archive_name=f"pfaf_{code}_MERIT_Hydro_v07_Basins_v01.zip",
                sha256=_MERIT_SHA256.get(code),
                size_bytes_approx=size,
                unit=code,
                role="data",
                members={
                    "catchments": [f"cat_pfaf_{code}_*.shp"],
                    "rivernet": [f"riv_pfaf_{code}_*.shp"],
                },
            )
            for code, (file_id, size) in _MERIT_DRIVE_IDS.items()
        ],
        delivery="path",
        unit_scheme="Pfafstetter Level-1 region (codes 1-9)",
        unit_processing="gpkg",
        # cat_pfaf_* shapefiles ship without a .prj; the official ReadMe and
        # the rivernet .prj both say WGS84 — assigned, never reprojected.
        assumed_crs="EPSG:4326",
        shapefile_patterns=["*riv_pfaf_*.shp", "*cat_pfaf_*.shp", "pfaf_*.shp", "*.shp"],
        known_columns=["COMID", "NextDownID", "up1", "up2", "up3"],
        license=MirrorLicense(
            license="ODbL-1.0 OR CC-BY-NC-4.0 (user's choice; inherited from MERIT-Hydro)",
            license_url="https://opendatacommons.org/licenses/odbl/1-0/",
            license_verified=True,  # design §8: dual license verified
            attribution="MERIT-Basins (Lin et al., 2019); MERIT-Hydro (Yamazaki et al., 2019)",
            license_flags=["license-fork:odbl-or-cc-by-nc"],
            requires_acknowledgment=False,
            disclaimer=(
                "Dual-licensed ODbL-1.0 OR CC-BY-NC-4.0 — pick ONE: ODbL adds "
                "share-alike + attribution obligations; CC-BY-NC bars commercial "
                "use. Never side-door the MERIT-Hydro rasters past their "
                "registration form."
            ),
        ),
        citation=(
            "Lin, P., Pan, M., Beck, H.E., et al. (2019). Global reconstruction "
            "of naturalized river flows at 2.94 million reaches. Water Resources "
            "Research, 55, 6499-6516. Accessed {access_date}."
        ),
        approx_materialized_bytes=8_000_000_000,
        disk_note=(
            "0.2-1.1 GB zip per Pfaf-L1 region (Google Drive); "
            "~1 GB materialized per region, ~8 GB for all 9."
        ),
    )
)


# ── TDX-Hydro / GEOGLOWS v2 ─────────────────────────────────────────
#
# ~125 VPUs, enumerated only via the upstream vpu-boundaries index — per-VPU
# lazy is MANDATORY; `cas mirror sync tdx_hydro` without a unit is refused
# (design §1: ~25-40 GB global). Catchments ship as GeoParquet, streams as
# GeoPackage — kept as upstream ships them. Dual CC-BY-SA / CC-BY (design §8).
_GEOGLOWS_S3 = "https://geoglows-v2.s3.us-west-2.amazonaws.com"
_TDX_CATCHMENTS = f"{_GEOGLOWS_S3}/hydrography/vpu={{vpu}}/catchments_{{vpu}}.parquet"
_TDX_STREAMS = f"{_GEOGLOWS_S3}/hydrography/vpu={{vpu}}/streams_{{vpu}}.gpkg"


def _tdx_sources(ds: MirrorDataset, unit: str) -> list[MirrorSource]:
    return [
        MirrorSource(
            url=_TDX_CATCHMENTS.format(vpu=unit),
            archive_name=f"catchments_{unit}.parquet",
            sha256=None,
            size_bytes_approx=200_000_000,
            unit=unit,
            role="catchments",
        ),
        MirrorSource(
            url=_TDX_STREAMS.format(vpu=unit),
            archive_name=f"streams_{unit}.gpkg",
            sha256=None,
            size_bytes_approx=100_000_000,
            unit=unit,
            role="streams",
        ),
    ]


register_unit_source_factory("tdx_hydro", _tdx_sources)
register_mirror_dataset(
    MirrorDataset(
        slug="tdx_hydro",
        version="2",
        display_name="TDX-Hydro / GEOGLOWS v2 — per-VPU catchments + streams",
        description=(
            "GEOGLOWS v2 (TDX-Hydro), ~125 Virtual Processing Units. Catchments "
            "(GeoParquet) + streams (GeoPackage) with streamID / LINKNO / "
            "DSLINKNO topology. Per-VPU lazy only; resolved via the upstream "
            "vpu-boundaries index."
        ),
        sources=[],  # built per-VPU by the factory above
        delivery="path",
        unit_scheme="VPU (~125, via the vpu-boundaries index)",
        unit_processing="raw",  # parquet + gpkg kept byte-identical
        dynamic_units=True,
        units_required_for_sync=True,  # `sync --all` refused (design §1)
        known_columns=["streamID", "LINKNO", "DSLINKNO", "USLINKNO1", "USLINKNO2"],
        license=MirrorLicense(
            license="TDX-Hydro CC-BY-SA-4.0 (© NGA 2023); GEOGLOWS distribution CC-BY-4.0",
            license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            license_verified=True,  # design §8: both notices verified
            attribution=(
                "TDX-Hydro (© NGA 2023, CC-BY-SA 4.0); GEOGLOWS v2 "
                "(CC-BY 4.0, https://data.geoglows.org)"
            ),
            license_flags=["share-alike"],
            requires_acknowledgment=False,
            disclaimer=(
                "TDX-Hydro is CC-BY-SA 4.0: published derivatives must stay "
                "CC-BY-SA. The GEOGLOWS distribution layer is CC-BY 4.0; carry "
                "BOTH notices."
            ),
        ),
        citation=(
            "Sanchez Lozano, J., et al. (2021). A streamflow-based global river "
            "network (GEOGLOWS v2 / TDX-Hydro). https://data.geoglows.org. "
            "Accessed {access_date}."
        ),
        approx_materialized_bytes=40_000_000_000,
        disk_note="~0.5 GB per VPU; ~25-40 GB for the full global set (refused).",
    )
)


# ── NWS NextGen hydrofabric v2.2 ────────────────────────────────────
#
# Single CONUS unit: a 1.6 GB tar.gz expanding to a ~7 GB multi-layer
# GeoPackage. License not stated at the bucket (live-probed); upstream
# NOAA-OWP/Lynker ODbL 1.0 assumed (design §8).
_NWS_URL = (
    "https://communityhydrofabric.s3.us-east-1.amazonaws.com/"
    "hydrofabrics/community/conus_nextgen.tar.gz"
)

register_mirror_dataset(
    MirrorDataset(
        slug="nws_hydrofabric",
        version="2.2",
        display_name="NWS NextGen hydrofabric v2.2 — CONUS (divides/flowpaths/network)",
        description=(
            "NOAA NextGen community hydrofabric, one CONUS-wide GeoPackage "
            "(divides, flowpaths, network layers; divide_id / toid topology). "
            "Path-delivered; the subsetter traces via the network table."
        ),
        sources=[
            MirrorSource(
                url=_NWS_URL,
                archive_name="conus_nextgen.tar.gz",
                sha256=None,
                size_bytes_approx=1_741_084_439,  # live-probed content-length
                unit="conus",
                role="hydrofabric",
            )
        ],
        delivery="path",
        unit_scheme="single CONUS unit",
        unit_processing="tar_member",
        shapefile_patterns=["*conus_nextgen.gpkg", "*.gpkg"],
        known_columns=["divide_id", "id", "toid"],
        license=MirrorLicense(
            license="not stated at source; upstream NOAA-OWP/Lynker ODbL-1.0 assumed",
            license_url="https://www.lynker-spatial.com",
            license_verified=False,  # design §8: live-probed, no license at bucket
            attribution=(
                "NextGen community hydrofabric (NOAA-OWP / Lynker-Spatial); "
                "license not stated at source, upstream ODbL 1.0 assumed"
            ),
            license_flags=["license-unverified-at-source"],
            requires_acknowledgment=False,
            disclaimer=(
                "PROVISIONAL data; license not stated at the distribution "
                "bucket (live-probed). Upstream NOAA-OWP/Lynker ODbL 1.0 is "
                "assumed — confirm before redistribution."
            ),
        ),
        citation=(
            "Johnson, J.M., et al. (2023). National Hydrologic Geospatial Fabric "
            "(hydrofabric) for the NextGen framework. Community hydrofabric "
            "v2.2. Accessed {access_date}."
        ),
        approx_materialized_bytes=7_000_000_000,
        disk_note=(
            "1.6 GB tar.gz download expands to a ~7 GB GeoPackage "
            "(~9 GB transient during extraction). Single CONUS unit."
        ),
    )
)
