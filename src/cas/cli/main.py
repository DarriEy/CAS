# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""CAS command-line interface."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click
import structlog

structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(),
    ],
)


@click.group()
@click.version_option(package_name="community-attribute-service")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """CAS — Community Attribute Service."""
    ctx.ensure_object(dict)


@cli.command()
@click.option(
    "--geometry", "-g", required=True,
    help="GeoJSON geometry (Point, Polygon, MultiPolygon) or @filepath",
)
@click.option("--datasets", "-d", required=True, multiple=True, help="Dataset ID(s) to query")
@click.option("--start", default=None, help="Start time (ISO 8601) for dynamic datasets")
@click.option("--end", default=None, help="End time (ISO 8601) for dynamic datasets")
@click.option("--aggregation", "-a", default="mean", help="Aggregation method")
@click.option("--output", "-o", default=None, help="Output file path (default: stdout)")
def extract(geometry, datasets, start, end, aggregation, output):
    """Extract attribute values for a geometry from one or more datasets."""
    from cas.core.models import (
        AggregationMethod,
        AttributeRequest,
        BatchAttributeRequest,
        Geometry,
        TimeRange,
    )
    from cas.extract.engine import batch_extract
    from cas.extract.engine import extract as run_extract

    if geometry.startswith("@"):
        with open(geometry[1:]) as f:
            geom_data = json.load(f)
    else:
        geom_data = json.loads(geometry)

    time_range = None
    if start and end:
        from datetime import datetime

        time_range = TimeRange(
            start=datetime.fromisoformat(start),
            end=datetime.fromisoformat(end),
        )

    agg = AggregationMethod(aggregation)
    ds_ids = list(datasets)

    if geom_data.get("type") == "FeatureCollection":
        geometries = [
            Geometry(**f["geometry"]) for f in geom_data["features"]
        ]
        batch_request = BatchAttributeRequest(
            geometries=geometries,
            dataset_ids=ds_ids,
            time_range=time_range,
            aggregation=agg,
        )

        async def _run():
            response = await batch_extract(batch_request)
            result_json = response.model_dump_json(indent=2)
            if output:
                with open(output, "w") as f:
                    f.write(result_json)
                click.echo(f"Results written to {output}")
            else:
                click.echo(result_json)

        asyncio.run(_run())
        return

    if "geometry" in geom_data:
        geom_data = geom_data["geometry"]

    request = AttributeRequest(
        geometry=Geometry(**geom_data),
        dataset_ids=ds_ids,
        time_range=time_range,
        aggregation=agg,
    )

    async def _run_single():
        response = await run_extract(request)
        result_json = response.model_dump_json(indent=2)
        if output:
            with open(output, "w") as f:
                f.write(result_json)
            click.echo(f"Results written to {output}")
        else:
            click.echo(result_json)

    asyncio.run(_run_single())


@cli.command()
@click.option("--support", "show_support", is_flag=True, help="Show evidence-backed support tiers")
@click.option("--baseline", default="health/baseline.json", help="Health baseline used with --support")
def providers(show_support, baseline):
    """List registered providers."""
    from cas.core.registry import discover, list_providers

    if show_support:
        from cas.monitor.support import build_support_report

        report = build_support_report(baseline)
        for provider in report["providers"]:
            click.echo(f"  {provider['provider']:38s} {provider['tier']}")
        return

    discover()
    for slug in list_providers():
        click.echo(f"  {slug}")


@cli.command()
@click.option("--provider", "-p", default=None, help="Filter by provider slug")
def datasets(provider):
    """List available datasets."""
    from cas.core.registry import discover, get_connector, list_providers

    discover()
    slugs = [provider] if provider else list_providers()

    async def _run():
        for slug in slugs:
            try:
                connector_cls = get_connector(slug)
                async with connector_cls() as conn:
                    ds_list = await conn.list_datasets()
                    click.echo(f"\n{conn.display_name} ({slug}):")
                    for ds in ds_list:
                        vars_str = ", ".join(v.name for v in ds.variables)
                        click.echo(
                            f"  {ds.id:45s}  {ds.resolution_m:>6.0f}m  "
                            f"[{vars_str}]  {ds.temporal.temporal_type}"
                        )
            except Exception as e:
                click.echo(f"\n{slug}: ERROR — {e}", err=True)

    asyncio.run(_run())


@cli.command(name="export-support")
@click.option("--baseline", default="health/baseline.json", help="Committed health baseline")
@click.option("--output", "-o", default="inventory/support.json", help="Output JSON path")
def export_support(baseline, output):
    """Publish evidence-backed provider support tiers."""
    from pathlib import Path

    from cas.monitor.support import build_support_report

    report = build_support_report(baseline)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n")
    click.echo(f"Wrote {report['summary']['total']} provider tiers to {target}")


@cli.command(name="export-inventory")
@click.option("--output", "-o", default="inventory/providers.yaml", help="Output file path")
def export_inventory(output):
    """Generate providers.yaml from the live connector registry."""
    from cas.core.registry import discover, get_connector, list_providers

    discover()
    slugs = list_providers()

    async def _run():
        entries = []
        for slug in slugs:
            cls = get_connector(slug)
            try:
                async with cls() as conn:
                    datasets = await conn.list_datasets()
            except Exception:
                datasets = []

            ds = datasets[0] if datasets else None
            entry: dict[str, object] = {
                "slug": slug,
                "name": cls.display_name,
                "protocol": cls.protocol,
                "base_url": cls.base_url,
            }
            if ds:
                entry["resolution_m"] = ds.resolution_m
                entry["bbox"] = [ds.bbox.min_lon, ds.bbox.min_lat, ds.bbox.max_lon, ds.bbox.max_lat]
                entry["temporal_type"] = str(ds.temporal.temporal_type)
                entry["license"] = ds.license or "Open"
                entry["citation"] = ds.citation or ""
                entry["datasets"] = len(datasets)
                entry["variables"] = [v.name for v in ds.variables]
            entry["status"] = "implemented"
            entries.append(entry)

        lines = [
            "# CAS Provider Inventory — auto-generated by `cas export-inventory`",
            f"# Total providers: {len(entries)}",
            "",
        ]
        for e in entries:
            lines.append(f"- slug: {e['slug']}")
            lines.append(f"  name: {e['name']}")
            lines.append(f"  protocol: {e['protocol']}")
            lines.append(f"  base_url: {e['base_url']}")
            if "resolution_m" in e:
                lines.append(f"  resolution_m: {e['resolution_m']}")
                lines.append(f"  bbox: {e['bbox']}")
                lines.append(f"  temporal_type: {e['temporal_type']}")
                lines.append(f'  license: "{e["license"]}"')
                lines.append(f'  citation: "{e["citation"]}"')
                lines.append(f"  datasets: {e['datasets']}")
                lines.append(f"  variables: {e['variables']}")
            lines.append(f"  status: {e['status']}")
            lines.append("")

        text = "\n".join(lines)
        with open(output, "w") as f:
            f.write(text)
        click.echo(f"Wrote {len(entries)} providers to {output}")

    asyncio.run(_run())


@cli.command()
@click.option("--slug", "-s", default=None, help="Check a single provider by slug")
@click.option("--max-failures", type=int, default=0,
              help="Tolerate up to N still-failing endpoints before exiting non-zero. "
                   "A backstop for the odd endpoint that stays slow even on the serial "
                   "re-probe; a real multi-endpoint outage still exceeds it. Default: 0.")
def verify(slug, max_failures):
    """Fast endpoint reachability check for all providers (~20s)."""
    from cas.monitor.reachability import check_all_reachability

    async def _run():
        slugs = [slug] if slug else None
        results = await check_all_reachability(slugs=slugs)

        ok = auth = skip = fail = 0
        failures = []
        for r in sorted(results, key=lambda r: r.slug):
            if r.skipped:
                icon, skip = "SKIP", skip + 1
            elif r.reachable and r.status_code in (401, 403):
                icon, auth = "AUTH", auth + 1
            elif r.reachable:
                icon, ok = "OK  ", ok + 1
            else:
                icon, fail = "FAIL", fail + 1
                failures.append(r)
            click.echo(
                f"  [{icon}]  {r.slug:30s}  {r.elapsed_s:5.1f}s  {r.detail}"
            )

        total = ok + auth + skip + fail
        click.echo(f"\n  {total} providers: {ok} ok, {auth} auth-gated, "
                    f"{skip} skipped, {fail} failed")

        if failures:
            click.echo("\nFailed endpoints:", err=True)
            for r in failures:
                click.echo(f"  {r.slug}: {r.url}  ({r.detail})", err=True)
            if fail > max_failures:
                raise SystemExit(1)
            click.echo(
                f"\n  {fail} failure(s) within tolerance (--max-failures {max_failures}); "
                "not failing.", err=True,
            )

    asyncio.run(_run())


@cli.command()
@click.option("--slug", "-s", default=None, help="Check a single provider by slug")
@click.option("--strict", is_flag=True, help="Exit non-zero if any provider is down")
@click.option("--json", "json_path", default=None,
              help="Write a machine-readable snapshot to PATH (use '-' for stdout)")
@click.option("--concurrency", type=int, default=None,
              help="Max in-flight checks across the sweep. Lower it (e.g. 6) on a "
                   "shared CI runner so heavy providers don't read as down under "
                   "egress saturation. Default: the sweep's own bound (12).")
def health(slug, strict, json_path, concurrency):
    """End-to-end extraction health check (real extract per provider)."""
    from cas.monitor.health import check_all_providers
    from cas.monitor.trend import snapshot_dict

    async def _run():
        slugs = [slug] if slug else None
        kwargs = {} if concurrency is None else {"concurrency": concurrency}
        results = await check_all_providers(slugs=slugs, **kwargs)

        if json_path:
            snapshot = json.dumps(snapshot_dict(results), indent=2)
            if json_path == "-":
                click.echo(snapshot)
            else:
                with open(json_path, "w") as f:
                    f.write(snapshot + "\n")
                click.echo(f"  Snapshot written to {json_path}", err=True)

        healthy = degraded = down = auth = unknown = 0
        failures = []
        for r in sorted(results, key=lambda r: r.provider):
            status_icon = {
                "healthy": "OK  ",
                "degraded": "WARN",
                "down": "FAIL",
                "auth_gated": "AUTH",
                "unknown": "UNKN",
            }.get(r.status, "UNKN")
            if r.status == "healthy":
                healthy += 1
            elif r.status == "degraded":
                degraded += 1
            elif r.status == "auth_gated":
                auth += 1
            elif r.status == "down":
                down += 1
                failures.append(r)
            else:
                unknown += 1
            line = (
                f"  [{status_icon}] {r.provider:30s}  "
                f"{r.response_time_ms or 0:>6d}ms  "
                f"{r.datasets_available} datasets"
            )
            if r.error:
                line += f"  ERROR: {r.error}"
            click.echo(line)

        total = healthy + degraded + down + auth + unknown
        click.echo(f"\n  {total} providers: {healthy} healthy, "
                    f"{degraded} degraded, {auth} auth-gated, {down} down"
                    + (f", {unknown} unknown" if unknown else ""))

        if strict and failures:
            click.echo("\nDown providers:", err=True)
            for r in failures:
                click.echo(f"  {r.provider}: {r.error}", err=True)
            raise SystemExit(1)

    asyncio.run(_run())


@cli.command(name="health-compare")
@click.argument("baseline", type=click.Path(exists=True))
@click.argument("current", type=click.Path(exists=True))
@click.option("--previous", type=click.Path(exists=True), default=None,
              help="Previous run's snapshot. When set, only regressions present in BOTH "
                   "the current and previous run (persistent) drive the exit code — "
                   "transient single-run churn is reported but does not fail.")
@click.option("--fail-on-regression/--no-fail-on-regression", default=True,
              help="Exit non-zero on a (persistent, if --previous) regression (default: on)")
def health_compare(baseline, current, previous, fail_on_regression):
    """Compare a CURRENT health snapshot against a BASELINE snapshot.

    Reports providers that regressed (status worse than baseline), improved,
    or arrived broken. Exits 1 on regressions unless --no-fail-on-regression.

    With --previous, the exit code is gated on *persistent* regressions only:
    a provider must be broken in both the current and the previous run to fail
    the check. This filters out transient upstream/runner blips, which rarely
    recur on the same provider across consecutive runs.
    """
    from cas.monitor.trend import compare, format_report, persistent_regressions

    with open(baseline) as f:
        base_snap = json.load(f)
    with open(current) as f:
        cur_snap = json.load(f)

    cmp = compare(base_snap, cur_snap)
    click.echo(format_report(cmp))

    if previous is not None:
        with open(previous) as f:
            prev_snap = json.load(f)
        persistent = persistent_regressions(base_snap, cur_snap, prev_snap)
        click.echo("")
        if persistent:
            click.echo(f"Persistent regressions ({len(persistent)}) — broken this run AND the previous run:")
            click.echo("\n".join(f"  ✗✗ {c}" for c in persistent))
        else:
            click.echo("No persistent regressions — any regressions above appeared in only one run (transient).")
        should_fail = bool(persistent)
    else:
        should_fail = cmp.has_regressions

    if fail_on_regression and should_fail:
        raise SystemExit(1)


# ── Curated mirror tier ─────────────────────────────────────────────


def _human_bytes(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


@cli.group()
def mirror():
    """Curated local mirrors of bulk-download-only vector datasets.

    Version-pinned, checksummed copies materialized on YOUR disk (under
    CAS_MIRROR_DIR, default ~/.cas/mirror). CAS is only ever a download
    client + local subsetter — it never hosts or redistributes mirror data,
    and the HTTP API serves none of it.
    """


def _stdin_is_interactive() -> bool:
    """Whether we can prompt the user (overridable in tests)."""
    import sys

    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _ack_or_fail(ds, accept_licenses: bool) -> tuple[bool, str | None]:
    """Resolve the license-acknowledgment gate for an explicit sync.

    Returns ``(accepted, via)``. Interactive sessions are prompted (terms
    surfaced, never silently accepted); non-interactive contexts need
    --accept-licenses or CAS_MIRROR_ACCEPT_LICENSES.
    """
    from cas.core.config import get_settings

    lic = ds.license
    if not lic.requires_acknowledgment:
        return False, None
    if accept_licenses:
        return True, "flag"
    if ds.slug in get_settings().mirror_accept_licenses:
        return True, "env"
    if _stdin_is_interactive():
        click.echo(f"\n{ds.display_name} ({ds.spec}) requires license acknowledgment:")
        click.echo(f"  License:     {lic.license}")
        click.echo(f"  Full text:   {lic.license_url}")
        click.echo(f"  Attribution: {lic.attribution}")
        if click.confirm("Accept these license terms?", default=False):
            return True, "interactive"
        raise click.ClickException(f"License terms for {ds.spec} not accepted; not syncing.")
    raise click.ClickException(
        f"{ds.spec} requires license acknowledgment and this session is "
        f"non-interactive. Re-run with --accept-licenses or set "
        f"CAS_MIRROR_ACCEPT_LICENSES={ds.slug}."
    )


@mirror.command("sync")
@click.argument("datasets", nargs=-1, required=True)
@click.option("--accept-licenses", is_flag=True,
              help="Accept license terms of acknowledgment-requiring datasets "
                   "(recorded in the manifest with a timestamp).")
def mirror_sync(datasets, accept_licenses):
    """Materialize DATASETS (e.g. ``wokam``, ``glhymps==2.0``, ``rgi7:11``).

    Unit-structured datasets take a ``slug:unit`` form (RGI region
    ``rgi7:11``, HydroBASINS ``hydrobasins:na_lev06``, TDX VPU
    ``tdx_hydro:714``); without a unit, all declared units are synced —
    except datasets whose full sync is refused by design (TDX-Hydro).

    The same code path as lazy first-use materialization — run it on an HPC
    login node to pre-stage data for offline compute nodes.
    """
    from cas.core.exceptions import MirrorError
    from cas.mirror import (
        ensure_materialized,
        ensure_units_materialized,
        get_mirror_dataset,
        load_manifest,
        parse_unit_spec,
        sources_for_unit,
    )
    from cas.mirror.store import resolve_unit_ids

    failures = 0
    for spec in datasets:
        try:
            base_spec, unit = parse_unit_spec(spec)
            ds = get_mirror_dataset(base_spec)
            accepted, via = _ack_or_fail(ds, accept_licenses)
            if ds.disk_note:
                click.echo(f"  Note: {ds.disk_note}")
            if ds.is_unit_structured:
                units = [unit] if unit else None
                resolved = resolve_unit_ids(ds, units, explicit=True)
                approx = sum(
                    s.size_bytes_approx for u in resolved for s in sources_for_unit(ds, u)
                )
                click.echo(
                    f"Syncing {ds.spec} unit(s) {', '.join(resolved)} "
                    f"(~{_human_bytes(approx)} download) ..."
                )
                paths = ensure_units_materialized(
                    ds, resolved, explicit=True,
                    licenses_accepted=accepted, ack_via=via,
                )
                for u in resolved:
                    listing = ", ".join(
                        f"{role}: {p.name}" for role, p in sorted(paths[u].items())
                    )
                    click.echo(f"  OK  {ds.slug}:{u}  {listing}")
            else:
                if unit:
                    raise MirrorError(
                        f"'{ds.spec}' is a single global dataset; it has no "
                        f"unit '{unit}'."
                    )
                click.echo(f"Syncing {ds.spec} from {ds.sources[0].url} "
                           f"(~{_human_bytes(ds.sources[0].size_bytes_approx)}) ...")
                path = ensure_materialized(
                    ds, explicit=True, licenses_accepted=accepted, ack_via=via,
                )
                manifest = load_manifest(ds)
                features = manifest.feature_count if manifest else "?"
                click.echo(f"  OK  {ds.spec}: {features} features, "
                           f"{_human_bytes(path.stat().st_size)} at {path}")
        except MirrorError as e:
            failures += 1
            click.echo(f"  FAIL  {spec}: {e}", err=True)
    if failures:
        raise SystemExit(1)


@mirror.command("import")
@click.argument("dataset")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--unit", "unit", default=None,
              help="Regional unit being imported (required for unit-structured "
                   "datasets, e.g. --unit 7 for a MERIT Pfaf region).")
@click.option("--accept-licenses", is_flag=True,
              help="Accept license terms of acknowledgment-requiring datasets "
                   "(recorded in the manifest with a timestamp).")
def mirror_import(dataset, source, unit, accept_licenses):
    """Stage a manually obtained archive as DATASET (the escape hatch for
    Globus-only or registration-gated distributions).

    SOURCE is the archive file, or a directory holding every expected archive
    under its exact registry name. CAS verifies the content against the
    registry (member names/format; pinned sha256 when one exists), records
    the checksum as tofu-import with manual-import provenance + the local
    path, then runs the SAME extraction/conversion/manifest pipeline as
    ``cas mirror sync``. Examples:

    \b
      cas mirror import merit_basins pfaf_7_MERIT_Hydro_v07_Basins_v01.zip --unit 7
      cas mirror import merit_basins:7 ~/globus-staging/   (unit via spec)
      cas mirror import glhymps==2.0 GLHYMPS.zip
    """
    from cas.core.exceptions import MirrorError
    from cas.mirror import get_mirror_dataset, mirror_import_sync, parse_unit_spec

    base_spec, spec_unit = parse_unit_spec(dataset)
    if spec_unit and unit and spec_unit != unit:
        raise click.ClickException(
            f"Conflicting units: '{dataset}' names unit {spec_unit} but "
            f"--unit says {unit}."
        )
    unit = unit or spec_unit
    try:
        ds = get_mirror_dataset(base_spec)
        accepted, via = _ack_or_fail(ds, accept_licenses)
        result = mirror_import_sync(
            base_spec, source, unit=unit,
            licenses_accepted=accepted, ack_via=via,
        )
    except MirrorError as e:
        raise click.ClickException(str(e)) from e
    unit_note = f" unit {result.unit}" if result.unit else ""
    click.echo(f"  OK  {result.dataset_id}{unit_note} imported from {result.imported_from}")
    for role, p in sorted(result.paths.items()):
        click.echo(f"      {role}: {p}")
    for a in result.archives:
        click.echo(f"      sha256 {a.sha256} ({a.sha256_source})")


@mirror.command("status")
def mirror_status():
    """Per-dataset disk use, version, license, and checksum state."""
    from cas.core.config import get_settings
    from cas.mirror import status as mirror_status_fn

    rows = mirror_status_fn()
    click.echo(f"Mirror root: {get_settings().mirror_dir}\n")
    total = 0
    for r in rows:
        if r.units_materialized:
            denom = f"/{r.units_total}" if r.units_total else ""
            state = f"{len(r.units_materialized)}{denom} units"
        elif r.units_total or r.delivery == "path":
            state = "not synced"
        else:
            state = "synced" if r.materialized else "not synced"
        flags = f" [{', '.join(r.license_flags)}]" if r.license_flags else ""
        click.echo(
            f"  {r.slug + '==' + r.version:24s} {state:13s} "
            f"{_human_bytes(r.disk_bytes):>10s}  {r.checksum_state:18s} "
            f"{r.license}{flags}"
        )
        if r.disk_note:
            click.echo(f"  {'':24s} note: {r.disk_note}")
        total += r.disk_bytes
    click.echo(f"\n  Total disk use: {_human_bytes(total)}")


@mirror.command("verify")
@click.argument("datasets", nargs=-1)
def mirror_verify(datasets):
    """Full sha256 re-checksum of materialized files against manifests."""
    from cas.mirror import get_mirror_dataset, is_materialized, list_mirror_datasets
    from cas.mirror import verify as verify_fn

    if datasets:
        targets = [get_mirror_dataset(s) for s in datasets]
    else:
        targets = [ds for ds in list_mirror_datasets(all_versions=True) if is_materialized(ds)]
        if not targets:
            click.echo("Nothing materialized to verify.")
            return

    all_problems = []
    for ds in targets:
        problems = verify_fn(ds)
        if problems:
            all_problems.extend(problems)
            for p in problems:
                click.echo(f"  FAIL  {p}", err=True)
        else:
            click.echo(f"  OK    {ds.spec}")
    if all_problems:
        raise SystemExit(1)


@mirror.command("remove")
@click.argument("datasets", nargs=-1, required=True)
def mirror_remove(datasets):
    """Delete materialized dataset version(s) to reclaim disk."""
    from cas.mirror import get_mirror_dataset
    from cas.mirror import remove as remove_fn

    for spec in datasets:
        ds = get_mirror_dataset(spec)
        if remove_fn(ds):
            click.echo(f"  Removed {ds.spec}")
        else:
            click.echo(f"  {ds.spec} was not materialized; nothing to remove")


if __name__ == "__main__":
    cli()
