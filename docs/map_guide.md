# Vigilent Data Center Map -- Client Guide

## What This Map Shows

The Vigilent Map is an interactive web map showing data center locations across the US, Canada, Europe, and Brazil. Each data center is scored by the Vigilent optimization model and color-coded by target quality:

- **Large green circles** = Excellent targets (score 75-100)
- **Medium blue circles** = Good targets (score 50-74)
- **Small light blue circles** = Moderate targets (score 25-49)
- **Small gray circles** = Not yet scored

Click any data center to see its details: operator, size (MW), composite score, payback period, savings per MW, and environmental justice metrics.

---

## How to Use the Map

### Viewing Data Centers
- **Click a data center** to open its popup with scoring details
- **Zoom in/out** with scroll wheel or +/- buttons
- When zoomed out, nearby data centers are grouped into clusters (numbers indicate how many)

### Layer Controls (right side panel)
Toggle map layers on/off:
- **Data Center layers** (US, Canada, Europe, Brazil) -- show/hide DC points by region
- **Composite Score** -- colored regions showing overall Vigilent opportunity
- **Electricity Rates** -- state/province commercial electricity costs
- **Water Rates** -- state/province commercial water costs
- **Regulations** -- number of relevant regulations per state/province
- **Outline Maps** -- country/province boundary lines

### Filter by Operator
Use the **"Filter by Operator"** dropdown (top-right) to show only data centers belonging to a specific operator. Select "All Operators" to reset.

---

## How to Add New Data Centers

### Step 1: Add to the CSV database
Open `Vigilent Data Center Database (US)(Sheet1).csv` and add a new row with these columns:

| Column | Required | Example |
|--------|----------|---------|
| Name | Yes | `DFW-4` |
| Country | Yes | `USA` |
| City | Yes | `Dallas` |
| State/Province | Yes | `TX` |
| Latitude | Yes | `32.78` |
| Longitude | Yes | `-96.80` |
| Operator | Yes | `Digital Realty` |
| Size (MW) | Yes | `15.0` |
| Size (sq ft) | Optional | `80000` |
| Operational Status | Yes | `Active` |

**Finding coordinates**: Search "[City name] latitude longitude" in Google, or use Google Maps (right-click a location to copy coordinates).

### Step 2: Run the scoring pipeline
```
python3 score_datacenters.py
```
This produces updated `output/scored_datacenters.json` and `output/scored_datacenters.csv` with the new data center scored.

### Step 3: Update the map data
The map reads from GeoJSON files in the `qgis2web_.../data/` folder. To update:

1. Open the relevant data file (e.g., `VigilentDataCenterDatabaseUS_22.js`)
2. Add a new feature entry following the existing format (copy an existing entry and update the values)
3. Save and refresh the map in your browser

Alternatively, re-export from QGIS if you have the QGIS project file set up.

---

## How to Modify the Map

### Changing map appearance
The map styling is defined in `index.html` within `<script>` tags:
- **Data center colors/sizes**: Search for `style_VigilentDataCenterDatabase` functions
- **Layer colors**: Search for `style_[LayerName]` functions
- **Popup content**: Search for `pop_[LayerName]` functions

### Changing scoring thresholds
The color-coding thresholds (Excellent/Good/Moderate) are set in:
- `score_datacenters.py` line 96-105 (the `classify_score()` function)
- `index.html` style functions (the visual representation)

### Changing the scoring model
The model weights and formulas are in `vigilent_engine.py`:
- `SCORING_CONFIG` (line 18) -- factor weights and min/max bounds
- `compute_score()` (line 92) -- the scoring calculation
- `inputs_spec.json` -- documents all inputs and their sources

---

## Key Files

| File | Purpose |
|------|---------|
| `Vigilent Data Center Database (US)(Sheet1).csv` | Master list of all data centers |
| `vigilent_engine.py` | The scoring model (source of truth for all calculations) |
| `score_datacenters.py` | Batch scoring -- reads CSV, scores all DCs, outputs JSON/CSV |
| `inputs_spec.json` | Documents all model inputs, defaults, and sources |
| `qgis2web_.../index.html` | The interactive map (open in any browser) |
| `qgis2web_.../data/*.js` | Map data layers (GeoJSON format) |
| `output/scored_datacenters.json` | Full scoring results (used by map and reports) |
| `output/scored_datacenters.csv` | Flat table of results (for spreadsheet import) |
| `output/missing_inputs_report.csv` | Shows which inputs are estimated vs. real per DC |

---

## Hosting & Sharing

The map is a self-contained HTML bundle -- it works by opening `index.html` in any browser (Chrome, Firefox, Safari, Edge). No server or internet connection required for local use.

For online sharing, the map is deployed to **Netlify**:
- Drag the `qgis2web_...` folder into Netlify's deploy interface
- Share the resulting URL with clients
- Optional: Add password protection via Netlify's site settings

---

## Troubleshooting

**Map loads slowly**: Run `python3 optimize_map.py` to simplify boundary geometry. This reduces the map from ~700 MB to ~20 MB.

**Data center not showing up**: Check that the Latitude/Longitude values are correct (latitude should be roughly 25-50 for US, longitude should be negative for US/Americas).

**Score shows as "Not Scored"**: The DC may be missing a required field (Size MW). Check the CSV and ensure all required columns are filled.
