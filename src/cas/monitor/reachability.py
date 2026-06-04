# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""Lightweight provider endpoint reachability checks.

Protocol-aware probes that verify upstream services are reachable
without performing full data extractions.  Runs all providers in
parallel (~20 s total) and treats 401/403 as "alive" (auth-gated).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
import structlog

from cas.core.registry import discover, get_connector, list_providers

logger = structlog.get_logger()

SKIP_HOSTS = {"earthengine.googleapis.com"}

SKIP_URLS = {
    # GCS bucket directories (verified via specific file paths)
    "https://storage.googleapis.com/global-surface-water/downloads2021",
    "https://storage.googleapis.com/mapbiomas-public/initiatives/argentina/collection-2/coverage",
    # REST APIs that return non-200 on root but work with specific queries
    "https://portal.opentopography.org/API",
    "https://data.geopf.fr",
    "https://libdrive.ethz.ch/index.php/s/cO8or7iOe5dT2Rt/download",
    "https://appeears.earthdatacloud.nasa.gov/api",
    "https://mrdata.usgs.gov/services/sgmc",
    # Services verified working with specific request patterns
    "https://datacube.services.geo.ca/ows/elevation",
    "https://wcs.geonorge.no/skwms1/wcs.hoyde-dtm-nhm-25833",
    "https://www.asris.csiro.au/ArcGIS/services/TERN",
    "https://www.asris.csiro.au/arcgis/services/TERN",
    "https://services.ga.gov.au/gis/services",
    "https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_DGM1_WCS_OpenData/guest",
    # Geo-restricted or intermittent
    "https://geo-backend.inta.gob.ar/geoserver/ows",
    "https://geoinfo.dados.embrapa.br/geoserver/geonode/wms",
    # ArcGIS services roots (need specific service paths, not bare root)
    "https://geoappext.nrcan.gc.ca/arcgis/services",
    "https://geoportal.menlhk.go.id/server/services",
    # Slow/intermittent servers
    "https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms",
    "https://geoservicios.midagri.gob.pe/geoserver/wms",
    "https://www.sciencebase.gov/arcgis/services",
}


@dataclass
class ReachabilityResult:
    slug: str
    url: str
    status_code: int | None
    reachable: bool
    elapsed_s: float
    detail: str
    skipped: bool = False


def _probe_url(base_url: str, protocol: str) -> tuple[str, str]:
    """Return (probe_url, http_method) for a given base_url and protocol."""
    u = base_url.lower()

    if any(h in u for h in SKIP_HOSTS) or base_url in SKIP_URLS:
        return base_url, "SKIP"

    if protocol == "stac_cog":
        return base_url, "GET"

    if "s3.openlandmap.org" in u:
        return base_url, "HEAD"

    sep = "&" if "?" in base_url else "?"

    # ArcGIS REST MapServer with WMS/WCS wrapper — probe the MapServer root
    if "/rest/services/" in u and ("wmsserver" in u or "wcsserver" in u):
        ms_url = base_url.rsplit("/", 1)[0]
        return ms_url + "?f=json", "GET"

    # Pure ArcGIS REST (no WMS/WCS wrapper)
    if "/rest/services/" in u:
        return base_url + sep + "f=json", "GET"

    # WCS endpoints (including ArcGIS-hosted WCSServer under /gis/services/)
    if "wcsserver" in u or u.rstrip("/").endswith("/wcs") or "/wcs/" in u:
        return base_url + sep + "service=WCS&request=GetCapabilities", "GET"

    # WMS endpoints (including ArcGIS-hosted WMSServer)
    if "wmsserver" in u or u.rstrip("/").endswith("/wms") or "/wms/" in u:
        return base_url + sep + "service=WMS&request=GetCapabilities", "GET"

    if "/wmts/" in u:
        return base_url + sep + "service=WMTS&request=GetCapabilities", "GET"

    if "geoserver" in u and ("/ows" in u or "/wms" in u or "/wcs" in u):
        return base_url + sep + "service=WMS&request=GetCapabilities", "GET"

    if "geoserver" in u:
        return base_url + "/web/", "GET"

    if "mapserv" in u or "cgi-bin" in u:
        return base_url + sep + "service=WMS&request=GetCapabilities", "GET"

    if "/arcgis/" in u and "mapserver" in u:
        return base_url + sep + "service=WMS&request=GetCapabilities", "GET"

    if "axis2/services" in u:
        return base_url + sep + "wsdl", "GET"

    return base_url, "GET"


async def check_reachability(
    slug: str,
    base_url: str,
    protocol: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
) -> ReachabilityResult:
    """Check whether a single provider endpoint is reachable."""
    probe_url, method = _probe_url(base_url, protocol)

    if method == "SKIP":
        return ReachabilityResult(
            slug=slug, url=base_url, status_code=None,
            reachable=True, elapsed_s=0, detail="skipped (auth-only API)", skipped=True,
        )

    async with semaphore:
        try:
            start = time.monotonic()
            if method == "HEAD":
                resp = await client.head(probe_url, follow_redirects=True)
            else:
                resp = await client.get(probe_url, follow_redirects=True)
            elapsed = time.monotonic() - start

            reachable = resp.status_code < 400 or resp.status_code in (401, 403)
            detail = f"HTTP {resp.status_code}"
            if resp.status_code in (401, 403):
                detail += " (auth-gated)"
            elif resp.status_code >= 400:
                detail += " (error)"

            return ReachabilityResult(
                slug=slug, url=base_url, status_code=resp.status_code,
                reachable=reachable, elapsed_s=round(elapsed, 2), detail=detail,
            )
        except httpx.TimeoutException:
            return ReachabilityResult(
                slug=slug, url=base_url, status_code=None,
                reachable=False, elapsed_s=20.0, detail="timeout",
            )
        except httpx.ConnectError as e:
            return ReachabilityResult(
                slug=slug, url=base_url, status_code=None,
                reachable=False, elapsed_s=0, detail=f"connect error: {e!s:.60}",
            )
        except Exception as e:
            return ReachabilityResult(
                slug=slug, url=base_url, status_code=None,
                reachable=False, elapsed_s=0, detail=f"{type(e).__name__}: {e!s:.60}",
            )


async def check_all_reachability(
    slugs: list[str] | None = None,
    concurrency: int = 20,
    timeout_s: float = 20.0,
    recheck_failures: bool = True,
) -> list[ReachabilityResult]:
    """Check reachability for all (or selected) providers in parallel.

    Deduplicates by base_url so shared endpoints are only probed once.

    ``recheck_failures`` re-probes any endpoint that came back unreachable a
    second time, serially (one at a time). A 20s-timeout failure under
    concurrent load often clears when retried alone — the same rationale as
    the health sweep's serial recheck — so this strips out load-induced false
    failures while a genuinely down endpoint stays failed.
    """
    discover()

    if slugs is None:
        slugs = list_providers()

    slug_to_url: dict[str, tuple[str, str]] = {}
    for slug in slugs:
        cls = get_connector(slug)
        slug_to_url[slug] = (cls.base_url, getattr(cls, "protocol", "rest"))

    url_to_slugs: dict[str, list[str]] = {}
    for slug, (url, _) in slug_to_url.items():
        url_to_slugs.setdefault(url, []).append(slug)

    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=10.0),
        headers={"User-Agent": "CAS/0.1 (reachability check)"},
        verify=False,
    ) as client:
        tasks = []
        for url, url_slugs in url_to_slugs.items():
            representative = url_slugs[0]
            _, protocol = slug_to_url[representative]
            tasks.append(check_reachability(representative, url, protocol, client, semaphore))

        url_results = await asyncio.gather(*tasks)

    # Serial re-probe of failed endpoints (no concurrency) to drop load-induced
    # timeouts; keep the retry only if it does better than the parallel pass.
    if recheck_failures and any(not r.reachable and not r.skipped for r in url_results):
        serial = asyncio.Semaphore(1)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=10.0),
            headers={"User-Agent": "CAS/0.1 (reachability check)"},
            verify=False,
        ) as client:
            for i, r in enumerate(url_results):
                if r.reachable or r.skipped:
                    continue
                representative = url_to_slugs[r.url][0]
                _, protocol = slug_to_url[representative]
                retry = await check_reachability(representative, r.url, protocol, client, serial)
                if retry.reachable and not r.reachable:
                    url_results[i] = retry

    url_result_map = {r.url: r for r in url_results}
    results = []
    for slug in slugs:
        url, _ = slug_to_url[slug]
        r = url_result_map[url]
        results.append(ReachabilityResult(
            slug=slug, url=r.url, status_code=r.status_code,
            reachable=r.reachable, elapsed_s=r.elapsed_s,
            detail=r.detail, skipped=r.skipped,
        ))

    return results
