# Contributing to CAS

Thank you for considering a contribution to CAS (Community Attribute Service).
The most valuable contribution is usually a **new provider connector** — see
[Adding a Provider](#adding-a-provider) below. Bug reports, documentation
fixes, and QC improvements are equally welcome.

## Development setup

```bash
git clone https://github.com/DarriEy/CAS.git
cd CAS
pip install -e ".[dev]"          # core + test/lint tooling
# Optional extras as needed:
pip install -e ".[dev,api,stac,climate]"
```

## Running the tests

The default test suite is fully offline (network calls are mocked):

```bash
pytest -m "not network" -q
```

Tests marked `network` run real extractions against live upstream providers
and are excluded by default:

```bash
pytest tests/test_e2e_extract.py -m network -k my_provider
```

## Linting and type checking

```bash
ruff check src/ tests/
mypy src/cas/ --ignore-missing-imports
```

CI runs all three (ruff, mypy, offline pytest with coverage) on Python
3.11–3.13; please make sure they pass locally before opening a PR.

## Adding a Provider

1. Create `src/cas/connectors/my_provider.py`
2. Subclass `BaseConnector`, implement `list_datasets()` and `extract()`
3. Decorate with `@register("my_provider")`
4. Regenerate the inventory: `cas export-inventory` (updates `inventory/providers.yaml`)
5. Create `tests/connectors/test_my_provider.py` with mocked (offline) tests

```python
@register("my_provider")
class MyProviderConnector(WCSMixin, BaseConnector):
    slug = "my_provider"
    display_name = "My Provider"
    base_url = "https://api.example.com"
    protocol = "wcs"

    async def list_datasets(self) -> list[Dataset]:
        ...

    async def extract(self, dataset_id, geometry, time_range=None) -> AttributeResult:
        ...
```

Protocol mixins (WCS, STAC+COG, OPeNDAP, Zarr) in `src/cas/connectors/protocols/`
handle most of the transport work — compose them via multiple inheritance.

For providers requiring registration, raise `RegistrationRequiredError` with
clear instructions:

```python
from cas.core.exceptions import RegistrationRequiredError

class MyGatedConnector(BaseConnector):
    def _get_credentials(self):
        key = os.environ.get("CAS_MY_PROVIDER_KEY", "")
        if not key:
            raise RegistrationRequiredError(
                self.slug,
                "https://provider.example.com/register",
                "Register for a free API key, then:\n  export CAS_MY_PROVIDER_KEY=your_key",
            )
        return key
```

Suggesting a provider without implementing it is also useful — open an issue
with the "New provider request" template.

## Pull request expectations

- One logical change per PR; keep diffs reviewable.
- Include offline tests for new code (mock network calls with `respx`).
- New providers must pass an end-to-end extraction
  (`pytest tests/test_e2e_extract.py -m network -k my_provider` or
  `cas health -s my_provider`) at submission time.
- Update `inventory/providers.yaml` (via `cas export-inventory`) and any
  affected documentation.
- `ruff check`, `mypy`, and `pytest -m "not network"` must pass.
- Imperative-mood commit messages ("Add X", "Fix Y").

By contributing you agree that your contributions are licensed under the MIT
License, and you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
