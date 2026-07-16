# Provider support levels

CAS distinguishes a registered connector from one demonstrated to extract data.
Every provider is assigned exactly one support tier from the committed
end-to-end health baseline:

| Tier | Evidence and meaning |
| --- | --- |
| `verified` | A real zonal extraction returned a finite value in the baseline sweep. |
| `credentialed` | Extraction is implemented, but the sweep confirmed that provider credentials are required. |
| `mirror-backed` | Extraction uses a curated local mirror; CI intentionally does not download the source archive. |
| `degraded` | The baseline extraction returned no usable data or the provider was unavailable. |
| `metadata-only` | The connector is registered and discoverable but has not yet appeared in the committed extraction baseline. |

The generated report records the observation timestamp, protocol, health
status, and evidence file for every provider. Inspect or regenerate it with:

```bash
cas providers --support
cas export-support
```

The committed machine-readable result is `inventory/support.json`. A health
result is evidence from a point in time, not a permanent uptime guarantee;
live upstream services can still fail transiently.
