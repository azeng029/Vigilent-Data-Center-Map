"""
Reverse Composite Score Gradient
=================================
Updates the Composite field in all 4 regional GeoJSON data files
from (max - x) / range to (x - min) / range normalization.

This makes high-utility-cost regions score higher (= better Vigilent targets).
"""

import json
import os
import re

MAP_DIR = "qgis2web_2026_04_01-14_53_08_869925"
DATA_DIR = os.path.join(MAP_DIR, "data")

# Regional config: file → (electricity_key, water_key)
REGIONS = {
    "USCompositeScore_15.js": {
        "elec_key": "Electricity By State_Commercial Electricity Rate Per State (¢/kWh)",
        "water_key": "Water Cost By State_Commercial Water Price ($/1000 gallons)",
        "reg_key": "Regulations_Regulations:",
    },
    "CanadaCompositeScore_6.js": {
        "elec_key": "Canada Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Canada Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key": "Canada Statistics_Regulations",
    },
    "EuropeCompositeScore_11.js": {
        "elec_key": "Europe Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Europe Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key": "Europe Statistics_Regulations",
    },
    "BrazilCompositeScore_1.js": {
        "elec_key": "Brazil Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Brazil Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key": "Brazil Statistics_Regulations",
    },
}


def parse_js_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.match(r'var\s+(json_\w+)\s*=\s*', content)
    if not match:
        raise ValueError(f"Could not parse variable name from {filepath}")
    var_name = match.group(1)
    json_str = content[match.end():].rstrip().rstrip(';')
    geojson = json.loads(json_str)
    return var_name, geojson


def write_js_file(filepath, var_name, geojson):
    json_str = json.dumps(geojson, separators=(',', ':'))
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"var {var_name} = {json_str};")


def count_regulations(reg_str):
    if not reg_str or not reg_str.strip():
        return 0
    return len([r for r in reg_str.split("\n") if r.strip()])


def recalculate_composite(geojson, elec_key, water_key, reg_key):
    """Recalculate Composite using (x - min) / range normalization."""
    features = geojson["features"]

    # Collect values
    elecs = []
    waters = []
    reg_counts = []
    for f in features:
        p = f["properties"]
        e = p.get(elec_key)
        w = p.get(water_key)
        r = count_regulations(p.get(reg_key, ""))
        if e is not None:
            elecs.append(e)
        if w is not None:
            waters.append(w)
        reg_counts.append(r)

    if not elecs or not waters:
        return 0

    e_min, e_max = min(elecs), max(elecs)
    w_min, w_max = min(waters), max(waters)

    updated = 0
    for i, f in enumerate(features):
        p = f["properties"]
        e = p.get(elec_key)
        w = p.get(water_key)
        r = count_regulations(p.get(reg_key, ""))

        if e is None or w is None:
            continue

        # (x - min) / range normalization: higher values = higher score
        e_score = ((e - e_min) / (e_max - e_min) * 100) if e_max > e_min else 50
        w_score = ((w - w_min) / (w_max - w_min) * 100) if w_max > w_min else 50
        # Regulations: 3-bucket normalization matching QGIS reference: 0→0, 1→50, 2+→100
        r_score = 0 if r == 0 else (50 if r == 1 else 100)

        new_composite = round((e_score + w_score + r_score) / 3, 2)
        old = p.get("Composite", "N/A")
        p["Composite"] = new_composite
        updated += 1

    return updated


def main():
    print("=" * 60)
    print("  REVERSE COMPOSITE SCORE GRADIENT")
    print("  (max-x)/range → (x-min)/range")
    print("=" * 60)

    for filename, cfg in REGIONS.items():
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            print(f"\n  SKIP (not found): {filename}")
            continue

        print(f"\n  Processing: {filename}")
        var_name, geojson = parse_js_file(filepath)

        updated = recalculate_composite(
            geojson, cfg["elec_key"], cfg["water_key"], cfg["reg_key"]
        )
        print(f"    Updated {updated} features")

        write_js_file(filepath, var_name, geojson)
        print(f"    Written: {filepath}")

    print("\n  Done! Reload the map to see the reversed gradient.\n")


if __name__ == "__main__":
    main()
