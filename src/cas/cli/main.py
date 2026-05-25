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

    async def _run():
        response = await run_extract(request)
        result_json = response.model_dump_json(indent=2)
        if output:
            with open(output, "w") as f:
                f.write(result_json)
            click.echo(f"Results written to {output}")
        else:
            click.echo(result_json)

    asyncio.run(_run())


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


@cli.command()
@click.option("--polygon", default="central_us", help="Test polygon name")
def health(polygon):
    """Run health checks against all providers."""
    from cas.monitor.health import check_all_providers

    async def _run():
        results = await check_all_providers(test_polygon_name=polygon)
        for r in results:
            status_icon = {
                "healthy": "OK",
                "degraded": "WARN",
                "down": "FAIL",
                "unknown": "??",
            }[r.status]
            line = (
                f"  [{status_icon:4s}] {r.provider:25s}  "
                f"{r.response_time_ms or 0:>5d}ms  "
                f"{r.datasets_available} datasets"
            )
            if r.error:
                line += f"  ERROR: {r.error}"
            click.echo(line)

    asyncio.run(_run())
