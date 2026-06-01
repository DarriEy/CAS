# SPDX-License-Identifier: MIT
# Copyright (C) 2024-2026 CAS Contributors
"""CAS command-line interface."""

from __future__ import annotations

import asyncio
import json

import click
import structlog

structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(),
    ],
)


@click.group()
@click.version_option(package_name="cas")
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
def providers():
    """List registered providers."""
    from cas.core.registry import discover, list_providers

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
def verify(slug):
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
            raise SystemExit(1)

    asyncio.run(_run())


@cli.command()
@click.option("--slug", "-s", default=None, help="Check a single provider by slug")
@click.option("--strict", is_flag=True, help="Exit non-zero if any provider is down")
@click.option("--json", "json_path", default=None,
              help="Write a machine-readable snapshot to PATH (use '-' for stdout)")
def health(slug, strict, json_path):
    """End-to-end extraction health check (real extract per provider)."""
    from cas.monitor.health import check_all_providers
    from cas.monitor.trend import snapshot_dict

    async def _run():
        slugs = [slug] if slug else None
        results = await check_all_providers(slugs=slugs)

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
@click.option("--fail-on-regression/--no-fail-on-regression", default=True,
              help="Exit non-zero if any provider regressed vs baseline (default: on)")
def health_compare(baseline, current, fail_on_regression):
    """Compare a CURRENT health snapshot against a BASELINE snapshot.

    Reports providers that regressed (status worse than baseline), improved,
    or arrived broken. Exits 1 on regressions unless --no-fail-on-regression.
    """
    from cas.monitor.trend import compare, format_report

    with open(baseline) as f:
        base_snap = json.load(f)
    with open(current) as f:
        cur_snap = json.load(f)

    cmp = compare(base_snap, cur_snap)
    click.echo(format_report(cmp))

    if fail_on_regression and cmp.has_regressions:
        raise SystemExit(1)
