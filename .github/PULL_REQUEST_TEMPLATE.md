## What does this PR do?

Brief description of the change and why it's needed. Link related issues
(`Closes #123`).

## Checklist

- [ ] `ruff check src/ tests/` passes
- [ ] `mypy src/cas/ --ignore-missing-imports` passes
- [ ] `pytest -m "not network" -q` passes (new code has offline tests)
- [ ] Docs updated if behavior changed

### New provider connectors only

- [ ] Offline tests in `tests/connectors/` (network mocked with `respx`)
- [ ] Live end-to-end extraction passes (`cas health -s my_provider`)
- [ ] Inventory regenerated (`cas export-inventory`)
