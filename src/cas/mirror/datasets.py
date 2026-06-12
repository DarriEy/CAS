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

from cas.core.exceptions import MirrorDatasetNotFoundError
from cas.mirror.models import MirrorDataset, MirrorLicense, MirrorSource

_CC_BY_4 = "https://creativecommons.org/licenses/by/4.0/"

_REGISTRY: dict[str, dict[str, MirrorDataset]] = {}
_DEFAULT_VERSIONS: dict[str, str] = {}


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
