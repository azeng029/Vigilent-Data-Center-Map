"""
Vigilent Data Center Scoring Pipeline
======================================
Reads the US data center CSV, applies the composite scoring model + EJ impact
analysis to 10 selected data centers, and outputs structured results for
database integration.

Usage:
    python3 score_datacenters.py

Outputs:
    output/scored_datacenters.json  — full structured results
    output/scored_datacenters.csv   — flat table for database import
"""

import csv
import json
import os
from vigilent_engine import compute_score, compute_ej_impact, SCORING_CONFIG

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

CSV_PATH = "Vigilent Data Center Database (US)(Sheet1).csv"
OUTPUT_DIR = "output"

# --- Vigilent standard offering (constant across all DCs) ---
VIGILENT_PARAMS = {
    "investment_cost": 1_500_000,
    "energy_reduction_pct": 0.10,
    "water_reduction_pct": 0.05,
    "num_years": 1,
}

# --- Default assumptions for missing DC parameters ---
DEFAULTS = {
    "baseline_pue": 1.55,       # Uptime Institute 2023 global average
    "load_growth_rate": 0.10,   # 10% — industry consensus estimate
    "energy_pct_opex": 0.40,    # 40% — typical for large DCs (Gartner, McKinsey)
}

# --- State commercial electricity rates (EIA / GIS data, $/kWh) ---
STATE_ELECTRICITY_RATES = {
    "TX": 0.0912,   # Source: EIA, matches GIS layer
    "NY": 0.2254,   # Source: EIA, matches GIS layer
    "CO": 0.1332,   # Source: EIA, matches GIS layer
    "NV": 0.0991,   # Source: EIA, matches GIS layer
    "OH": 0.1155,   # Source: EIA, matches GIS layer
    "MA": 0.2340,   # Source: EIA, matches GIS layer
    "CA": 0.2500,   # Source: EIA state average (not in GIS 14-DC set)
    "VA": 0.0973,   # Source: EIA, matches GIS layer
    "WA": 0.1100,   # Source: EIA state average (not in GIS 14-DC set)
    "OR": 0.1136,   # Source: EIA, matches GIS layer
    "MN": 0.1322,   # Source: EIA, matches GIS layer
    "MS": 0.1267,   # Source: EIA, matches GIS layer
    "NJ": 0.1600,   # Source: EIA state average
    "MI": 0.1267,   # Source: EIA — CSV lists C Spire Starkville as MI (likely MS typo)
}

# --- Representative zip codes for EJ impact (city-level) ---
CITY_ZIP_MAP = {
    "Dallas": "75201",
    "New York": "10001",
    "Denver": "80201",
    "Las Vegas": "89101",
    "Columbus": "43201",
    "Boston": "02101",
    "Santa Clara": "95050",
    "Richmond": "23219",
    "Red Oak": "75154",
    "Seattle": "98101",
    "Houston": "77001",
    "Starkville": "39759",
    "Eagan": "55121",
    "Plano": "75024",
    "Hillsboro": "97123",
    "Leesburg": "20175",
    "Ashburn": "20147",
    "Culpeper": "22701",
    "Austin": "78701",
    "Los Angeles": "90001",
    "Piscataway": "08854",
    "Minneapolis": "55401",
    "Clifton": "07011",
    "San Francisco": "94105",
}

# --- Score ALL DCs (set to None to score all, or a list of names to filter) ---
SELECTED_DCS = None  # Score all data centers in the CSV

# ═══════════════════════════════════════════════════════════════════════════════
# SCORING PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def classify_score(score):
    """Classify composite score into Vigilent target tiers."""
    if score >= 75:
        return "Excellent"
    elif score >= 50:
        return "Good"
    elif score >= 25:
        return "Moderate"
    else:
        return "Low"


def load_csv(path):
    """Load CSV and return list of dicts for selected DCs (or all if SELECTED_DCS is None)."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", "").strip()
            if not name:
                continue
            if SELECTED_DCS is None or name in SELECTED_DCS:
                rows.append(row)
    return rows


def parse_mw(raw):
    """Parse Size (MW) from CSV, handling commas and whitespace."""
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def score_datacenter(row):
    """Score a single data center row. Returns full result dict."""
    name = row["Name"].strip()
    city = row["City"].strip()
    state = row["State/Province"].strip()
    mw = parse_mw(row.get("Size (MW)", ""))

    if mw is None or mw <= 0:
        return None

    # Resolve electricity price from state rates
    elec_price = STATE_ELECTRICITY_RATES.get(state)
    if elec_price is None:
        print(f"  WARNING: No electricity rate for state '{state}', using $0.12/kWh")
        elec_price = 0.12

    # Track which inputs are real vs. estimated
    real_inputs = ["dc_size_mw", "city", "state"]
    estimated_inputs = []

    if state in STATE_ELECTRICITY_RATES:
        real_inputs.append("electricity_price (state-level)")
    else:
        estimated_inputs.append("electricity_price")

    estimated_inputs.extend([
        "baseline_pue (industry avg 1.55)",
        "load_growth_rate (industry est 10%)",
        "energy_pct_opex (industry est 40%)",
    ])

    # --- Run composite scoring model ---
    score_result = compute_score(
        dc_size_mw=mw,
        baseline_pue=DEFAULTS["baseline_pue"],
        electricity_price=elec_price,
        load_growth_rate=DEFAULTS["load_growth_rate"],
        energy_pct_opex=DEFAULTS["energy_pct_opex"],
        **VIGILENT_PARAMS,
    )

    # --- Run EJ impact analysis ---
    zip_code = CITY_ZIP_MAP.get(city)
    ej_result = None
    if zip_code:
        ej_result = compute_ej_impact(
            dc_size_mw=mw,
            baseline_pue=DEFAULTS["baseline_pue"],
            load_growth_rate=DEFAULTS["load_growth_rate"],
            energy_reduction_pct=VIGILENT_PARAMS["energy_reduction_pct"],
            zip_code=zip_code,
        )

    composite = score_result["composite_score"]
    classification = classify_score(composite)

    result = {
        # Identity
        "name": name,
        "city": city,
        "state": state,
        "operator": row.get("Operator", "").strip(),
        "size_mw": mw,
        "size_sqft": row.get("Size (sq ft)", "").strip(),
        "latitude": float(row.get("Latitude", 0) or 0),
        "longitude": float(row.get("Longitude", 0) or 0),
        "operational_status": row.get("Operational Status", "").strip(),

        # Inputs used
        "electricity_price": elec_price,
        "baseline_pue": DEFAULTS["baseline_pue"],
        "load_growth_rate": DEFAULTS["load_growth_rate"],
        "energy_pct_opex": DEFAULTS["energy_pct_opex"],

        # Composite scoring
        "composite_score": round(composite, 2),
        "classification": classification,
        "factor_scores": {k: round(v, 2) for k, v in score_result["factor_scores"].items()},
        "savings_per_mw": round(score_result["savings_per_mw"], 2),
        "payback_years": round(score_result["payback_period_years"], 3),
        "impact_on_opex_pct": round(score_result["impact_on_opex_pct"] * 100, 2),
        "annual_energy_cost": round(score_result["annual_energy_cost"], 2),
        "estimated_savings": round(score_result["estimated_savings"], 2),

        # Data provenance
        "real_inputs": real_inputs,
        "estimated_inputs": estimated_inputs,
        "missing_inputs_note": "baseline_pue, load_growth_rate, energy_pct_opex are estimated from industry averages",
    }

    # EJ impact
    if ej_result:
        result["ej"] = {
            "zip_code": zip_code,
            "state_name": ej_result["state_name"],
            "demographic_index": ej_result["demographic_index"],
            "energy_burden_pct": ej_result["energy_burden_pct"],
            "co2_avoided_metric_tons": round(ej_result["co2_avoided_metric_tons"], 1),
            "co2_avoided_lbs": round(ej_result["co2_avoided_lbs"], 0),
            "water_saved_gallons": round(ej_result["water_saved_gallons"], 0),
            "grid_relief_pct": round(ej_result["grid_relief_pct"], 4),
            "cars_equivalent": round(ej_result["cars_equivalent"], 1),
            "homes_equivalent": round(ej_result["homes_equivalent"], 1),
            "trees_equivalent": round(ej_result["trees_equivalent"], 1),
            "pools_equivalent": round(ej_result["pools_equivalent"], 2),
            "poverty_rate": ej_result["poverty_rate"],
            "people_of_color_pct": ej_result["people_of_color_pct"],
        }
    else:
        result["ej"] = None

    return result


def write_outputs(results):
    """Write JSON and CSV output files."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # --- JSON (full structured output) ---
    json_path = os.path.join(OUTPUT_DIR, "scored_datacenters.json")
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n  JSON: {json_path}")

    # --- CSV (flat table for database import) ---
    csv_path = os.path.join(OUTPUT_DIR, "scored_datacenters.csv")
    fieldnames = [
        "Name", "City", "State", "Operator", "Size_MW", "Latitude", "Longitude",
        "Status", "Electricity_Price", "Composite_Score", "Classification",
        "Savings_Per_MW", "Payback_Years", "OPEX_Impact_Pct",
        "Annual_Energy_Cost", "Estimated_Savings",
        "EJ_Demographic_Index", "EJ_Energy_Burden_Pct",
        "CO2_Avoided_MT", "Water_Saved_Gal", "Grid_Relief_Pct",
        "Cars_Equivalent", "Homes_Equivalent",
        "Missing_Inputs_Note",
    ]

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            ej = r.get("ej") or {}
            writer.writerow({
                "Name": r["name"],
                "City": r["city"],
                "State": r["state"],
                "Operator": r["operator"],
                "Size_MW": r["size_mw"],
                "Latitude": r["latitude"],
                "Longitude": r["longitude"],
                "Status": r["operational_status"],
                "Electricity_Price": r["electricity_price"],
                "Composite_Score": r["composite_score"],
                "Classification": r["classification"],
                "Savings_Per_MW": r["savings_per_mw"],
                "Payback_Years": r["payback_years"],
                "OPEX_Impact_Pct": r["impact_on_opex_pct"],
                "Annual_Energy_Cost": r["annual_energy_cost"],
                "Estimated_Savings": r["estimated_savings"],
                "EJ_Demographic_Index": ej.get("demographic_index", ""),
                "EJ_Energy_Burden_Pct": ej.get("energy_burden_pct", ""),
                "CO2_Avoided_MT": ej.get("co2_avoided_metric_tons", ""),
                "Water_Saved_Gal": ej.get("water_saved_gallons", ""),
                "Grid_Relief_Pct": ej.get("grid_relief_pct", ""),
                "Cars_Equivalent": ej.get("cars_equivalent", ""),
                "Homes_Equivalent": ej.get("homes_equivalent", ""),
                "Missing_Inputs_Note": r["missing_inputs_note"],
            })
    print(f"  CSV:  {csv_path}")


def write_enhanced_missing_inputs(results):
    """Write missing inputs report with sensitivity analysis.

    For each DC, computes the score at low/high bounds for each estimated
    input to show how much uncertainty the estimates introduce.
    """
    # Sensitivity ranges for estimated inputs
    ranges = {
        "baseline_pue":     {"low": 1.20, "default": 1.55, "high": 1.80, "label": "PUE"},
        "load_growth_rate": {"low": 0.05, "default": 0.10, "high": 0.15, "label": "Load Growth"},
        "energy_pct_opex":  {"low": 0.30, "default": 0.40, "high": 0.50, "label": "Energy % OPEX"},
    }

    report_path = os.path.join(OUTPUT_DIR, "missing_inputs_report.csv")
    fieldnames = [
        "Name", "City", "State", "Size_MW",
        "Score_Default", "Classification",
        "Score_Best_Case", "Score_Worst_Case", "Score_Range",
        "Missing_Inputs",
        "PUE_Low_Score", "PUE_High_Score",
        "Growth_Low_Score", "Growth_High_Score",
        "OPEX_Low_Score", "OPEX_High_Score",
        "Data_Collection_Priority",
    ]

    rows = []
    for r in results:
        mw = r["size_mw"]
        price = r["electricity_price"]
        default_score = r["composite_score"]

        # Compute score at each bound
        scores_at_bounds = []
        pue_scores = {}
        growth_scores = {}
        opex_scores = {}

        for pue_val in [ranges["baseline_pue"]["low"], ranges["baseline_pue"]["high"]]:
            for growth_val in [ranges["load_growth_rate"]["low"], ranges["load_growth_rate"]["high"]]:
                for opex_val in [ranges["energy_pct_opex"]["low"], ranges["energy_pct_opex"]["high"]]:
                    s = compute_score(
                        dc_size_mw=mw, baseline_pue=pue_val,
                        electricity_price=price, load_growth_rate=growth_val,
                        energy_pct_opex=opex_val,
                        **VIGILENT_PARAMS,
                    )
                    scores_at_bounds.append(s["composite_score"])

        # Individual parameter sensitivity (vary one, hold others at default)
        for label, param, store in [
            ("pue", "baseline_pue", pue_scores),
            ("growth", "load_growth_rate", growth_scores),
            ("opex", "energy_pct_opex", opex_scores),
        ]:
            for bound in ["low", "high"]:
                kwargs = {
                    "dc_size_mw": mw,
                    "baseline_pue": DEFAULTS["baseline_pue"],
                    "electricity_price": price,
                    "load_growth_rate": DEFAULTS["load_growth_rate"],
                    "energy_pct_opex": DEFAULTS["energy_pct_opex"],
                    **VIGILENT_PARAMS,
                }
                kwargs[param] = ranges[param][bound]
                s = compute_score(**kwargs)
                store[bound] = round(s["composite_score"], 2)

        best = round(max(scores_at_bounds), 2)
        worst = round(min(scores_at_bounds), 2)
        score_range = round(best - worst, 2)

        # Priority: larger range = more value from real data
        if score_range > 15:
            priority = "HIGH"
        elif score_range > 8:
            priority = "MEDIUM"
        else:
            priority = "LOW"

        rows.append({
            "Name": r["name"],
            "City": r["city"],
            "State": r["state"],
            "Size_MW": mw,
            "Score_Default": default_score,
            "Classification": r["classification"],
            "Score_Best_Case": best,
            "Score_Worst_Case": worst,
            "Score_Range": score_range,
            "Missing_Inputs": "baseline_pue; load_growth_rate; energy_pct_opex",
            "PUE_Low_Score": pue_scores["low"],
            "PUE_High_Score": pue_scores["high"],
            "Growth_Low_Score": growth_scores["low"],
            "Growth_High_Score": growth_scores["high"],
            "OPEX_Low_Score": opex_scores["low"],
            "OPEX_High_Score": opex_scores["high"],
            "Data_Collection_Priority": priority,
        })

    # Sort by score range descending (most uncertain first)
    rows.sort(key=lambda x: x["Score_Range"], reverse=True)

    with open(report_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  Missing inputs report: {report_path}")
    print(f"    HIGH priority: {sum(1 for r in rows if r['Data_Collection_Priority'] == 'HIGH')}")
    print(f"    MEDIUM priority: {sum(1 for r in rows if r['Data_Collection_Priority'] == 'MEDIUM')}")
    print(f"    LOW priority: {sum(1 for r in rows if r['Data_Collection_Priority'] == 'LOW')}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("  VIGILENT DATA CENTER SCORING PIPELINE")
    print("=" * 70)

    # --- Load & select ---
    print(f"\nReading CSV: {CSV_PATH}")
    rows = load_csv(CSV_PATH)
    print(f"  Found {len(rows)} data centers")

    if SELECTED_DCS is not None:
        missing = set(SELECTED_DCS) - {r["Name"].strip() for r in rows}
        if missing:
            print(f"  Missing from CSV: {missing}")

    # --- Score each DC ---
    print("\nScoring data centers...")
    results = []
    for row in rows:
        name = row["Name"].strip()
        print(f"\n  [{len(results)+1}] {name}")
        result = score_datacenter(row)
        if result:
            results.append(result)
            print(f"      Score: {result['composite_score']:.1f}/100 ({result['classification']})")
            print(f"      Savings/MW: ${result['savings_per_mw']:,.0f} | Payback: {result['payback_years']:.2f} yr")
            if result.get("ej"):
                ej = result["ej"]
                print(f"      EJ: Demo Index {ej['demographic_index']} | "
                      f"CO2 Avoided: {ej['co2_avoided_metric_tons']:,.0f} MT/yr")
        else:
            print(f"      SKIPPED (invalid MW value)")

    # --- Sort by composite score (descending) ---
    results.sort(key=lambda r: r["composite_score"], reverse=True)

    # --- Output ---
    print("\n" + "=" * 70)
    print("  RESULTS SUMMARY")
    print("=" * 70)
    print(f"\n  {'Name':<20} {'MW':>6} {'Score':>6} {'Class':<12} {'Payback':>8}")
    print("  " + "-" * 58)
    for r in results:
        print(f"  {r['name']:<20} {r['size_mw']:>6.1f} {r['composite_score']:>6.1f} "
              f"{r['classification']:<12} {r['payback_years']:>7.2f}yr")

    # --- Assumptions summary ---
    print(f"\n  ASSUMPTIONS & DATA PROVENANCE")
    print("  " + "-" * 58)
    print("  Real data from CSV:")
    print("    - DC name, city, state, operator, size (MW), lat/lng, status")
    print("  Real data from EIA/GIS:")
    print("    - State-level commercial electricity rates ($/kWh)")
    print("  Estimated (industry averages):")
    print(f"    - Baseline PUE: {DEFAULTS['baseline_pue']} (Uptime Institute 2023)")
    print(f"    - Load Growth Rate: {DEFAULTS['load_growth_rate']*100:.0f}% (industry consensus)")
    print(f"    - Energy % of OPEX: {DEFAULTS['energy_pct_opex']*100:.0f}% (Gartner/McKinsey)")
    print("  Vigilent parameters (standard offering):")
    print(f"    - Investment Cost: ${VIGILENT_PARAMS['investment_cost']:,.0f}")
    print(f"    - Energy Reduction: {VIGILENT_PARAMS['energy_reduction_pct']*100:.0f}%")
    print(f"    - Water Reduction: {VIGILENT_PARAMS['water_reduction_pct']*100:.0f}%")

    # --- Write files ---
    print("\nWriting output files...")
    write_outputs(results)
    write_enhanced_missing_inputs(results)

    print(f"\nDone! {len(results)} data centers scored.\n")
    return results


if __name__ == "__main__":
    main()
