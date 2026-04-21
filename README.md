# Vigilent Data Center Map

Interactive choropleth map of global data centers with a Vigilent
scoring model overlay. Open `index.html` in a browser (or serve it
with any static web server) to view.

## Updating the map

The file `Vigilent Data Center Database (US)(Sheet1).csv` at the repo
root is the **single source of truth** for every data center shown on
the map. To add, remove, or edit data centers:

1. Edit the CSV — only the first 11 columns matter (Name, Country,
   City, State/Province, Operator, Size (sq ft), Size (MW), Latitude,
   Longitude, Link, Operational Status). Score columns are regenerated.
2. Run the sync script:

   ```bash
   python3 sync_map_from_csv.py
   ```

3. Refresh `index.html` in your browser.

The sync script:

- Parses the CSV and buckets rows by country into the five regional
  layers (US / Canada / Europe / Brazil / Other).
- Geocodes any rows with missing `Latitude` / `Longitude` using the
  city-coordinate table in `import_full_database.py`, falling back to
  the OpenStreetMap Nominatim API when no known coordinate exists.
- Re-runs the scoring pipeline (`score_datacenters.py`) so every DC
  gets a fresh composite score, savings/MW, payback, and EJ metrics.
- Bakes the state/province-level electricity rate, water rate, and
  regulation list into every DC feature so the popup shows them.
- Regenerates the five `data/VigilentDataCenterDatabase*.js` files
  that the map loads.

**Warning:** the regional `.js` GeoJSON files are rebuilt from
scratch on every sync. Edit the CSV, not those files.

## Scoring parameters

Vigilent scoring lives in `vigilent_engine.py` (`SCORING_CONFIG`,
`compute_score`, `compute_ej_impact`). State- and country-level
electricity rates live in `score_datacenters.py`:

- `STATE_ELECTRICITY_RATES` — US states, Canadian provinces, Brazilian
  states. Values come directly from the map's regional commercial-rate
  layers.
- `COUNTRY_ELECTRICITY_RATES` — European countries (from the Europe
  choropleth) plus India, Singapore, and Thailand (published commercial
  tariffs; no map layer for those three).

The lookup cascades: state/province → country → $0.12/kWh default.

## File layout

```
.
├── index.html                         # the map
├── Vigilent Data Center Database (US)(Sheet1).csv   # source of truth
├── sync_map_from_csv.py               # CSV → GeoJSONs (run after edits)
├── score_datacenters.py               # scoring pipeline
├── vigilent_engine.py                 # scoring model
├── import_full_database.py            # initial Excel → GeoJSON importer
├── data/                              # generated layer files
│   ├── VigilentDataCenterDatabase{US,Canada,Europe,Brazil,Other}_*.js
│   ├── {US,Canada,Europe,Brazil}CommercialElectricityRates*.js
│   ├── ...RegulationsByQuantity*.js
│   ├── ...CommercialWaterRates*.js
│   └── ...CompositeScore*.js
├── css/, js/                          # qgis2web static assets
└── output/
    ├── scored_datacenters.csv         # generated; used by sync script
    └── scored_datacenters.json        # full structured scoring output
```

## Prerequisites

Python 3.9+ with stdlib only; `score_datacenters.py` imports
`vigilent_engine.py` which ships with the repo. No virtualenv required.

If the sync script reports `SKIPPED (no coords)` for a new DC, add the
city's latitude/longitude directly to the CSV row or extend
`CITY_COORDS` in `import_full_database.py`.
