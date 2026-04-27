"""
build_global_layers.py
======================
Merges the four per-region choropleth layers (electricity / water /
regulations / composite) into four *global* layer files. All four use
a single global gradient so scores are comparable across countries.

The composite score is recomputed from scratch against the global
min/max of each input metric:

    elec_norm = (elec - elec_min) / (elec_max - elec_min) * 100
    water_norm = (water - water_min) / (water_max - water_min) * 100
    reg_norm   = (regs - reg_min) / (reg_max - reg_min) * 100

    composite  = 0.50 * elec_norm
               + 0.25 * water_norm
               + 0.25 * reg_norm

Higher composite = stronger Vigilent opportunity (expensive rates +
existing efficiency regulations).  Weights live in WEIGHTS below;
edit there to retune.

Output files (all written to data/):
    GlobalElectricity.js   var json_GlobalElectricity
    GlobalWater.js         var json_GlobalWater
    GlobalRegulations.js   var json_GlobalRegulations
    GlobalComposite.js     var json_GlobalComposite

Usage:
    python3 build_global_layers.py
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

WEIGHTS = {"electricity": 0.50, "water": 0.25, "regulations": 0.25}

# For each region, the four per-region layer files.
# NAME_KEY is the property that holds the polygon's display name.
REGIONS = [
    {
        "region": "US",
        "name_key": "NAME",
        "elec_file":  "USCommercialElectricityRateskWh_17.js",
        "water_file": "USCommercialWaterRates1000gallons_18.js",
        "reg_file":   "USRegulationsByQuantity_16.js",
        "elec_key":  "Electricity By State_Commercial Electricity Rate Per State (¢/kWh)",
        "water_key": "Water Cost By State_Commercial Water Price ($/1000 gallons)",
        "reg_key":   "Regulations_Regulations:",
    },
    {
        "region": "Europe",
        "name_key": "NAME",
        "elec_file":  "EuropeCommercialElectricityRateskWh_13.js",
        "water_file": "EuropeCommercialWaterRates1000gallons_14.js",
        "reg_file":   "EuropeRegulationsByQuantity_12.js",
        "elec_key":  "Europe Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Europe Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Europe Statistics_Regulations",
    },
    {
        "region": "Canada",
        "name_key": "PRENAME",
        "elec_file":  "CanadaCommercialElectricityRatekWh_8.js",
        "water_file": "CanadaCommercialWaterRate1000gallons_9.js",
        "reg_file":   "CanadaRegulationsByQuantity_7.js",
        "elec_key":  "Canada Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Canada Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Canada Statistics_Regulations",
    },
    {
        "region": "Brazil",
        "name_key": "name",
        "elec_file":  "BrazilCommercialElectricityRateskWh_3.js",
        "water_file": "BrazilCommercialWaterRates1000gallons_4.js",
        "reg_file":   "BrazilRegulationsByQuantity_2.js",
        "elec_key":  "Brazil Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Brazil Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Brazil Statistics_Regulations",
    },
    {
        "region": "India",
        "name_key": "NAME",
        "elec_file":  "IndiaCommercialElectricityRateskWh_22.js",
        "water_file": "IndiaCommercialWaterRates1000gallons_23.js",
        "reg_file":   "IndiaRegulationsByQuantity_24.js",
        "elec_key":  "India Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "India Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "India Statistics_Regulations",
    },
    # APAC additions (built 2026-04-27 via build_apac_layers.py)
    {
        "region": "Australia",
        "name_key": "NAME",
        "elec_file":  "AustraliaCommercialElectricityRateskWh_30.js",
        "water_file": "AustraliaCommercialWaterRates1000gallons_31.js",
        "reg_file":   "AustraliaRegulationsByQuantity_32.js",
        "elec_key":  "Australia Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Australia Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Australia Statistics_Regulations",
    },
    {
        "region": "Singapore",
        "name_key": "NAME",
        "elec_file":  "SingaporeCommercialElectricityRateskWh_31.js",
        "water_file": "SingaporeCommercialWaterRates1000gallons_32.js",
        "reg_file":   "SingaporeRegulationsByQuantity_33.js",
        "elec_key":  "Singapore Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Singapore Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Singapore Statistics_Regulations",
    },
    {
        "region": "Thailand",
        "name_key": "NAME",
        "elec_file":  "ThailandCommercialElectricityRateskWh_32.js",
        "water_file": "ThailandCommercialWaterRates1000gallons_33.js",
        "reg_file":   "ThailandRegulationsByQuantity_34.js",
        "elec_key":  "Thailand Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Thailand Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Thailand Statistics_Regulations",
    },
    {
        "region": "Vietnam",
        "name_key": "NAME",
        "elec_file":  "VietnamCommercialElectricityRateskWh_33.js",
        "water_file": "VietnamCommercialWaterRates1000gallons_34.js",
        "reg_file":   "VietnamRegulationsByQuantity_35.js",
        "elec_key":  "Vietnam Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Vietnam Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Vietnam Statistics_Regulations",
    },
    {
        "region": "Malaysia",
        "name_key": "NAME",
        "elec_file":  "MalaysiaCommercialElectricityRateskWh_34.js",
        "water_file": "MalaysiaCommercialWaterRates1000gallons_35.js",
        "reg_file":   "MalaysiaRegulationsByQuantity_36.js",
        "elec_key":  "Malaysia Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Malaysia Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Malaysia Statistics_Regulations",
    },
    {
        "region": "Indonesia",
        "name_key": "NAME",
        "elec_file":  "IndonesiaCommercialElectricityRateskWh_35.js",
        "water_file": "IndonesiaCommercialWaterRates1000gallons_36.js",
        "reg_file":   "IndonesiaRegulationsByQuantity_37.js",
        "elec_key":  "Indonesia Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Indonesia Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Indonesia Statistics_Regulations",
    },
    {
        "region": "Philippines",
        "name_key": "NAME",
        "elec_file":  "PhilippinesCommercialElectricityRateskWh_36.js",
        "water_file": "PhilippinesCommercialWaterRates1000gallons_37.js",
        "reg_file":   "PhilippinesRegulationsByQuantity_38.js",
        "elec_key":  "Philippines Statistics_Commercial Electricity Rate (¢/kWh)",
        "water_key": "Philippines Statistics_Commercial Water Rate ($/1000 gallons)",
        "reg_key":   "Philippines Statistics_Regulations",
    },
]

OUTPUTS = {
    "electricity": ("GlobalElectricity.js",  "json_GlobalElectricity"),
    "water":       ("GlobalWater.js",        "json_GlobalWater"),
    "regulations": ("GlobalRegulations.js",  "json_GlobalRegulations"),
    "composite":   ("GlobalComposite.js",    "json_GlobalComposite"),
}


# ---------------------------------------------------------------------------

def load_geojson(path):
    text = Path(path).read_text()
    m = re.match(r"var\s+(\w+)\s*=\s*", text)
    if not m:
        raise ValueError(f"Not a qgis2web file: {path}")
    body = text[m.end():].rstrip().rstrip(";")
    return m.group(1), json.loads(body)


def write_geojson(path, var_name, obj):
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    Path(path).write_text(f"var {var_name} = {body};", encoding="utf-8")


def count_regulations(value):
    if not value:
        return 0
    return sum(1 for line in str(value).split("\n") if line.strip())


def safe_norm(x, lo, hi):
    """Min-max normalize to 0-100. Returns None if x is None; 50 if degenerate."""
    if x is None:
        return None
    if hi <= lo:
        return 50.0
    return 100.0 * (x - lo) / (hi - lo)


# ---------------------------------------------------------------------------

def collect_per_region():
    """Return a list of {region, name, geometry, elec, water, reg_count, regs_text}."""
    rows = []
    for spec in REGIONS:
        _, data = load_geojson(DATA_DIR / spec["elec_file"])
        # Water and reg layers have the same polygon geometries — pull values only.
        _, wdata = load_geojson(DATA_DIR / spec["water_file"])
        _, rdata = load_geojson(DATA_DIR / spec["reg_file"])

        water_by_name = {
            f["properties"].get(spec["name_key"]): f["properties"].get(spec["water_key"])
            for f in wdata["features"]
        }
        reg_by_name = {
            f["properties"].get(spec["name_key"]): f["properties"].get(spec["reg_key"])
            for f in rdata["features"]
        }

        for feat in data["features"]:
            p = feat["properties"]
            name = p.get(spec["name_key"])
            if not name:
                continue
            regs_text = reg_by_name.get(name)
            rows.append({
                "region": spec["region"],
                "name": name,
                "geometry": feat["geometry"],
                "elec": p.get(spec["elec_key"]),
                "water": water_by_name.get(name),
                "reg_count": count_regulations(regs_text),
                "regs_text": regs_text or "",
            })
    return rows


def global_extents(rows, keys):
    """Compute (min, max) per key, skipping None."""
    ext = {}
    for k in keys:
        vals = [r[k] for r in rows if r[k] is not None]
        ext[k] = (min(vals), max(vals)) if vals else (0, 1)
    return ext


def build_output_feature(row, props):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": row["geometry"],
    }


def main():
    print("=" * 60)
    print("  BUILD GLOBAL LAYERS")
    print("=" * 60)

    rows = collect_per_region()
    print(f"\nCollected {len(rows)} polygons across "
          f"{len({r['region'] for r in rows})} regions.")

    ext = global_extents(rows, ["elec", "water", "reg_count"])
    print(f"\nGlobal extents:")
    print(f"  electricity:   {ext['elec'][0]:.2f} .. {ext['elec'][1]:.2f}  ¢/kWh")
    print(f"  water:         {ext['water'][0]:.2f} .. {ext['water'][1]:.2f}  $/1000 gal")
    print(f"  regulations:   {ext['reg_count'][0]} .. {ext['reg_count'][1]}  statutes")

    # --- Build 4 feature collections ---
    # Missing-data rule (per Adam, 2026-04-27):
    #   • Popup-visible fields keep the original null so popups display "N/A"
    #   • Gradient `value` and composite math substitute 0 in place of null
    #     so the polygon still renders (lowest gradient bin) and the composite
    #     score stays computable instead of dropping the polygon entirely.
    feats_elec, feats_water, feats_reg, feats_comp = [], [], [], []
    for r in rows:
        en = safe_norm(r["elec"],      *ext["elec"])
        wn = safe_norm(r["water"],     *ext["water"])
        rn = safe_norm(r["reg_count"], *ext["reg_count"])
        # Composite always computes — substitute 0 for any missing component
        composite = round(
            WEIGHTS["electricity"] * (en if en is not None else 0.0)
            + WEIGHTS["water"]       * (wn if wn is not None else 0.0)
            + WEIGHTS["regulations"] * (rn if rn is not None else 0.0),
            2,
        )

        base_props = {
            "Region": r["region"],
            "Name": r["name"],
            "electricity_rate_cents_per_kwh": r["elec"],
            "water_rate_per_1000_gal": r["water"],
            "regulations_count": r["reg_count"],
            "regulations_text": r["regs_text"],
            "electricity_norm": round(en, 2) if en is not None else None,
            "water_norm": round(wn, 2) if wn is not None else None,
            "regulations_norm": round(rn, 2) if rn is not None else None,
            "global_composite": composite,
        }

        # Gradient values — substitute 0 when raw is None so polygon renders.
        elec_val  = r["elec"]      if r["elec"]      is not None else 0
        water_val = r["water"]     if r["water"]     is not None else 0
        reg_val   = r["reg_count"] if r["reg_count"] is not None else 0

        feats_elec.append(build_output_feature(r, {**base_props, "value": elec_val}))
        feats_water.append(build_output_feature(r, {**base_props, "value": water_val}))
        feats_reg.append(build_output_feature(r,   {**base_props, "value": reg_val}))
        feats_comp.append(build_output_feature(r,  {**base_props, "value": composite}))

    # --- Write 4 files ---
    print("\nWriting global layers:")
    for metric, feats in [
        ("electricity", feats_elec),
        ("water",       feats_water),
        ("regulations", feats_reg),
        ("composite",   feats_comp),
    ]:
        fname, var_name = OUTPUTS[metric]
        path = DATA_DIR / fname
        obj = {
            "type": "FeatureCollection",
            "name": var_name.replace("json_", ""),
            "global_extents": {
                "electricity": list(ext["elec"]),
                "water": list(ext["water"]),
                "regulations": list(ext["reg_count"]),
            },
            "weights": WEIGHTS,
            "features": feats,
        }
        write_geojson(path, var_name, obj)
        print(f"  {fname}  ({len(feats)} features, {path.stat().st_size // 1024} KB)")

    # --- Summary: show composite distribution ---
    print("\nGlobal composite score — top 10 / bottom 10:")
    scored = sorted(
        [r for r in rows if r["elec"] is not None and r["water"] is not None],
        key=lambda r: (
            WEIGHTS["electricity"] * safe_norm(r["elec"], *ext["elec"])
            + WEIGHTS["water"] * safe_norm(r["water"], *ext["water"])
            + WEIGHTS["regulations"] * safe_norm(r["reg_count"], *ext["reg_count"])
        ),
        reverse=True,
    )
    for r in scored[:10]:
        c = (WEIGHTS["electricity"] * safe_norm(r["elec"], *ext["elec"])
             + WEIGHTS["water"] * safe_norm(r["water"], *ext["water"])
             + WEIGHTS["regulations"] * safe_norm(r["reg_count"], *ext["reg_count"]))
        print(f"  {c:6.2f}  {r['region']:7s}  {r['name']}")
    print("  ...")
    for r in scored[-5:]:
        c = (WEIGHTS["electricity"] * safe_norm(r["elec"], *ext["elec"])
             + WEIGHTS["water"] * safe_norm(r["water"], *ext["water"])
             + WEIGHTS["regulations"] * safe_norm(r["reg_count"], *ext["reg_count"]))
        print(f"  {c:6.2f}  {r['region']:7s}  {r['name']}")


if __name__ == "__main__":
    main()
