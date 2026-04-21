"""
One-shot: merge all data centers currently on the QGIS map into the root CSV.

Reads the 5 regional GeoJSON files that power index.html, finds DCs missing
from 'Vigilent Data Center Database (US)(Sheet1).csv' (by Name), and appends
them as new rows populating the 11 raw-input columns. Score columns are left
empty so score_datacenters.py will recompute them on the next run.

Existing rows are preserved verbatim — the file is append-only here.
"""

import csv
import json
import re
from pathlib import Path

CSV_PATH = Path("Vigilent Data Center Database (US)(Sheet1).csv")
GEOJSON_FILES = [
    "data/VigilentDataCenterDatabaseUS_22.js",
    "data/VigilentDataCenterDatabaseEurope_20.js",
    "data/VigilentDataCenterDatabaseCanada_21.js",
    "data/VigilentDataCenterDatabaseBrazil_19.js",
    "data/VigilentDataCenterDatabaseOther_23.js",
]

# Columns 1..11 of the CSV header — the inputs the scoring pipeline reads.
INPUT_COLS = [
    "Name", "Country", "City", "State/Province", "Operator",
    "Size (sq ft)", "Size (MW)", "Latitude", "Longitude",
    "Link", "Operational Status",
]


def load_geojson_features(js_path):
    text = Path(js_path).read_text()
    m = re.search(r"=\s*(\{.*\});?\s*$", text, re.S)
    return json.loads(m.group(1))["features"]


def feature_to_row(feature):
    p = feature["properties"]
    return {
        "Name": (p.get("Name") or "").strip(),
        "Country": (p.get("Country") or "").strip(),
        "City": (p.get("City") or "").strip(),
        "State/Province": (p.get("State/Province") or "").strip(),
        "Operator": (p.get("Operator") or "").strip(),
        "Size (sq ft)": _num(p.get("Size (sq ft)")),
        "Size (MW)": _num(p.get("Size (MW)")),
        "Latitude": _num(p.get("Latitude") or feature["geometry"]["coordinates"][1]),
        "Longitude": _num(p.get("Longitude") or feature["geometry"]["coordinates"][0]),
        "Link": "",
        "Operational Status": (p.get("Operational Status") or "").strip(),
    }


def _num(v):
    if v is None or v == "":
        return ""
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return str(v).strip()


def main():
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    existing_names = {r[0].strip() for r in rows[1:] if r and r[0].strip()}

    # Gather every DC from every regional GeoJSON, dedup by Name.
    all_features = {}
    per_region = {}
    for js in GEOJSON_FILES:
        region = Path(js).stem.replace("VigilentDataCenterDatabase", "").rsplit("_", 1)[0]
        feats = load_geojson_features(js)
        per_region[region] = len(feats)
        for feat in feats:
            name = (feat["properties"].get("Name") or "").strip()
            if name and name not in all_features:
                all_features[name] = feat

    missing = [name for name in all_features if name not in existing_names]

    print(f"Map DCs by region: {per_region}")
    print(f"Unique DCs on map: {len(all_features)}")
    print(f"Already in CSV:    {len(existing_names)}")
    print(f"To append:         {len(missing)}")

    if not missing:
        print("Nothing to do.")
        return

    # Append new rows using the 27-column header. Only input columns populated.
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for name in missing:
            row_dict = feature_to_row(all_features[name])
            w.writerow([row_dict.get(col, "") for col in header])

    print(f"Appended {len(missing)} rows to {CSV_PATH}")


if __name__ == "__main__":
    main()
