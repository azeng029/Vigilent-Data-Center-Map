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
from operator_tiers import tier_for_operator, opex_pct_for_operator, TIER_OPEX_PCT

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
    "energy_pct_opex": 0.40,    # 40% wholesale-colo midline (operator-tier override applied per-DC; see operator_tiers.py)
    "capacity_factor": 0.70,    # 70% — Uptime Institute / IDC avg IT load utilization (downtime, maintenance, headroom, ramp)
}

# --- State / province commercial electricity rates ($/kWh) ---
# Values for non-US rows mirror the map's commercial electricity rate layers
# (data/EuropeCommercialElectricityRateskWh_13.js,
#  data/CanadaCommercialElectricityRatekWh_8.js,
#  data/BrazilCommercialElectricityRateskWh_3.js).
STATE_ELECTRICITY_RATES = {
    # US (EIA, matches GIS layer)
    "TX": 0.0912, "NY": 0.2254, "CO": 0.1332, "NV": 0.0991, "OH": 0.1155,
    "MA": 0.2340, "CA": 0.2500, "VA": 0.0973, "WA": 0.1100, "OR": 0.1136,
    "MN": 0.1322, "MS": 0.1267, "NJ": 0.1600, "CT": 0.2389,
    "MI": 0.1267,   # CSV lists C Spire Starkville as MI (likely MS typo)

    # Canada (province-level, from CanadaCommercialElectricityRatekWh layer)
    "Newfoundland and Labrador": 0.0794, "Prince Edward Island": 0.1004,
    "Nova Scotia": 0.0895, "New Brunswick": 0.0548, "Quebec": 0.0269,
    "Ontario": 0.1037,    "Manitoba": 0.0286, "Saskatchewan": 0.0548,
    "Alberta": 0.0683,    "British Columbia": 0.0493, "Yukon": 0.1021,
    "Northwest Territories": 0.2052, "Nunavut": 0.4532,

    # Brazil (state-level, from BrazilCommercialElectricityRateskWh layer)
    "Rio Grande do Sul": 0.1557, "Roraima": 0.1215, "Pará": 0.1861,
    "Acre": 0.1652, "Amapá": 0.1614, "Mato Grosso do Sul": 0.1671,
    "Paraná": 0.1215, "Santa Catarina": 0.1329, "Amazonas": 0.1595,
    "Rondônia": 0.1595, "Mato Grosso": 0.1614, "Maranhão": 0.1595,
    "Piauí": 0.1804, "Ceará": 0.1348, "Rio Grande do Norte": 0.1405,
    "Paraíba": 0.1291, "Pernambuco": 0.1462, "Alagoas": 0.1538,
    "Sergipe": 0.1348, "Bahia": 0.1595, "Espírito Santo": 0.15,
    "Rio de Janeiro": 0.1766, "São Paulo": 0.1386, "Goiás": 0.169,
    "Distrito Federal": 0.1576, "Minas Gerais": 0.1633, "Tocantins": 0.1766,
}

# --- Country-level commercial rates ($/kWh), fallback when state/province
# isn't in the table above (Europe country polygons + Asia country averages).
COUNTRY_ELECTRICITY_RATES = {
    # Europe (from EuropeCommercialElectricityRateskWh layer)
    "Norway": 0.085, "Sweden": 0.11, "Germany": 0.21, "Netherlands": 0.19,
    "Croatia": 0.25, "Finland": 0.089, "France": 0.16, "Greece": 0.19,
    "Italy": 0.20, "United Kingdom": 0.34, "Estonia": 0.15, "Latvia": 0.16,
    "Lithuania": 0.18, "Macedonia": 0.15, "Albania": 0.16, "Ireland": 0.30,
    "Austria": 0.21, "Slovenia": 0.16, "Hungary": 0.23, "Kosovo": 0.17,
    "Serbia": 0.14, "Montenegro": 0.094, "Belgium": 0.18, "Switzerland": 0.304,
    "Bosnia and Herz.": 0.13, "Slovakia": 0.19, "Czechia": 0.19,
    "Bulgaria": 0.17, "Romania": 0.18, "Spain": 0.14, "Cyprus": 0.19,
    "Turkey": 0.10, "Malta": 0.15, "Iceland": 0.101, "Denmark": 0.14,
    "Portugal": 0.13, "Luxembourg": 0.20, "Poland": 0.15,

    # Asia / Oceania — no map layer; commercial-tariff averages (public sources)
    "India": 0.10,       # CEA national avg industrial tariff ~₹8.3/kWh
    "Singapore": 0.25,   # EMA non-residential tariff, 2024
    "Thailand": 0.13,    # MEA commercial TOU avg, 2024
    "Japan": 0.20,       # TEPCO commercial tariff, 2024
    "Australia": 0.28,   # AER commercial tariff national avg, 2024
    "New Zealand": 0.18, # Commercial tariff, 2024
    "South Korea": 0.12, # KEPCO commercial, 2024
    "Hong Kong": 0.18,   # CLP/HK Electric commercial
    "Taiwan": 0.14,      # TaiPower commercial

    # Country-name aliases used in CSV rows
    "USA": None,         # US handled via state table — sentinel to skip
    "The Netherlands": 0.19, "NEtherlands": 0.19,
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
    country = row.get("Country", "").strip()
    mw = parse_mw(row.get("Size (MW)", ""))

    if mw is None or mw <= 0:
        return None

    # Resolve electricity price: state/province first, then country, then default.
    elec_price = STATE_ELECTRICITY_RATES.get(state)
    rate_source = "state-level" if elec_price is not None else None
    if elec_price is None:
        elec_price = COUNTRY_ELECTRICITY_RATES.get(country)
        if elec_price is not None:
            rate_source = "country-level"
    if elec_price is None:
        print(f"  WARNING: No electricity rate for '{state}' / '{country}', "
              f"using $0.12/kWh")
        elec_price = 0.12
        rate_source = "default"

    # Track which inputs are real vs. estimated
    real_inputs = ["dc_size_mw", "city", "state"]
    estimated_inputs = []

    if rate_source in ("state-level", "country-level"):
        real_inputs.append(f"electricity_price ({rate_source})")
    else:
        estimated_inputs.append("electricity_price")

    # --- Per-DC OPEX share by operator tier ---
    operator_name = row.get("Operator", "").strip()
    op_tier = tier_for_operator(operator_name)
    energy_pct_opex = opex_pct_for_operator(operator_name)

    estimated_inputs.extend([
        "baseline_pue (industry avg 1.55)",
        "load_growth_rate (industry est 10%)",
        f"energy_pct_opex (operator-tier {op_tier}: {energy_pct_opex:.0%})",
        f"capacity_factor (industry avg {DEFAULTS['capacity_factor']:.0%})",
    ])

    # --- Run composite scoring model ---
    score_result = compute_score(
        dc_size_mw=mw,
        baseline_pue=DEFAULTS["baseline_pue"],
        electricity_price=elec_price,
        load_growth_rate=DEFAULTS["load_growth_rate"],
        energy_pct_opex=energy_pct_opex,
        capacity_factor=DEFAULTS["capacity_factor"],
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
            capacity_factor=DEFAULTS["capacity_factor"],
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
        "energy_pct_opex": energy_pct_opex,
        "capacity_factor": DEFAULTS["capacity_factor"],
        "operator_tier": op_tier,

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
        "missing_inputs_note": "baseline_pue & load_growth_rate from industry averages; energy_pct_opex from operator-tier benchmark; capacity_factor from Uptime Institute / IDC avg",
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

        # Use this DC's operator-tier OPEX as the default for sensitivity sweeps
        dc_default_opex = r.get("energy_pct_opex", DEFAULTS["energy_pct_opex"])

        for pue_val in [ranges["baseline_pue"]["low"], ranges["baseline_pue"]["high"]]:
            for growth_val in [ranges["load_growth_rate"]["low"], ranges["load_growth_rate"]["high"]]:
                for opex_val in [ranges["energy_pct_opex"]["low"], ranges["energy_pct_opex"]["high"]]:
                    s = compute_score(
                        dc_size_mw=mw, baseline_pue=pue_val,
                        electricity_price=price, load_growth_rate=growth_val,
                        energy_pct_opex=opex_val,
                        capacity_factor=DEFAULTS["capacity_factor"],
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
                    "energy_pct_opex": dc_default_opex,
                    "capacity_factor": DEFAULTS["capacity_factor"],
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
    print(f"    - Capacity Factor: {DEFAULTS['capacity_factor']*100:.0f}% (Uptime Institute / IDC avg utilization)")
    print(f"    - Energy % of OPEX (operator-tier benchmark):")
    for tier, pct in TIER_OPEX_PCT.items():
        print(f"        {tier:<15} {pct*100:.0f}%")
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
