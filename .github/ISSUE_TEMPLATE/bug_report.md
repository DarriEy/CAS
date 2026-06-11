---
name: Bug report
about: Something in CAS is broken or behaving unexpectedly
labels: bug
---

## What happened

A clear description of the bug.

## How to reproduce

```bash
# Exact command(s) or minimal Python snippet, including the geometry and dataset ids
cas extract -g '...' -d provider:dataset
```

## Expected behavior

What you expected instead.

## Output / traceback

```text
Paste the full error output or traceback here.
```

## Environment

- CAS version (`cas --version`):
- Python version:
- OS:
- Install method (PyPI / source checkout):

## Notes

If the failure involves a live provider, please re-run once before filing —
upstream services have transient outages (see the daily
[provider health workflow](../../actions/workflows/provider-health.yml)).
