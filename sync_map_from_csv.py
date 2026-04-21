"""
sync_map_from_csv.py
====================
Makes the root CSV the single source of truth for the QGIS map.

Reads `Vigilent Data Center Database (US)(Sheet1).csv`, rebuilds the five
regional data-center GeoJSON layers under `data/`, runs the scoring pipeline,
and bakes scoring + polygon stats back into each DC feature.

Usage:
    python3 sync_map_from_csv.py

After editing the CSV (add / remove / update rows), run this script once and
the map picks up the changes on next reload. No QGIS re-export is required.

Design:
  - Every DC's identity fields come from the CSV.
  - Polygon-level stats (electricity rate, water rate, regulations) are copied
    from the regional choropleth layers, matched by State/Province or Country.
  - Score fields (composite_score, savings_per_mw, payback_years, EJ stats)
    come from running `score_datacenters.py` and re-reading its output CSV.
  - The five regional GeoJSON files are fully regenerated — any manual edits
    there will be lost.  Always edit the CSV.
"""

import csv
import json
import re
import subprocess
import sys
from pathlib import Path

from import_full_database import CITY_COORDS, geocode_nominatim

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "Vigilent Data Center Database (US)(Sheet1).csv"
DATA_DIR = ROOT / "data"
SCORED_CSV = ROOT / "output" / "scored_datacenters.csv"

# Region -> (geojson file, qgis2web var name)
REGION_FILES = {
    "US":     ("VigilentDataCenterDatabaseUS_22.js",    "json_VigilentDataCenterDatabaseUS_22"),
    "Canada": ("VigilentDataCenterDatabaseCanada_21.js", "json_VigilentDataCenterDatabaseCanada_21"),
    "Europe": ("VigilentDataCenterDatabaseEurope_20.js", "json_VigilentDataCenterDatabaseEurope_20"),
    "Brazil": ("VigilentDataCenterDatabaseBrazil_19.js", "json_VigilentDataCenterDatabaseBrazil_19"),
    "Other":  ("VigilentDataCenterDatabaseOther_23.js",  "json_VigilentDataCenterDatabaseOther_23"),
}

# Countries that belong to the Europe regional layer (polygon matching uses
# the country name as the key in EuropeCommercialElectricityRateskWh_13.js).
EUROPE_COUNTRIES = {
    "United Kingdom", "Germany", "France", "Ireland", "Sweden", "Spain",
    "Netherlands", "The Netherlands", "NEtherlands", "Finland", "Portugal",
    "Italy", "Denmark", "Norway", "Belgium", "Austria", "Switzerland",
    "Poland", "Czechia", "Hungary", "Greece", "Romania", "Bulgaria",
    "Iceland", "Luxembourg", "Estonia", "Latvia", "Lithuania",
    "Slovakia", "Slovenia", "Croatia", "Cyprus", "Malta", "Albania",
    "Macedonia", "Montenegro", "Serbia", "Turkey", "Kosovo",
    "Bosnia and Herz.", "Bosnia and Herzegovina",
}

# US postal abbr -> full state name (matches polygon NAME in USCommercialElectricityRateskWh_17.js)
US_STATE_ABBR_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Normalize country names before using them to match Europe polygons.
COUNTRY_ALIASES = {
    "The Netherlands": "Netherlands",
    "NEtherlands": "Netherlands",
    "Bosnia and Herzegovina": "Bosnia and Herz.",
}

# Per-region polygon stat sources (from the regional commercial-rate layers).
POLYGON_LAYERS = [
    {
        "region": "US",
        "file": "USCommercialElectricityRateskWh_17.js",
        "name_key": "NAME",
        "elec_key": "Electricity By State_Commercial Electricity Rate Per State (¢/kWh)",
        "water_key": "Water Cost By State_Commercial Water Price ($/1000 gallons)",
        "regs_key": "Regulations_Regulations:",
    },
    {
        "region": "Europe",
        "file": "EuropeCommercialElectricityRateskWh_13.js",
        "name_key": "NAME",
        "elec_key": "Europe Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Europe Statistics_Commercial Water Rate ($/1000 gallons)",
        "regs_key": "Europe Statistics_Regulations",
    },
    {
        "region": "Canada",
        "file": "CanadaCommercialElectricityRatekWh_8.js",
        "name_key": "PRENAME",
        "elec_key": "Canada Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Canada Statistics_Commercial Water Rate ($/1000 gallons)",
        "regs_key": "Canada Statistics_Regulations",
    },
    {
        "region": "Brazil",
        "file": "BrazilCommercialElectricityRateskWh_3.js",
        "name_key": "name",
        "elec_key": "Brazil Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Brazil Statistics_Commercial Water Rate ($/1000 gallons)",
        "regs_key": "Brazil Statistics_Regulations",
    },
]

GLOBAL_COMPOSITE_FILE = DATA_DIR / "GlobalComposite.js"

SCORED_FIELD_MAP = {
    # scored CSV column -> GeoJSON property name
    "Composite_Score":     "composite_score",
    "Classification":      "vigilent_classification",
    "Savings_Per_MW":      "savings_per_mw",
    "Payback_Years":       "payback_years",
    "OPEX_Impact_Pct":     "impact_on_opex_pct",
    "Estimated_Savings":   "estimated_savings",
    "Electricity_Price":   "electricity_price_used",
    "EJ_Demographic_Index": "ej_demographic_index",
    "EJ_Energy_Burden_Pct": "ej_energy_burden_pct",
    "CO2_Avoided_MT":      "co2_avoided_metric_tons",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_geojson(path):
    text = Path(path).read_text()
    m = re.match(r"var\s+(\w+)\s*=\s*", text)
    if not m:
        raise ValueError(f"Not a qgis2web-style file: {path}")
    body = text[m.end():].rstrip().rstrip(";")
    return m.group(1), json.loads(body)


def write_geojson(path, var_name, obj):
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    Path(path).write_text(f"var {var_name} = {body};", encoding="utf-8")


def parse_num(v):
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "").strip())
        return int(f) if f.is_integer() else f
    except (ValueError, TypeError):
        return None


def country_to_region(country):
    c = (country or "").strip()
    if c == "USA":
        return "US"
    if c in ("Canada",):
        return "Canada"
    if c in ("Brazil",):
        return "Brazil"
    if c in EUROPE_COUNTRIES:
        return "Europe"
    return "Other"


def resolve_coords(row):
    lat = parse_num(row.get("Latitude"))
    lng = parse_num(row.get("Longitude"))
    if lat is not None and lng is not None:
        return lat, lng

    city = (row.get("City") or "").strip()
    state = (row.get("State/Province") or "").strip()
    country = (row.get("Country") or "").strip()
    for key in [(city, state), (city, country), (city, None), (city, "N/A")]:
        hit = CITY_COORDS.get(key)
        if hit:
            return hit
    return geocode_nominatim(city, state, country)


def load_polygon_stats():
    """Return {region: {polygon_name: {elec, water, regs, *_key}}} plus reverse keys."""
    stats = {}
    for spec in POLYGON_LAYERS:
        _, data = load_geojson(DATA_DIR / spec["file"])
        region_stats = {}
        for feat in data["features"]:
            p = feat["properties"]
            nm = p.get(spec["name_key"])
            if not nm:
                continue
            region_stats[nm.strip()] = {
                "elec": p.get(spec["elec_key"]),
                "water": p.get(spec["water_key"]),
                "regs": p.get(spec["regs_key"]),
            }
        stats[spec["region"]] = {
            "lookup": region_stats,
            "elec_key": spec["elec_key"],
            "water_key": spec["water_key"],
            "regs_key": spec["regs_key"],
        }
    return stats


def load_global_composite_by_polygon():
    """Return {(region, polygon_name): global_composite_score}."""
    if not GLOBAL_COMPOSITE_FILE.exists():
        return {}
    _, data = load_geojson(GLOBAL_COMPOSITE_FILE)
    return {
        (f["properties"]["Region"], f["properties"]["Name"]):
            f["properties"].get("global_composite")
        for f in data["features"]
    }


def polygon_match_name(region, row):
    """Pick the polygon-name key matching this DC's state/country."""
    state = (row.get("State/Province") or "").strip()
    country = (row.get("Country") or "").strip()
    if region == "US":
        return US_STATE_ABBR_TO_NAME.get(state)
    if region == "Canada":
        return state or None
    if region == "Brazil":
        return state or None
    if region == "Europe":
        return COUNTRY_ALIASES.get(country, country) or None
    return None


def enrich(feat, region, polygon_stats, global_composite_lookup):
    match = polygon_match_name(region, feat["properties"])
    if not match:
        return
    spec = polygon_stats.get(region)
    if spec:
        entry = spec["lookup"].get(match)
        if entry:
            p = feat["properties"]
            if entry["elec"] is not None:
                p[spec["elec_key"]] = entry["elec"]
            if entry["water"] is not None:
                p[spec["water_key"]] = entry["water"]
            if entry["regs"]:
                p[spec["regs_key"]] = entry["regs"]

    # Bake the globally-renormalized polygon composite into the DC feature
    # so the popup can show "State/Province Score" without runtime lookup.
    gc = global_composite_lookup.get((region, match))
    if gc is not None:
        feat["properties"]["state_province_composite"] = gc
        feat["properties"]["state_province_name"] = match


def build_feature(row):
    lat, lng = resolve_coords(row)
    if lat is None or lng is None:
        return None
    props = {
        "Name": (row.get("Name") or "").strip(),
        "Country": (row.get("Country") or "").strip(),
        "City": (row.get("City") or "").strip(),
        "State/Province": (row.get("State/Province") or "").strip(),
        "Operator": (row.get("Operator") or "").strip(),
        "Size (sq ft)": parse_num(row.get("Size (sq ft)")),
        "Size (MW)": parse_num(row.get("Size (MW)")),
        "Latitude": lat,
        "Longitude": lng,
        "Operational Status": (row.get("Operational Status") or "").strip(),
    }
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
    }


def load_scored_rows():
    if not SCORED_CSV.exists():
        return {}
    with SCORED_CSV.open(newline="") as f:
        return {
            (r.get("Name") or "").strip(): r
            for r in csv.DictReader(f)
            if (r.get("Name") or "").strip()
        }


def merge_scores(features_by_region, scored_by_name):
    for feats in features_by_region.values():
        for feat in feats:
            name = feat["properties"].get("Name", "")
            scored = scored_by_name.get(name)
            if not scored:
                continue
            for csv_k, js_k in SCORED_FIELD_MAP.items():
                raw = scored.get(csv_k)
                if raw in (None, ""):
                    continue
                num = parse_num(raw)
                feat["properties"][js_k] = num if num is not None else raw


def run_subprocess(label, script):
    result = subprocess.run(
        [sys.executable, str(ROOT / script)],
        capture_output=True, text=True, cwd=ROOT,
    )
    if result.returncode != 0:
        print(f"  ERROR: {script} failed")
        print(result.stderr)
        sys.exit(1)
    # Surface the tail of stdout so the user sees it completed.
    for line in result.stdout.splitlines()[-6:]:
        s = line.strip()
        if s:
            print(f"    {s}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("  SYNC MAP FROM CSV")
    print("=" * 60)

    # 0) Refresh global choropleth layers so the polygon composite baked
    #    into DC popups stays consistent with what the map displays.
    print("\n[1/5] Rebuilding global choropleth layers...")
    run_subprocess("build", "build_global_layers.py")

    # 1) Load CSV + polygon stats
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        csv_rows = [r for r in csv.DictReader(f) if (r.get("Name") or "").strip()]
    polygon_stats = load_polygon_stats()
    global_composite_lookup = load_global_composite_by_polygon()
    print(f"\n[2/5] Loaded {len(csv_rows)} rows from CSV.")

    # 2) Build features, bucketed by region
    features_by_region = {r: [] for r in REGION_FILES}
    skipped = []
    for row in csv_rows:
        region = country_to_region(row.get("Country"))
        feat = build_feature(row)
        if feat is None:
            skipped.append(row.get("Name", "?"))
            continue
        enrich(feat, region, polygon_stats, global_composite_lookup)
        features_by_region[region].append(feat)
    for r, feats in features_by_region.items():
        print(f"    {r:8s}: {len(feats)} features")
    if skipped:
        print(f"    SKIPPED (no coords): {len(skipped)} -> {skipped[:5]}{'...' if len(skipped) > 5 else ''}")

    # 3) Re-run scoring so output CSV matches current input CSV
    print("\n[3/5] Running score_datacenters.py...")
    run_subprocess("score", "score_datacenters.py")

    # 4) Bake scoring fields into features
    print("\n[4/5] Baking score fields into features...")
    scored = load_scored_rows()
    merge_scores(features_by_region, scored)
    print(f"    Score rows available: {len(scored)}")

    # 5) Write GeoJSONs
    print("\n[5/5] Writing regional GeoJSON files...")
    for region, feats in features_by_region.items():
        fname, var_name = REGION_FILES[region]
        path = DATA_DIR / fname
        obj = {
            "type": "FeatureCollection",
            "name": var_name.replace("json_", ""),
            "features": feats,
        }
        write_geojson(path, var_name, obj)
        print(f"    {fname} -> {len(feats)} features ({path.stat().st_size // 1024} KB)")

    print("\nDone. Reload the map to see the changes.")


if __name__ == "__main__":
    main()
