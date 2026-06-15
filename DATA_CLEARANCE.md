# Data Clearance — CAS (Community Attribute Service)

Per-provider licensing clearance for commercial use and redistribution. **This documents the terms of third-party data sources; it does not grant any rights to that data.** The CAS *code* is licensed separately (see `LICENSE`); using CAS to acquire data does not transfer any rights in the data itself. You are responsible for complying with each source's terms. Machine-readable detail: [`inventory/clearance.csv`](inventory/clearance.csv).

## Two-axis model

- **commercial_use** — may an *end user* use the data for commercial purposes?
- **redistribution** — may a *third party re-host/re-serve* it? (`conditional` = yes with attribution/share-alike)

## Tiers

| Tier | Meaning | Self-hosted client (commercial) | Hosted SaaS (redistribution) |
|---|---|---|---|
| A | Public domain / open | ✅ | ✅ |
| B | Attribution required | ✅ (attribute) | ✅ (attribute) |
| B-SA | Attribution + share-alike | ✅ | ⚠️ derived data inherits copyleft |
| C | Non-commercial / research-only | 🔴 gate out | 🔴 gate out |
| D | No redistribution / gated | user-BYO only | 🔴 never serve |
| E | Unknown — unverified | ⚠️ treat as restricted | 🔴 until cleared |

## CAS summary (228 providers)

| A | B | B-SA | C | D | E |
|--|--|--|--|--|--|
| 36 | 158 | 13 | 12 | 2 | 7 |

**Commercial-clearable (A/B): 194/228.** Gate from commercial use: 12 C + 7 E. Never host: 2 D.

## Restricted / unverified providers (do not auto-clear)

| Tier | Provider | License | Why |
|---|---|---|---|
| C | ALOS Forest/Non-Forest 25m | non-commercial/research | non-commercial |
| C | ALOS World 3D 30m | non-commercial/research | non-commercial |
| C | Argentina Soil Map (INTA) | CC-BY-NC-SA-2.5 | non-commercial |
| C | Brazil Biomes (IBGE) | IBGE-restricted | non-commercial |
| C | HydroBASINS upstream drainage area (Americas) | non-commercial/research | non-commercial |
| C | HydroLAKES mean depth (Central Asia) | non-commercial/research | non-commercial |
| C | HydroRIVERS average discharge (South America) | non-commercial/research | non-commercial |
| C | MERIT DEM 90m | CC-BY-NC | non-commercial |
| C | MERIT Hydro 90m | CC-BY-NC | non-commercial |
| C | POLARIS 30m (US) | CC-BY-NC | non-commercial |
| C | TanDEM-X 90m (DLR) | non-commercial/research | non-commercial |
| C | UK Soilscapes (Cranfield) | proprietary-LandIS | non-commercial |
| D | India LULC (NRSC) | Bhuvan-restricted | no redistribution / permission-gated |
| D | Lithuania DEM (INSPIRE) | geoportal.lt-restricted | no redistribution / permission-gated |
| E | ESA CCI Land Cover 300m | ESA-CCI-LC | no verifiable terms — contact source |
| E | Global Permafrost (Zurich) | no-formal-licence | no verifiable terms — contact source |
| E | Greece Geological Map (IGME) | IGME-restricted | no verifiable terms — contact source |
| E | India Soil Map (NBSS&LUP) | unknown | no verifiable terms — contact source |
| E | Peru Land Cover (MINAM) | not-specified | no verifiable terms — contact source |
| E | Slovenia Land Use (MKGP) | not-defined | no verifiable terms — contact source |
| E | Slovenia Soil Map (MKGP) | not-defined | no verifiable terms — contact source |

## Method

Each provider's free-text licence was normalized to the two-axis schema and tier; values marked `agent-verified` in `clearance.csv` were confirmed against the official licence page (see the `source` column). Tiers are derived deterministically; re-running the classifier preserves verified rows. `E` rows have no publishable terms and require a direct request to the source agency or a legal determination.
