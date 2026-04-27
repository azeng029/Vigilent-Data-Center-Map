"""
Vigilent Compute Engine
========================
Shared scoring model imported by simulation.py and optimizer.py.
Replicates formulas from Vigilent Calculator (2).xlsx with the
inverted payback fix.

No framework dependencies — pure Python + numpy.
"""

import numpy as np
from typing import Dict, Any, List, Tuple, Optional

# ═══════════════════════════════════════════════════════════════════════════════
# SCORING CONFIGURATION (from spreadsheet)
# ═══════════════════════════════════════════════════════════════════════════════

SCORING_CONFIG = {
    "savings_per_mw":    {"min": 0, "max": 300_000, "weight": 0.35},
    "water_savings_pct": {"min": 0, "max": 0.08,    "weight": 0.10},
    "impact_on_opex":    {"min": 0, "max": 0.10,    "weight": 0.20},
    "payback_period":    {"min": 0, "max": 5.0,     "weight": 0.25},   # INVERTED
    "load_growth":       {"min": 0, "max": 0.15,    "weight": 0.10},
}

# ═══════════════════════════════════════════════════════════════════════════════
# PARAMETER BOUNDS (industry-sourced)
# ═══════════════════════════════════════════════════════════════════════════════

# Non-Vigilent (data center criteria) — used for axes, sliders, optimization
DC_PARAMS = {
    "dc_size_mw": {
        "label": "DC Size (MW)",
        "min": 1, "max": 200, "default": 20, "step": 1,
        "fmt": ".0f", "unit": "MW",
    },
    "baseline_pue": {
        "label": "Baseline PUE",
        "min": 1.0, "max": 2.5, "default": 1.55, "step": 0.01,
        "fmt": ".2f", "unit": "",
    },
    "electricity_price": {
        "label": "Electricity Price ($/kWh)",
        "min": 0.01, "max": 0.50, "default": 0.10, "step": 0.01,
        "fmt": ".2f", "unit": "$/kWh",
    },
    "load_growth_rate": {
        "label": "Load Growth Rate (%)",
        "min": 0.0, "max": 0.30, "default": 0.10, "step": 0.01,
        "fmt": ".0%", "unit": "",
        "display_mult": 100,  # show as percentage in UI
    },
    "energy_pct_opex": {
        "label": "Energy % of OPEX",
        "min": 0.05, "max": 0.80, "default": 0.40, "step": 0.01,
        "fmt": ".0%", "unit": "",
        "display_mult": 100,
    },
    "capacity_factor": {
        "label": "Capacity Factor (avg utilization)",
        "min": 0.40, "max": 1.00, "default": 0.70, "step": 0.01,
        "fmt": ".0%", "unit": "",
        "display_mult": 100,
    },
}

# Vigilent criteria — separate input box
VIGILENT_PARAMS = {
    "num_years": {
        "label": "Number of Years",
        "min": 1, "max": 10, "default": 1, "step": 1,
        "fmt": "d", "unit": "yr",
    },
    "investment_cost": {
        "label": "Vigilent Investment Cost ($)",
        "min": 10_000, "max": 50_000_000, "default": 1_500_000, "step": 10_000,
        "fmt": ",.0f", "unit": "$",
    },
    "energy_reduction_pct": {
        "label": "Energy Reduction w/ Vigilent (%)",
        "min": 0.01, "max": 0.40, "default": 0.10, "step": 0.01,
        "fmt": ".0%", "unit": "",
        "display_mult": 100,
    },
    "water_reduction_pct": {
        "label": "Water Reduction w/ Vigilent (%)",
        "min": 0.01, "max": 0.15, "default": 0.05, "step": 0.01,
        "fmt": ".0%", "unit": "",
        "display_mult": 100,
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# CORE COMPUTE
# ═══════════════════════════════════════════════════════════════════════════════

def compute_score(
    dc_size_mw: float,
    baseline_pue: float,
    electricity_price: float,
    load_growth_rate: float,
    energy_pct_opex: float,
    investment_cost: float,
    energy_reduction_pct: float,
    water_reduction_pct: float,
    num_years: int = 1,
    capacity_factor: float = 0.70,
    scoring_config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Run the full Vigilent savings & scoring model.

    scoring_config: optional override for SCORING_CONFIG (weights + max thresholds).
                    If None, uses the module-level SCORING_CONFIG defaults.

    Returns dict with:
        composite_score, factor_scores (dict),
        annual_energy_kwh, annual_energy_cost, estimated_savings,
        savings_per_mw, payback_period_years, impact_on_opex_pct,
        water_savings_pct
    """
    cfg = scoring_config if scoring_config is not None else SCORING_CONFIG

    # --- Energy ---
    # capacity_factor accounts for real-world IT load utilization (downtime,
    # maintenance, redundancy headroom, growth ramp). 100% = always full load.
    total_mw = dc_size_mw * baseline_pue
    annual_energy_kwh = total_mw * 1000 * 8760 * capacity_factor * (1 + load_growth_rate)
    annual_energy_cost = annual_energy_kwh * electricity_price

    # --- Savings ---
    estimated_savings = annual_energy_cost * energy_reduction_pct

    # --- Derived scoring inputs ---
    savings_per_mw = estimated_savings / dc_size_mw if dc_size_mw > 0 else 0
    payback_period = (investment_cost / estimated_savings
                      if estimated_savings > 0 else 999)
    # OPEX impact simplifies algebraically to:
    impact_on_opex_pct = energy_reduction_pct * energy_pct_opex

    # --- Scoring ---
    def _score(value, cfg_key):
        mn = cfg[cfg_key].get("min", 0)
        mx = cfg[cfg_key]["max"]
        span = mx - mn
        if span <= 0:
            return 0
        raw = ((value - mn) / span) * 100
        return max(min(raw, 100), 0)

    def _score_inv(value, cfg_key):
        mn = cfg[cfg_key].get("min", 0)
        mx = cfg[cfg_key]["max"]
        span = mx - mn
        if span <= 0:
            return 0
        raw = (1 - (value - mn) / span) * 100
        return max(min(raw, 100), 0)

    factor_scores = {
        "savings_per_mw":    _score(savings_per_mw, "savings_per_mw"),
        "water_savings_pct": _score(water_reduction_pct, "water_savings_pct"),
        "impact_on_opex":    _score(impact_on_opex_pct, "impact_on_opex"),
        "payback_period":    _score_inv(payback_period, "payback_period"),
        "load_growth":       _score(load_growth_rate, "load_growth"),
    }

    composite = sum(
        factor_scores[k] * cfg[k]["weight"]
        for k in factor_scores
    )

    return {
        "composite_score": composite,
        "factor_scores": factor_scores,
        "annual_energy_kwh": annual_energy_kwh,
        "annual_energy_cost": annual_energy_cost,
        "estimated_savings": estimated_savings,
        "savings_per_mw": savings_per_mw,
        "payback_period_years": payback_period,
        "impact_on_opex_pct": impact_on_opex_pct,
        "water_savings_pct": water_reduction_pct,
    }


def compute_score_grid(
    x_param: str,
    x_values: np.ndarray,
    y_param: str,
    y_values: np.ndarray,
    fixed_params: Dict[str, float],
    scoring_config: Optional[Dict] = None,
) -> np.ndarray:
    """
    Compute a 2D grid of composite scores by sweeping two parameters.

    x_values: 1D array for X axis
    y_values: 1D array for Y axis
    fixed_params: dict of all other params held constant
        Must include all keys from DC_PARAMS + VIGILENT_PARAMS minus the two axis params.
    scoring_config: optional override for SCORING_CONFIG (weights + max thresholds).

    Returns: 2D numpy array of shape (len(y_values), len(x_values))
             with composite scores [0-100].
    """
    all_param_names = [
        "dc_size_mw", "baseline_pue", "electricity_price",
        "load_growth_rate", "energy_pct_opex", "capacity_factor",
        "investment_cost", "energy_reduction_pct", "water_reduction_pct",
        "num_years",
    ]

    Z = np.zeros((len(y_values), len(x_values)))

    for i, yv in enumerate(y_values):
        for j, xv in enumerate(x_values):
            params = dict(fixed_params)
            params[x_param] = xv
            params[y_param] = yv
            # Ensure all params present
            for p in all_param_names:
                if p not in params:
                    # Fall back to defaults
                    if p in DC_PARAMS:
                        params[p] = DC_PARAMS[p]["default"]
                    elif p in VIGILENT_PARAMS:
                        params[p] = VIGILENT_PARAMS[p]["default"]
            if scoring_config is not None:
                params["scoring_config"] = scoring_config
            result = compute_score(**params)
            Z[i, j] = result["composite_score"]

    return Z


# ═══════════════════════════════════════════════════════════════════════════════
# EXHAUSTIVE MULTI-VARIABLE SWEEP (DC FINDER)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_exhaustive_sweep(
    vigilent_params: Dict[str, float],
    steps: int = 15,
    capacity_factor: float = 0.70,
    scoring_config: Optional[Dict] = None,
) -> tuple:
    """
    Sweep all 5 DC params simultaneously using vectorized numpy broadcasting.

    vigilent_params: dict with keys investment_cost, energy_reduction_pct,
                     water_reduction_pct (set by user, held fixed).
    steps: number of grid points per DC param (default 15 → 759K combos).

    Returns:
        composite: 5D numpy array of shape (steps, steps, steps, steps, steps)
                   with composite scores [0-100].
                   Axes: (dc_size, pue, price, growth, opex)
        grids: dict of 1D arrays for each DC param.
    """
    cfg = scoring_config if scoring_config is not None else SCORING_CONFIG

    # 1D grids for each DC param
    dc_size = np.linspace(DC_PARAMS["dc_size_mw"]["min"],
                          DC_PARAMS["dc_size_mw"]["max"], steps)
    pue = np.linspace(DC_PARAMS["baseline_pue"]["min"],
                      DC_PARAMS["baseline_pue"]["max"], steps)
    price = np.linspace(DC_PARAMS["electricity_price"]["min"],
                        DC_PARAMS["electricity_price"]["max"], steps)
    growth = np.linspace(DC_PARAMS["load_growth_rate"]["min"],
                         DC_PARAMS["load_growth_rate"]["max"], steps)
    opex = np.linspace(DC_PARAMS["energy_pct_opex"]["min"],
                       DC_PARAMS["energy_pct_opex"]["max"], steps)

    # Reshape for 5D broadcasting: (dc, pue, price, growth, opex)
    D = dc_size[:, None, None, None, None]
    P = pue[None, :, None, None, None]
    E = price[None, None, :, None, None]
    G = growth[None, None, None, :, None]
    O = opex[None, None, None, None, :]

    # Fixed Vigilent params
    e_red = vigilent_params["energy_reduction_pct"]
    w_red = vigilent_params["water_reduction_pct"]
    inv = vigilent_params["investment_cost"]

    # Vectorized scoring — same math as compute_score() lines 118-163
    savings_per_mw = P * 1000 * 8760 * capacity_factor * (1 + G) * E * e_red
    payback = np.where(savings_per_mw > 0,
                       inv / (D * savings_per_mw), 999.0)
    impact_on_opex = e_red * O

    # Score each factor 0-100
    spm_max = cfg["savings_per_mw"]["max"]
    pp_max = cfg["payback_period"]["max"]
    opex_max = cfg["impact_on_opex"]["max"]
    water_max = cfg["water_savings_pct"]["max"]
    growth_max = cfg["load_growth"]["max"]

    spm_score = np.clip(savings_per_mw / spm_max * 100, 0, 100)
    pp_score = np.clip((1 - payback / pp_max) * 100, 0, 100)
    opex_score = np.clip(impact_on_opex / opex_max * 100, 0, 100)
    water_score = float(min(w_red / water_max * 100, 100))  # scalar
    growth_score = np.clip(G / growth_max * 100, 0, 100)

    composite = (spm_score * cfg["savings_per_mw"]["weight"]
                 + pp_score * cfg["payback_period"]["weight"]
                 + opex_score * cfg["impact_on_opex"]["weight"]
                 + water_score * cfg["water_savings_pct"]["weight"]
                 + growth_score * cfg["load_growth"]["weight"])

    grids = {
        "dc_size_mw": dc_size,
        "baseline_pue": pue,
        "electricity_price": price,
        "load_growth_rate": growth,
        "energy_pct_opex": opex,
    }
    return composite, grids


# Axis index mapping for the 5D array
_FINDER_AXES = {
    "dc_size_mw": 0,
    "baseline_pue": 1,
    "electricity_price": 2,
    "load_growth_rate": 3,
    "energy_pct_opex": 4,
}


def extract_target_ranges(
    composite: np.ndarray,
    grids: Dict[str, np.ndarray],
    threshold: float,
) -> tuple:
    """
    From 5D score array, extract per-param ranges where score >= threshold.

    Returns:
        ranges: dict keyed by param name → {min, max, pct_of_range}
        feasibility_pct: % of all combos that pass
        total_passing: number of passing combos
        total_combos: total combos tested
    """
    passing = composite >= threshold
    total_passing = int(np.sum(passing))
    total_combos = passing.size
    feasibility_pct = total_passing / total_combos * 100

    ranges = {}
    for name, axis_idx in _FINDER_AXES.items():
        other_axes = tuple(i for i in range(5) if i != axis_idx)
        valid_mask = np.any(passing, axis=other_axes)
        valid_vals = grids[name][valid_mask]
        if len(valid_vals) > 0:
            ranges[name] = {
                "min": float(valid_vals.min()),
                "max": float(valid_vals.max()),
                "pct_of_range": float(np.sum(valid_mask) / len(grids[name]) * 100),
            }
        else:
            ranges[name] = {
                "min": None, "max": None, "pct_of_range": 0.0,
            }

    return ranges, feasibility_pct, total_passing, total_combos


def compute_pairwise_tradeoff(
    composite: np.ndarray,
    param_a: str,
    param_b: str,
    threshold: float,
) -> np.ndarray:
    """
    For a pair of DC params, compute % of other-param combos that pass.

    Returns 2D array of shape (steps_a, steps_b) with values 0-100.
    """
    ax_a = _FINDER_AXES[param_a]
    ax_b = _FINDER_AXES[param_b]
    passing = composite >= threshold
    other_axes = tuple(i for i in range(5) if i not in (ax_a, ax_b))
    # Mean across other axes gives fraction passing, then × 100 for %
    result = np.mean(passing, axis=other_axes) * 100
    # Ensure axis order is (a, b) — numpy reduces in sorted order
    if ax_a > ax_b:
        result = result.T
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# OPENEI UTILITY RATE LOOKUP
# ═══════════════════════════════════════════════════════════════════════════════

def lookup_electricity_rate(zip_code: str) -> List[Dict[str, Any]]:
    """
    Fetch commercial electricity rates from OpenEI for a given zip code.
    Returns list of dicts: [{utility, rate_name, blended_rate_per_kwh}, ...]
    """
    import requests

    url = "https://api.openei.org/utility_rates"
    params = {
        "version": "latest",
        "format": "json",
        "api_key": "DEMO_KEY",
        "address": zip_code,
        "sector": "Commercial",
        "detail": "full",
        "limit": 10,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return [{"utility": "Error", "rate_name": str(e), "blended_rate_per_kwh": None}]

    items = data.get("items", [])
    if not items:
        return [{"utility": "No rates found", "rate_name": f"for zip {zip_code}",
                 "blended_rate_per_kwh": None}]

    results = []
    for item in items:
        utility = item.get("utility", "Unknown")
        rate_name = item.get("name", "Unknown")
        rate_struct = item.get("energyratestructure", [])

        # Compute blended rate: weighted average across all tiers/periods
        # using weekday schedule frequency as weights
        weekday_sched = item.get("energyweekdayschedule", [])
        weekend_sched = item.get("energyweekendschedule", [])

        if rate_struct:
            blended = _compute_blended_rate(rate_struct, weekday_sched, weekend_sched)
        else:
            blended = None

        if blended is not None and blended > 0:
            results.append({
                "utility": utility,
                "rate_name": rate_name,
                "blended_rate_per_kwh": round(blended, 4),
            })

    # Sort by rate, lowest first
    results.sort(key=lambda r: r.get("blended_rate_per_kwh") or 999)
    return results if results else [
        {"utility": "No parseable rates", "rate_name": f"for zip {zip_code}",
         "blended_rate_per_kwh": None}
    ]


def _compute_blended_rate(
    rate_struct: list,
    weekday_sched: list,
    weekend_sched: list,
) -> Optional[float]:
    """
    Compute a single blended $/kWh from OpenEI rate structure.

    rate_struct: list of tiers, each tier is a list of dicts with 'rate' key
    weekday_sched: 12x24 matrix of tier indices for weekdays
    weekend_sched: 12x24 matrix of tier indices for weekends

    Weights each tier by the number of hours it appears in the schedule
    (5 weekdays + 2 weekend days per week).
    """
    # Extract the rate for each tier (first entry in each tier list)
    tier_rates = []
    for tier in rate_struct:
        if isinstance(tier, list) and len(tier) > 0 and isinstance(tier[0], dict):
            rate = tier[0].get("rate", 0)
            tier_rates.append(rate if rate else 0)
        else:
            tier_rates.append(0)

    if not tier_rates or all(r == 0 for r in tier_rates):
        return None

    # If no schedule, just average the tier rates
    if not weekday_sched or not weekend_sched:
        return sum(tier_rates) / len(tier_rates)

    # Count hours per tier across the year
    tier_hours = [0.0] * len(tier_rates)
    try:
        for month_idx in range(12):
            days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month_idx]
            weekdays = int(days_in_month * 5 / 7)
            weekends = days_in_month - weekdays

            for hour in range(24):
                wd_tier = weekday_sched[month_idx][hour] if month_idx < len(weekday_sched) else 0
                we_tier = weekend_sched[month_idx][hour] if month_idx < len(weekend_sched) else 0

                if wd_tier < len(tier_hours):
                    tier_hours[wd_tier] += weekdays
                if we_tier < len(tier_hours):
                    tier_hours[we_tier] += weekends
    except (IndexError, TypeError):
        return sum(tier_rates) / len(tier_rates)

    total_hours = sum(tier_hours)
    if total_hours == 0:
        return sum(tier_rates) / len(tier_rates)

    blended = sum(r * h for r, h in zip(tier_rates, tier_hours)) / total_hours
    return blended


# ═══════════════════════════════════════════════════════════════════════════════
# ENVIRONMENTAL JUSTICE DATA & COMPUTE
# ═══════════════════════════════════════════════════════════════════════════════

# --- EPA eGRID2022: CO2 emission rates by subregion (lbs CO2 / MWh) ---
EGRID_CO2_LBS_PER_MWH = {
    "AKGD": 1015.5, "AKMS": 337.1, "AZNM": 815.6, "CAMX": 531.0,
    "ERCT": 816.9, "FRCC": 821.4, "HIMS": 1245.2, "HIOA": 1534.5,
    "MROE": 1344.8, "MROW": 1002.5, "NEWE": 456.3, "NWPP": 643.0,
    "NYCW": 578.0, "NYLI": 1094.6, "NYUP": 305.6, "PRMS": 1482.1,
    "RFCE": 718.3, "RFCM": 1439.5, "RFCW": 1155.2, "RMPA": 1237.9,
    "SPNO": 1242.2, "SPSO": 984.5, "SRMW": 1611.8, "SRMV": 774.6,
    "SRSO": 905.1, "SRTV": 817.1, "SRVC": 647.3,
}

# --- State → primary eGRID subregion ---
STATE_TO_EGRID = {
    "AL": "SRSO", "AK": "AKGD", "AZ": "AZNM", "AR": "SRMV", "CA": "CAMX",
    "CO": "RMPA", "CT": "NEWE", "DE": "RFCE", "FL": "FRCC", "GA": "SRSO",
    "HI": "HIOA", "ID": "NWPP", "IL": "RFCW", "IN": "RFCW", "IA": "MROW",
    "KS": "SPNO", "KY": "SRTV", "LA": "SRMV", "ME": "NEWE", "MD": "RFCE",
    "MA": "NEWE", "MI": "RFCM", "MN": "MROW", "MS": "SRMV", "MO": "SRMW",
    "MT": "NWPP", "NE": "MROW", "NV": "NWPP", "NH": "NEWE", "NJ": "RFCE",
    "NM": "AZNM", "NY": "NYCW", "NC": "SRVC", "ND": "MROW", "OH": "RFCW",
    "OK": "SPSO", "OR": "NWPP", "PA": "RFCE", "RI": "NEWE", "SC": "SRVC",
    "SD": "MROW", "TN": "SRTV", "TX": "ERCT", "UT": "NWPP", "VT": "NEWE",
    "VA": "SRVC", "WA": "NWPP", "WV": "RFCW", "WI": "MROE", "WY": "RMPA",
    "DC": "RFCE",
}

# --- eGRID fuel mix (% of generation by fuel, per subregion) ---
# Source: EPA eGRID2022
EGRID_FUEL_MIX = {
    "AKGD": {"coal": 0.08, "gas": 0.60, "nuclear": 0.00, "hydro": 0.15, "wind": 0.05, "solar": 0.01, "other": 0.11},
    "AKMS": {"coal": 0.00, "gas": 0.10, "nuclear": 0.00, "hydro": 0.70, "wind": 0.10, "solar": 0.00, "other": 0.10},
    "AZNM": {"coal": 0.10, "gas": 0.38, "nuclear": 0.17, "hydro": 0.06, "wind": 0.10, "solar": 0.14, "other": 0.05},
    "CAMX": {"coal": 0.003, "gas": 0.477, "nuclear": 0.092, "hydro": 0.117, "wind": 0.083, "solar": 0.187, "other": 0.041},
    "ERCT": {"coal": 0.16, "gas": 0.42, "nuclear": 0.10, "hydro": 0.00, "wind": 0.25, "solar": 0.05, "other": 0.02},
    "FRCC": {"coal": 0.06, "gas": 0.68, "nuclear": 0.12, "hydro": 0.00, "wind": 0.00, "solar": 0.07, "other": 0.07},
    "HIMS": {"coal": 0.12, "gas": 0.00, "nuclear": 0.00, "hydro": 0.05, "wind": 0.25, "solar": 0.45, "other": 0.13},
    "HIOA": {"coal": 0.10, "gas": 0.00, "nuclear": 0.00, "hydro": 0.01, "wind": 0.10, "solar": 0.35, "other": 0.44},
    "MROE": {"coal": 0.45, "gas": 0.12, "nuclear": 0.20, "hydro": 0.02, "wind": 0.12, "solar": 0.02, "other": 0.07},
    "MROW": {"coal": 0.28, "gas": 0.10, "nuclear": 0.10, "hydro": 0.04, "wind": 0.35, "solar": 0.05, "other": 0.08},
    "NEWE": {"coal": 0.01, "gas": 0.48, "nuclear": 0.25, "hydro": 0.07, "wind": 0.06, "solar": 0.07, "other": 0.06},
    "NWPP": {"coal": 0.08, "gas": 0.18, "nuclear": 0.04, "hydro": 0.45, "wind": 0.14, "solar": 0.05, "other": 0.06},
    "NYCW": {"coal": 0.00, "gas": 0.58, "nuclear": 0.25, "hydro": 0.01, "wind": 0.05, "solar": 0.05, "other": 0.06},
    "NYLI": {"coal": 0.00, "gas": 0.80, "nuclear": 0.00, "hydro": 0.00, "wind": 0.10, "solar": 0.05, "other": 0.05},
    "NYUP": {"coal": 0.01, "gas": 0.20, "nuclear": 0.30, "hydro": 0.35, "wind": 0.08, "solar": 0.03, "other": 0.03},
    "PRMS": {"coal": 0.15, "gas": 0.45, "nuclear": 0.00, "hydro": 0.02, "wind": 0.05, "solar": 0.20, "other": 0.13},
    "RFCE": {"coal": 0.08, "gas": 0.40, "nuclear": 0.35, "hydro": 0.02, "wind": 0.04, "solar": 0.04, "other": 0.07},
    "RFCM": {"coal": 0.38, "gas": 0.25, "nuclear": 0.20, "hydro": 0.01, "wind": 0.08, "solar": 0.02, "other": 0.06},
    "RFCW": {"coal": 0.30, "gas": 0.25, "nuclear": 0.25, "hydro": 0.01, "wind": 0.10, "solar": 0.03, "other": 0.06},
    "RMPA": {"coal": 0.35, "gas": 0.22, "nuclear": 0.00, "hydro": 0.04, "wind": 0.28, "solar": 0.06, "other": 0.05},
    "SPNO": {"coal": 0.30, "gas": 0.15, "nuclear": 0.15, "hydro": 0.01, "wind": 0.30, "solar": 0.04, "other": 0.05},
    "SPSO": {"coal": 0.20, "gas": 0.40, "nuclear": 0.10, "hydro": 0.02, "wind": 0.20, "solar": 0.04, "other": 0.04},
    "SRMW": {"coal": 0.60, "gas": 0.08, "nuclear": 0.15, "hydro": 0.02, "wind": 0.08, "solar": 0.02, "other": 0.05},
    "SRMV": {"coal": 0.08, "gas": 0.55, "nuclear": 0.20, "hydro": 0.02, "wind": 0.03, "solar": 0.04, "other": 0.08},
    "SRSO": {"coal": 0.18, "gas": 0.42, "nuclear": 0.22, "hydro": 0.05, "wind": 0.02, "solar": 0.05, "other": 0.06},
    "SRTV": {"coal": 0.15, "gas": 0.25, "nuclear": 0.30, "hydro": 0.15, "wind": 0.04, "solar": 0.04, "other": 0.07},
    "SRVC": {"coal": 0.07, "gas": 0.38, "nuclear": 0.34, "hydro": 0.02, "wind": 0.03, "solar": 0.10, "other": 0.06},
}

# --- Water intensity by fuel type (gallons/MWh for cooling) ---
# Source: NREL, USGS, Macknick et al. (2012)
WATER_GAL_PER_MWH = {
    "coal": 12_000,
    "gas": 2_800,
    "nuclear": 13_000,
    "hydro": 0,         # no consumptive use for cooling
    "wind": 0,
    "solar": 26,        # PV — minimal cleaning water
    "other": 5_000,     # biomass/geothermal average
}

# --- State peak demand (MW) — EIA data ---
STATE_PEAK_DEMAND_MW = {
    "AL": 14_600, "AK": 1_100, "AZ": 20_300, "AR": 8_400, "CA": 52_061,
    "CO": 11_400, "CT": 7_200, "DE": 2_800, "FL": 47_000, "GA": 18_200,
    "HI": 1_800, "ID": 4_200, "IL": 39_000, "IN": 18_000, "IA": 8_800,
    "KS": 8_200, "KY": 12_000, "LA": 15_200, "ME": 2_700, "MD": 13_500,
    "MA": 13_200, "MI": 22_000, "MN": 13_500, "MS": 8_100, "MO": 15_000,
    "MT": 3_200, "NE": 5_700, "NV": 9_600, "NH": 2_600, "NJ": 18_500,
    "NM": 5_200, "NY": 32_075, "NC": 22_000, "ND": 3_100, "OH": 28_000,
    "OK": 14_000, "OR": 9_200, "PA": 30_000, "RI": 1_900, "SC": 11_500,
    "SD": 2_700, "TN": 18_500, "TX": 74_897, "UT": 7_800, "VT": 1_000,
    "VA": 22_000, "WA": 22_000, "WV": 6_500, "WI": 12_000, "WY": 3_200,
    "DC": 2_500,
}

# --- 3-digit zip prefix → state code ---
# Covers all US 3-digit zip prefixes
ZIP3_TO_STATE = {
    # PR
    "006": "PR", "007": "PR", "008": "PR", "009": "PR",
    # MA
    "010": "MA", "011": "MA", "012": "MA", "013": "MA", "014": "MA",
    "015": "MA", "016": "MA", "017": "MA", "018": "MA", "019": "MA",
    "020": "MA", "021": "MA", "022": "MA", "023": "MA", "024": "MA",
    "025": "MA", "026": "MA", "027": "MA",
    # RI
    "028": "RI", "029": "RI",
    # NH
    "030": "NH", "031": "NH", "032": "NH", "033": "NH", "034": "NH",
    "035": "NH", "036": "NH", "037": "NH", "038": "NH",
    # ME
    "039": "ME", "040": "ME", "041": "ME", "042": "ME", "043": "ME",
    "044": "ME", "045": "ME", "046": "ME", "047": "ME", "048": "ME",
    "049": "ME",
    # VT
    "050": "VT", "051": "VT", "052": "VT", "053": "VT", "054": "VT",
    "056": "VT", "057": "VT", "058": "VT", "059": "VT",
    # CT
    "060": "CT", "061": "CT", "062": "CT", "063": "CT", "064": "CT",
    "065": "CT", "066": "CT", "067": "CT", "068": "CT", "069": "CT",
    # NJ
    "070": "NJ", "071": "NJ", "072": "NJ", "073": "NJ", "074": "NJ",
    "075": "NJ", "076": "NJ", "077": "NJ", "078": "NJ", "079": "NJ",
    "080": "NJ", "081": "NJ", "082": "NJ", "083": "NJ", "084": "NJ",
    "085": "NJ", "086": "NJ", "087": "NJ", "088": "NJ", "089": "NJ",
    # NY
    "100": "NY", "101": "NY", "102": "NY", "103": "NY", "104": "NY",
    "105": "NY", "106": "NY", "107": "NY", "108": "NY", "109": "NY",
    "110": "NY", "111": "NY", "112": "NY", "113": "NY", "114": "NY",
    "115": "NY", "116": "NY", "117": "NY", "118": "NY", "119": "NY",
    "120": "NY", "121": "NY", "122": "NY", "123": "NY", "124": "NY",
    "125": "NY", "126": "NY", "127": "NY", "128": "NY", "129": "NY",
    "130": "NY", "131": "NY", "132": "NY", "133": "NY", "134": "NY",
    "135": "NY", "136": "NY", "137": "NY", "138": "NY", "139": "NY",
    "140": "NY", "141": "NY", "142": "NY", "143": "NY", "144": "NY",
    "145": "NY", "146": "NY", "147": "NY", "148": "NY", "149": "NY",
    # PA
    "150": "PA", "151": "PA", "152": "PA", "153": "PA", "154": "PA",
    "155": "PA", "156": "PA", "157": "PA", "158": "PA", "159": "PA",
    "160": "PA", "161": "PA", "162": "PA", "163": "PA", "164": "PA",
    "165": "PA", "166": "PA", "167": "PA", "168": "PA", "169": "PA",
    "170": "PA", "171": "PA", "172": "PA", "173": "PA", "174": "PA",
    "175": "PA", "176": "PA", "177": "PA", "178": "PA", "179": "PA",
    "180": "PA", "181": "PA", "182": "PA", "183": "PA", "184": "PA",
    "185": "PA", "186": "PA", "187": "PA", "188": "PA", "189": "PA",
    "190": "PA", "191": "PA", "192": "PA", "193": "PA", "194": "PA",
    "195": "PA", "196": "PA",
    # DE
    "197": "DE", "198": "DE", "199": "DE",
    # DC / Northern VA (200,202-205=DC; 201,206-209=VA suburbs including Ashburn)
    "200": "DC", "201": "VA", "202": "DC", "203": "DC", "204": "DC", "205": "DC",
    "206": "VA", "207": "VA", "208": "VA", "209": "VA",
    # VA
    "220": "VA", "221": "VA", "222": "VA", "223": "VA", "224": "VA",
    "225": "VA", "226": "VA", "227": "VA", "228": "VA", "229": "VA",
    "230": "VA", "231": "VA", "232": "VA", "233": "VA", "234": "VA",
    "235": "VA", "236": "VA", "237": "VA", "238": "VA", "239": "VA",
    "240": "VA", "241": "VA", "242": "VA", "243": "VA", "244": "VA",
    "245": "VA", "246": "VA",
    # WV
    "247": "WV", "248": "WV", "249": "WV", "250": "WV", "251": "WV",
    "252": "WV", "253": "WV", "254": "WV", "255": "WV", "256": "WV",
    "257": "WV", "258": "WV", "259": "WV", "260": "WV", "261": "WV",
    "262": "WV", "263": "WV", "264": "WV", "265": "WV", "266": "WV",
    "267": "WV", "268": "WV",
    # NC
    "270": "NC", "271": "NC", "272": "NC", "273": "NC", "274": "NC",
    "275": "NC", "276": "NC", "277": "NC", "278": "NC", "279": "NC",
    "280": "NC", "281": "NC", "282": "NC", "283": "NC", "284": "NC",
    "285": "NC", "286": "NC", "287": "NC", "288": "NC", "289": "NC",
    # SC
    "290": "SC", "291": "SC", "292": "SC", "293": "SC", "294": "SC",
    "295": "SC", "296": "SC", "297": "SC", "298": "SC", "299": "SC",
    # GA
    "300": "GA", "301": "GA", "302": "GA", "303": "GA", "304": "GA",
    "305": "GA", "306": "GA", "307": "GA", "308": "GA", "309": "GA",
    "310": "GA", "311": "GA", "312": "GA", "313": "GA", "314": "GA",
    "315": "GA", "316": "GA", "317": "GA", "318": "GA", "319": "GA",
    # FL
    "320": "FL", "321": "FL", "322": "FL", "323": "FL", "324": "FL",
    "325": "FL", "326": "FL", "327": "FL", "328": "FL", "329": "FL",
    "330": "FL", "331": "FL", "332": "FL", "333": "FL", "334": "FL",
    "335": "FL", "336": "FL", "337": "FL", "338": "FL", "339": "FL",
    "340": "FL", "341": "FL", "342": "FL", "344": "FL", "346": "FL",
    "347": "FL", "349": "FL",
    # AL
    "350": "AL", "351": "AL", "352": "AL", "353": "AL", "354": "AL",
    "355": "AL", "356": "AL", "357": "AL", "358": "AL", "359": "AL",
    "360": "AL", "361": "AL", "362": "AL", "363": "AL", "364": "AL",
    "365": "AL", "366": "AL", "367": "AL", "368": "AL", "369": "AL",
    # TN
    "370": "TN", "371": "TN", "372": "TN", "373": "TN", "374": "TN",
    "375": "TN", "376": "TN", "377": "TN", "378": "TN", "379": "TN",
    "380": "TN", "381": "TN", "382": "TN", "383": "TN", "384": "TN",
    "385": "TN",
    # MS
    "386": "MS", "387": "MS", "388": "MS", "389": "MS", "390": "MS",
    "391": "MS", "392": "MS", "393": "MS", "394": "MS", "395": "MS",
    "396": "MS", "397": "MS",
    # KY
    "400": "KY", "401": "KY", "402": "KY", "403": "KY", "404": "KY",
    "405": "KY", "406": "KY", "407": "KY", "408": "KY", "409": "KY",
    "410": "KY", "411": "KY", "412": "KY", "413": "KY", "414": "KY",
    "415": "KY", "416": "KY", "417": "KY", "418": "KY",
    # OH
    "430": "OH", "431": "OH", "432": "OH", "433": "OH", "434": "OH",
    "435": "OH", "436": "OH", "437": "OH", "438": "OH", "439": "OH",
    "440": "OH", "441": "OH", "442": "OH", "443": "OH", "444": "OH",
    "445": "OH", "446": "OH", "447": "OH", "448": "OH", "449": "OH",
    "450": "OH", "451": "OH", "452": "OH", "453": "OH", "454": "OH",
    "455": "OH", "456": "OH", "457": "OH", "458": "OH",
    # IN
    "460": "IN", "461": "IN", "462": "IN", "463": "IN", "464": "IN",
    "465": "IN", "466": "IN", "467": "IN", "468": "IN", "469": "IN",
    "470": "IN", "471": "IN", "472": "IN", "473": "IN", "474": "IN",
    "475": "IN", "476": "IN", "477": "IN", "478": "IN", "479": "IN",
    # MI
    "480": "MI", "481": "MI", "482": "MI", "483": "MI", "484": "MI",
    "485": "MI", "486": "MI", "487": "MI", "488": "MI", "489": "MI",
    "490": "MI", "491": "MI", "492": "MI", "493": "MI", "494": "MI",
    "495": "MI", "496": "MI", "497": "MI", "498": "MI", "499": "MI",
    # IA
    "500": "IA", "501": "IA", "502": "IA", "503": "IA", "504": "IA",
    "505": "IA", "506": "IA", "507": "IA", "508": "IA", "509": "IA",
    "510": "IA", "511": "IA", "512": "IA", "513": "IA", "514": "IA",
    "515": "IA", "516": "IA",
    # WI
    "530": "WI", "531": "WI", "532": "WI", "534": "WI", "535": "WI",
    "537": "WI", "538": "WI", "539": "WI", "540": "WI", "541": "WI",
    "542": "WI", "543": "WI", "544": "WI", "545": "WI", "546": "WI",
    "547": "WI", "548": "WI", "549": "WI",
    # MN
    "550": "MN", "551": "MN", "553": "MN", "554": "MN", "555": "MN",
    "556": "MN", "557": "MN", "558": "MN", "559": "MN", "560": "MN",
    "561": "MN", "562": "MN", "563": "MN", "564": "MN", "565": "MN",
    "566": "MN", "567": "MN",
    # SD / ND
    "570": "SD", "571": "SD", "572": "SD", "573": "SD", "574": "SD",
    "575": "SD", "576": "SD", "577": "SD",
    "580": "ND", "581": "ND", "582": "ND", "583": "ND", "584": "ND",
    "585": "ND", "586": "ND", "587": "ND", "588": "ND",
    # MT
    "590": "MT", "591": "MT", "592": "MT", "593": "MT", "594": "MT",
    "595": "MT", "596": "MT", "597": "MT", "598": "MT", "599": "MT",
    # IL
    "600": "IL", "601": "IL", "602": "IL", "603": "IL", "604": "IL",
    "605": "IL", "606": "IL", "607": "IL", "608": "IL", "609": "IL",
    "610": "IL", "611": "IL", "612": "IL", "613": "IL", "614": "IL",
    "615": "IL", "616": "IL", "617": "IL", "618": "IL", "619": "IL",
    "620": "IL", "622": "IL", "623": "IL", "624": "IL", "625": "IL",
    "626": "IL", "627": "IL", "628": "IL", "629": "IL",
    # MO
    "630": "MO", "631": "MO", "633": "MO", "634": "MO", "635": "MO",
    "636": "MO", "637": "MO", "638": "MO", "639": "MO", "640": "MO",
    "641": "MO", "644": "MO", "645": "MO", "646": "MO", "647": "MO",
    "648": "MO", "649": "MO", "650": "MO", "651": "MO", "652": "MO",
    "653": "MO", "654": "MO", "655": "MO", "656": "MO", "657": "MO",
    "658": "MO",
    # KS
    "660": "KS", "661": "KS", "662": "KS", "664": "KS", "665": "KS",
    "666": "KS", "667": "KS", "668": "KS", "669": "KS", "670": "KS",
    "671": "KS", "672": "KS", "673": "KS", "674": "KS", "675": "KS",
    "676": "KS", "677": "KS", "678": "KS", "679": "KS",
    # NE
    "680": "NE", "681": "NE", "683": "NE", "684": "NE", "685": "NE",
    "686": "NE", "687": "NE", "688": "NE", "689": "NE", "690": "NE",
    "691": "NE", "692": "NE", "693": "NE",
    # LA
    "700": "LA", "701": "LA", "703": "LA", "704": "LA", "705": "LA",
    "706": "LA", "707": "LA", "708": "LA", "710": "LA", "711": "LA",
    "712": "LA", "713": "LA", "714": "LA",
    # AR
    "716": "AR", "717": "AR", "718": "AR", "719": "AR", "720": "AR",
    "721": "AR", "722": "AR", "723": "AR", "724": "AR", "725": "AR",
    "726": "AR", "727": "AR", "728": "AR", "729": "AR",
    # OK
    "730": "OK", "731": "OK", "733": "OK", "734": "OK", "735": "OK",
    "736": "OK", "737": "OK", "738": "OK", "739": "OK", "740": "OK",
    "741": "OK", "743": "OK", "744": "OK", "745": "OK", "746": "OK",
    "747": "OK", "748": "OK", "749": "OK",
    # TX
    "750": "TX", "751": "TX", "752": "TX", "753": "TX", "754": "TX",
    "755": "TX", "756": "TX", "757": "TX", "758": "TX", "759": "TX",
    "760": "TX", "761": "TX", "762": "TX", "763": "TX", "764": "TX",
    "765": "TX", "766": "TX", "767": "TX", "768": "TX", "769": "TX",
    "770": "TX", "772": "TX", "773": "TX", "774": "TX", "775": "TX",
    "776": "TX", "777": "TX", "778": "TX", "779": "TX", "780": "TX",
    "781": "TX", "782": "TX", "783": "TX", "784": "TX", "785": "TX",
    "786": "TX", "787": "TX", "788": "TX", "789": "TX", "790": "TX",
    "791": "TX", "792": "TX", "793": "TX", "794": "TX", "795": "TX",
    "796": "TX", "797": "TX", "798": "TX", "799": "TX",
    # CO
    "800": "CO", "801": "CO", "802": "CO", "803": "CO", "804": "CO",
    "805": "CO", "806": "CO", "807": "CO", "808": "CO", "809": "CO",
    "810": "CO", "811": "CO", "812": "CO", "813": "CO", "814": "CO",
    "815": "CO", "816": "CO",
    # WY
    "820": "WY", "821": "WY", "822": "WY", "823": "WY", "824": "WY",
    "825": "WY", "826": "WY", "827": "WY", "828": "WY", "829": "WY",
    "830": "WY", "831": "WY",
    # UT
    "840": "UT", "841": "UT", "842": "UT", "843": "UT", "844": "UT",
    "845": "UT", "846": "UT", "847": "UT",
    # NM
    "870": "NM", "871": "NM", "872": "NM", "873": "NM", "874": "NM",
    "875": "NM", "877": "NM", "878": "NM", "879": "NM", "880": "NM",
    "881": "NM", "882": "NM", "883": "NM", "884": "NM",
    # AZ
    "850": "AZ", "851": "AZ", "852": "AZ", "853": "AZ", "855": "AZ",
    "856": "AZ", "857": "AZ", "859": "AZ", "860": "AZ",
    # NV
    "889": "NV", "890": "NV", "891": "NV", "893": "NV", "894": "NV",
    "895": "NV", "897": "NV", "898": "NV",
    # CA
    "900": "CA", "901": "CA", "902": "CA", "903": "CA", "904": "CA",
    "905": "CA", "906": "CA", "907": "CA", "908": "CA", "910": "CA",
    "911": "CA", "912": "CA", "913": "CA", "914": "CA", "915": "CA",
    "916": "CA", "917": "CA", "918": "CA", "919": "CA", "920": "CA",
    "921": "CA", "922": "CA", "923": "CA", "924": "CA", "925": "CA",
    "926": "CA", "927": "CA", "928": "CA", "930": "CA", "931": "CA",
    "932": "CA", "933": "CA", "934": "CA", "935": "CA", "936": "CA",
    "937": "CA", "938": "CA", "939": "CA", "940": "CA", "941": "CA",
    "942": "CA", "943": "CA", "944": "CA", "945": "CA", "946": "CA",
    "947": "CA", "948": "CA", "949": "CA", "950": "CA", "951": "CA",
    "952": "CA", "953": "CA", "954": "CA", "955": "CA", "956": "CA",
    "957": "CA", "958": "CA", "959": "CA", "960": "CA", "961": "CA",
    # HI
    "967": "HI", "968": "HI",
    # OR
    "970": "OR", "971": "OR", "972": "OR", "973": "OR", "974": "OR",
    "975": "OR", "976": "OR", "977": "OR", "978": "OR", "979": "OR",
    # WA
    "980": "WA", "981": "WA", "982": "WA", "983": "WA", "984": "WA",
    "985": "WA", "986": "WA", "988": "WA", "989": "WA", "990": "WA",
    "991": "WA", "992": "WA", "993": "WA", "994": "WA",
    # AK
    "995": "AK", "996": "AK", "997": "AK", "998": "AK", "999": "AK",
    # ID
    "832": "ID", "833": "ID", "834": "ID", "835": "ID", "836": "ID",
    "837": "ID", "838": "ID",
}

# --- State full names ---
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

# ─── MARGINALIZATION DATA ─────────────────────────────────────────────────────

# Energy burden: % of household income spent on energy (all households)
# Sources: DOE LEAD Tool, EIA RECS 2020, ACEEE, NREL SLOPE
ENERGY_BURDEN_BY_STATE = {
    "AL": 4.8, "AK": 4.5, "AZ": 3.2, "AR": 4.4, "CA": 2.5,
    "CO": 2.6, "CT": 3.1, "DE": 3.3, "DC": 2.8, "FL": 3.4, "GA": 3.8,
    "HI": 3.6, "ID": 4.1, "IL": 3.0, "IN": 3.5, "IA": 3.2,
    "KS": 3.4, "KY": 4.5, "LA": 4.2, "ME": 4.3, "MD": 2.9,
    "MA": 3.2, "MI": 3.4, "MN": 2.8, "MS": 5.3, "MO": 3.5,
    "MT": 5.1, "NE": 3.1, "NV": 2.7, "NH": 3.3, "NJ": 2.9,
    "NM": 3.4, "NY": 3.1, "NC": 3.6, "ND": 3.2, "OH": 3.3,
    "OK": 3.8, "OR": 3.4, "PA": 3.2, "RI": 3.3, "SC": 4.1,
    "SD": 3.3, "TN": 4.2, "TX": 3.3, "UT": 2.3, "VT": 4.0,
    "VA": 2.9, "WA": 2.5, "WV": 4.9, "WI": 2.9, "WY": 3.1,
}

# Average monthly water bill by state ($)
# Source: DataPandas.org (utility rate surveys), EPA Water Affordability 2024
MONTHLY_WATER_BILL_BY_STATE = {
    "AL": 24, "AK": 87, "AZ": 53, "AR": 24, "CA": 76,
    "CO": 41, "CT": 41, "DE": 48, "DC": 45, "FL": 34, "GA": 28,
    "HI": 64, "ID": 49, "IL": 26, "IN": 31, "IA": 33,
    "KS": 31, "KY": 31, "LA": 34, "ME": 22, "MD": 50,
    "MA": 33, "MI": 29, "MN": 30, "MS": 35, "MO": 42,
    "MT": 41, "NE": 32, "NV": 50, "NH": 28, "NJ": 71,
    "NM": 33, "NY": 30, "NC": 20, "ND": 42, "OH": 28,
    "OK": 39, "OR": 88, "PA": 31, "RI": 33, "SC": 31,
    "SD": 26, "TN": 36, "TX": 45, "UT": 41, "VT": 21,
    "VA": 36, "WA": 78, "WV": 105, "WI": 21, "WY": 74,
}

# Poverty rate by state (%, 2023 ACS)
# Source: US Census Bureau
POVERTY_RATE_BY_STATE = {
    "AL": 15.2, "AK": 10.2, "AZ": 11.7, "AR": 15.5, "CA": 11.8,
    "CO": 9.6, "CT": 10.2, "DE": 9.6, "DC": 12.4, "FL": 12.0, "GA": 12.6,
    "HI": 10.0, "ID": 10.5, "IL": 11.6, "IN": 12.2, "IA": 11.3,
    "KS": 10.9, "KY": 15.6, "LA": 18.7, "ME": 10.6, "MD": 9.1,
    "MA": 9.7, "MI": 13.4, "MN": 9.3, "MS": 17.8, "MO": 12.3,
    "MT": 10.2, "NE": 10.9, "NV": 11.6, "NH": 7.2, "NJ": 9.2,
    "NM": 16.4, "NY": 14.0, "NC": 12.5, "ND": 11.1, "OH": 12.7,
    "OK": 14.9, "OR": 11.8, "PA": 11.6, "RI": 12.2, "SC": 13.3,
    "SD": 10.4, "TN": 13.5, "TX": 13.4, "UT": 8.3, "VT": 9.0,
    "VA": 9.7, "WA": 9.9, "WV": 16.7, "WI": 10.3, "WY": 10.1,
}

# Non-Hispanic White % by state (2023 ACS) — derive People of Color %
# Source: Census Bureau via IndexMundi
NON_HISPANIC_WHITE_PCT_BY_STATE = {
    "AL": 65.4, "AK": 60.3, "AZ": 54.4, "AR": 72.2, "CA": 36.8,
    "CO": 67.9, "CT": 66.5, "DE": 61.9, "DC": 37.5, "FL": 53.5, "GA": 52.4,
    "HI": 21.8, "ID": 81.7, "IL": 61.0, "IN": 78.9, "IA": 85.3,
    "KS": 75.7, "KY": 84.3, "LA": 58.6, "ME": 93.1, "MD": 50.5,
    "MA": 71.4, "MI": 74.9, "MN": 79.5, "MS": 56.5, "MO": 79.3,
    "MT": 85.9, "NE": 78.6, "NV": 48.7, "NH": 90.0, "NJ": 54.9,
    "NM": 37.1, "NY": 55.4, "NC": 62.8, "ND": 84.0, "OH": 78.7,
    "OK": 65.3, "OR": 75.3, "PA": 76.1, "RI": 72.0, "SC": 63.7,
    "SD": 81.4, "TN": 73.7, "TX": 41.5, "UT": 78.0, "VT": 92.5,
    "VA": 61.5, "WA": 68.0, "WV": 92.1, "WI": 81.1, "WY": 83.8,
}

# Median household income by state ($, 2023 ACS)
# Source: Census Bureau
MEDIAN_HOUSEHOLD_INCOME_BY_STATE = {
    "AL": 56_929, "AK": 86_370, "AZ": 72_581, "AR": 52_528, "CA": 91_905,
    "CO": 87_598, "CT": 90_213, "DE": 75_340, "DC": 101_722, "FL": 67_917, "GA": 66_559,
    "HI": 94_814, "ID": 69_208, "IL": 78_433, "IN": 63_982, "IA": 68_718,
    "KS": 69_747, "KY": 57_644, "LA": 55_416, "ME": 68_251, "MD": 98_461,
    "MA": 96_505, "MI": 66_986, "MN": 84_313, "MS": 48_610, "MO": 63_594,
    "MT": 66_017, "NE": 71_772, "NV": 69_733, "NH": 90_845, "NJ": 97_126,
    "NM": 58_722, "NY": 75_910, "NC": 64_730, "ND": 73_959, "OH": 62_689,
    "OK": 59_673, "OR": 76_362, "PA": 73_170, "RI": 76_582, "SC": 59_318,
    "SD": 67_180, "TN": 61_770, "TX": 73_035, "UT": 86_833, "VT": 74_014,
    "VA": 87_249, "WA": 90_325, "WV": 51_248, "WI": 72_458, "WY": 72_495,
}

# National reference values
NATIONAL_AVG_ENERGY_BURDEN = 3.1         # %
NATIONAL_AVG_WATER_BILL = 41.52          # $/month
NATIONAL_AVG_POVERTY_RATE = 12.4         # %
NATIONAL_AVG_POC_PCT = 39.6             # %
HIGH_ENERGY_BURDEN_THRESHOLD = 6.0       # DOE definition

# EPA equivalency constants
EPA_CO2_PER_CAR_PER_YEAR_LBS = 10_141   # 4.6 metric tons
EPA_KWH_PER_HOUSEHOLD_PER_YEAR = 10_500  # EIA average
EPA_CO2_PER_TREE_PER_YEAR_LBS = 48.0     # ~48 lbs/tree/yr
GALLONS_PER_OLYMPIC_POOL = 660_000


# ─── EJ COMPUTE FUNCTIONS ─────────────────────────────────────────────────────

def resolve_location(zip_code: str) -> Optional[Dict[str, Any]]:
    """
    Resolve a zip code to state, eGRID subregion, and all associated data.
    Returns None if zip code cannot be resolved.
    """
    z = zip_code.strip().zfill(5)
    zip3 = z[:3]

    state = ZIP3_TO_STATE.get(zip3)
    if not state:
        return None

    egrid = STATE_TO_EGRID.get(state)
    if not egrid:
        return None

    co2_rate = EGRID_CO2_LBS_PER_MWH.get(egrid, 891)  # national avg fallback
    fuel_mix = EGRID_FUEL_MIX.get(egrid, {})
    peak_demand = STATE_PEAK_DEMAND_MW.get(state, 20_000)

    # Compute weighted water intensity from fuel mix
    water_intensity = sum(
        fuel_mix.get(fuel, 0) * WATER_GAL_PER_MWH.get(fuel, 0)
        for fuel in WATER_GAL_PER_MWH
    )

    return {
        "state": state,
        "state_name": STATE_NAMES.get(state, state),
        "egrid_subregion": egrid,
        "co2_lbs_per_mwh": co2_rate,
        "fuel_mix": fuel_mix,
        "peak_demand_mw": peak_demand,
        "water_intensity_gal_per_mwh": water_intensity,
        # Marginalization
        "energy_burden_pct": ENERGY_BURDEN_BY_STATE.get(state, NATIONAL_AVG_ENERGY_BURDEN),
        "water_bill_monthly": MONTHLY_WATER_BILL_BY_STATE.get(state, NATIONAL_AVG_WATER_BILL),
        "poverty_rate": POVERTY_RATE_BY_STATE.get(state, NATIONAL_AVG_POVERTY_RATE),
        "people_of_color_pct": round(100 - NON_HISPANIC_WHITE_PCT_BY_STATE.get(state, 60.4), 1),
        "median_income": MEDIAN_HOUSEHOLD_INCOME_BY_STATE.get(state, 75_000),
    }


def compute_ej_impact(
    dc_size_mw: float,
    baseline_pue: float,
    load_growth_rate: float,
    energy_reduction_pct: float,
    zip_code: str,
    capacity_factor: float = 0.70,
) -> Optional[Dict[str, Any]]:
    """
    Compute the environmental justice impact of Vigilent at a given location.

    Returns dict with marginalization metrics, environmental impact, and
    de-marginalization narrative data. Returns None if zip code is invalid.
    """
    loc = resolve_location(zip_code)
    if loc is None:
        return None

    # --- Energy calculation ---
    total_energy_mwh = dc_size_mw * baseline_pue * 8760 * capacity_factor * (1 + load_growth_rate)
    energy_saved_mwh = total_energy_mwh * energy_reduction_pct

    # --- Carbon impact ---
    co2_avoided_lbs = energy_saved_mwh * loc["co2_lbs_per_mwh"]
    co2_avoided_metric_tons = co2_avoided_lbs / 2204.62

    # --- Water impact (upstream grid water saved) ---
    water_saved_gallons = energy_saved_mwh * loc["water_intensity_gal_per_mwh"]

    # --- Grid strain relief ---
    mw_freed = dc_size_mw * baseline_pue * capacity_factor * energy_reduction_pct
    grid_relief_pct = (mw_freed / loc["peak_demand_mw"] * 100) if loc["peak_demand_mw"] > 0 else 0

    # --- EPA equivalencies ---
    cars_equivalent = co2_avoided_lbs / EPA_CO2_PER_CAR_PER_YEAR_LBS
    homes_equivalent = (energy_saved_mwh * 1000) / EPA_KWH_PER_HOUSEHOLD_PER_YEAR
    trees_equivalent = co2_avoided_lbs / EPA_CO2_PER_TREE_PER_YEAR_LBS
    pools_equivalent = water_saved_gallons / GALLONS_PER_OLYMPIC_POOL if GALLONS_PER_OLYMPIC_POOL > 0 else 0

    # --- Marginalization metrics ---
    energy_burden = loc["energy_burden_pct"]
    energy_burden_ratio = energy_burden / NATIONAL_AVG_ENERGY_BURDEN if NATIONAL_AVG_ENERGY_BURDEN > 0 else 1
    poverty_rate = loc["poverty_rate"]
    poverty_ratio = poverty_rate / NATIONAL_AVG_POVERTY_RATE if NATIONAL_AVG_POVERTY_RATE > 0 else 1
    poc_pct = loc["people_of_color_pct"]

    # EJScreen-style demographic index: (approx 2x poverty rate + POC%) / 2
    low_income_approx = min(poverty_rate * 2, 60.0)  # cap at 60%
    demographic_index = (low_income_approx + poc_pct) / 2

    # National average demographic index for comparison
    national_demo_index = (min(NATIONAL_AVG_POVERTY_RATE * 2, 60.0) + NATIONAL_AVG_POC_PCT) / 2

    # --- How Vigilent helps (de-marginalization) ---
    # Annual energy cost of the saved energy (at state-level implied cost)
    avg_energy_cost_per_mwh = (energy_burden / 100) * loc["median_income"] / (
        EPA_KWH_PER_HOUSEHOLD_PER_YEAR / 1000)  # rough cost per MWh
    energy_cost_savings = energy_saved_mwh * avg_energy_cost_per_mwh

    # How many households' annual energy costs does this savings represent
    avg_household_energy_cost = (energy_burden / 100) * loc["median_income"]
    households_equivalent_savings = (
        energy_cost_savings / avg_household_energy_cost
        if avg_household_energy_cost > 0 else 0
    )

    return {
        # Location
        "state": loc["state"],
        "state_name": loc["state_name"],
        "egrid_subregion": loc["egrid_subregion"],
        "zip_code": zip_code.strip(),

        # Marginalization (current state)
        "energy_burden_pct": energy_burden,
        "energy_burden_ratio": round(energy_burden_ratio, 2),
        "water_bill_monthly": loc["water_bill_monthly"],
        "poverty_rate": poverty_rate,
        "poverty_ratio": round(poverty_ratio, 2),
        "people_of_color_pct": poc_pct,
        "demographic_index": round(demographic_index, 1),
        "national_demo_index": round(national_demo_index, 1),
        "median_income": loc["median_income"],

        # Environmental impact
        "total_energy_mwh": total_energy_mwh,
        "energy_saved_mwh": energy_saved_mwh,
        "co2_avoided_lbs": co2_avoided_lbs,
        "co2_avoided_metric_tons": co2_avoided_metric_tons,
        "co2_rate_lbs_per_mwh": loc["co2_lbs_per_mwh"],
        "water_saved_gallons": water_saved_gallons,
        "water_intensity_gal_per_mwh": loc["water_intensity_gal_per_mwh"],
        "mw_freed": mw_freed,
        "grid_relief_pct": grid_relief_pct,
        "peak_demand_mw": loc["peak_demand_mw"],

        # EPA equivalencies
        "cars_equivalent": cars_equivalent,
        "homes_equivalent": homes_equivalent,
        "trees_equivalent": trees_equivalent,
        "pools_equivalent": pools_equivalent,

        # De-marginalization
        "energy_cost_savings_annual": energy_cost_savings,
        "households_equivalent_savings": households_equivalent_savings,

        # Grid data
        "fuel_mix": loc["fuel_mix"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Verify against spreadsheet Sheet1 defaults
    # (20MW, PUE=1.58, $0.10, 8% growth, 40% OPEX, $1.5M invest, 7.5% E.red, 4% W.red,
    #  capacity_factor=0.70 — applied 2026-04 update)
    result = compute_score(
        dc_size_mw=20,
        baseline_pue=1.58,
        electricity_price=0.10,
        load_growth_rate=0.08,
        energy_pct_opex=0.40,
        investment_cost=1_500_000,
        energy_reduction_pct=0.075,
        water_reduction_pct=0.04,
        num_years=1,
        capacity_factor=0.70,
    )

    print("Vigilent Engine — Verification")
    print("=" * 50)
    print(f"Composite Score: {result['composite_score']:.2f}")
    print(f"  Expected at CF=0.70: ~45.7  (was ~64.15 at CF=1.0)")
    print()
    for k, v in result["factor_scores"].items():
        w = SCORING_CONFIG[k]["weight"]
        print(f"  {k:25s}  score={v:>7.2f}  x{w:.2f} = {v*w:.2f}")
    print()
    print(f"  Savings/MW:   ${result['savings_per_mw']:,.0f}")
    print(f"  Payback:      {result['payback_period_years']:.2f} yr")
    print(f"  OPEX Impact:  {result['impact_on_opex_pct']*100:.1f}%")
    print(f"  Annual Cost:  ${result['annual_energy_cost']:,.0f}")
    print(f"  Ann. Savings: ${result['estimated_savings']:,.0f}")

    # Verify payback inversion
    print()
    print("Payback inversion check:")
    print(f"  0.41yr payback → score = {result['factor_scores']['payback_period']:.1f}"
          f" (should be ~83-87)")

    # Test OpenEI
    print()
    print("OpenEI rate lookup for 95110 (San Jose):")
    rates = lookup_electricity_rate("95110")
    for r in rates[:3]:
        rate_str = f"${r['blended_rate_per_kwh']:.4f}/kWh" if r['blended_rate_per_kwh'] else "N/A"
        print(f"  {r['utility']} — {r['rate_name']}: {rate_str}")

    # Test EJ Calculator
    print()
    print("EJ Calculator — Virginia (20147, Ashburn):")
    ej = compute_ej_impact(
        dc_size_mw=20, baseline_pue=1.55,
        load_growth_rate=0.10, energy_reduction_pct=0.10,
        zip_code="20147",
    )
    if ej:
        print(f"  State: {ej['state_name']} ({ej['state']})")
        print(f"  eGRID: {ej['egrid_subregion']} ({ej['co2_rate_lbs_per_mwh']:.0f} lbs CO2/MWh)")
        print(f"  Energy Burden: {ej['energy_burden_pct']}% (national avg: {NATIONAL_AVG_ENERGY_BURDEN}%)")
        print(f"  Energy Burden Ratio: {ej['energy_burden_ratio']:.2f}x national avg")
        print(f"  Poverty Rate: {ej['poverty_rate']}% (national avg: {NATIONAL_AVG_POVERTY_RATE}%)")
        print(f"  People of Color: {ej['people_of_color_pct']}%")
        print(f"  Demographic Index: {ej['demographic_index']} (national: {ej['national_demo_index']})")
        print(f"  CO2 Avoided: {ej['co2_avoided_metric_tons']:,.0f} metric tons/yr")
        print(f"  Water Saved: {ej['water_saved_gallons']:,.0f} gallons/yr")
        print(f"  Grid Relief: {ej['mw_freed']:.1f} MW ({ej['grid_relief_pct']:.3f}% of state peak)")
        print(f"  Cars Equivalent: {ej['cars_equivalent']:,.0f}")
        print(f"  Homes Equivalent: {ej['homes_equivalent']:,.0f}")
    else:
        print("  ERROR: Could not resolve location")
