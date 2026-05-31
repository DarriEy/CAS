# Architecture

```
Geometry in → CAS engine → fan out to providers → server-side subset → zonal stats → QC → results out
```

CAS is a **passthrough** service: it stores no data and holds no database. Every
request is satisfied live from the upstream provider, validated, harmonized, and
returned.

## Layers

- **Connectors** (`cas.connectors`) — one self-contained module per provider.
  Each subclasses `BaseConnector`, implements `list_datasets()` and `extract()`,
  and self-registers with the `@register("slug")` decorator.
- **Protocol mixins** — WCS, STAC+COG, OPeNDAP behaviours compose into a
  connector via multiple inheritance, so a new provider reuses transport,
  parsing, and windowed-read logic.
- **Registry** (`cas.core.registry`) — `discover()` imports every connector
  module to trigger registration; `list_providers()` / `get_connector(slug)`
  enumerate and resolve them at runtime.
- **Engine** (`cas.extract`) — fans a request out across the requested datasets,
  enforces per-provider and whole-request deadlines, computes zonal statistics
  (continuous: mean/median/min/max/std; categorical: majority/distribution),
  runs QC, and assembles the response. Results are cached in-memory.
- **API** (`cas.api`) — a FastAPI app (`create_app()`), with middleware for the
  request-ID + error envelope, optional API-key auth and rate limiting, and
  Prometheus metrics.
- **SDK** (`cas.client`) — a typed HTTP client returning the same models.

## Quality control

- **Range checks** against each variable's declared valid range.
- **Coverage thresholds** — a result over a polygon with little real data is
  flagged (`partial` / `degraded`).
- **Cross-provider consistency** — when multiple providers answer the same
  request (e.g. several DEMs), divergence surfaces as a warning.

## Health monitoring

CAS ships an end-to-end health system. `cas health` runs a real `extract()` per
provider over a **coverage-aware** test polygon — a small area chosen inside the
provider's own declared bbox, so country-specific connectors are exercised over
data they actually serve rather than a single fixed global point. Snapshots are
compared against a committed `health/baseline.json` so only genuine regressions
alert; the daily CI workflow archives history and opens an issue on regression.

## Adding a provider

1. Create `src/cas/connectors/my_provider.py`.
2. Subclass `BaseConnector` (+ a protocol mixin), implement `list_datasets()`
   and `extract()`.
3. Decorate the class with `@register("my_provider")`.
4. Run `cas export-inventory` to refresh the catalog.
5. Add `tests/connectors/test_my_provider.py`.

```python
@register("my_provider")
class MyProviderConnector(WCSMixin, BaseConnector):
    slug = "my_provider"
    display_name = "My Provider"
    base_url = "https://api.example.com"
    protocol = "wcs"

    async def list_datasets(self) -> list[Dataset]: ...

    async def extract(self, dataset_id, geometry, time_range=None) -> AttributeResult: ...
```
