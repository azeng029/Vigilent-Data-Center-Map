"""
Vigilent Optimization Model
============================
Finds the ideal data center profile that maximizes the Vigilent composite
score, runs sensitivity analysis, and generates:
  - Console report
  - Self-contained HTML report with embedded Plotly charts

Usage:
  python3 optimizer.py
  python3 optimizer.py --investment 1200000 --energy-red 0.10 --water-red 0.05 --years 1
"""

import argparse
import json
import os
import sys
import textwrap
from datetime import datetime

import numpy as np
from scipy.optimize import differential_evolution

from vigilent_engine import (
    DC_PARAMS, VIGILENT_PARAMS, SCORING_CONFIG,
    compute_score, lookup_electricity_rate,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

DC_KEYS = ["dc_size_mw", "baseline_pue", "electricity_price",
           "load_growth_rate", "energy_pct_opex"]

SWEET_SPOT_THRESHOLD = 75  # Minimum composite score for "sweet spot"

# Major US data center market zip codes for location matching
DC_MARKETS = {
    "Ashburn, VA (Data Center Alley)": "20147",
    "Dallas, TX": "75201",
    "Phoenix, AZ": "85001",
    "Chicago, IL": "60601",
    "Portland, OR": "97201",
    "San Jose, CA": "95110",
    "New York, NY": "10001",
    "Atlanta, GA": "30301",
    "Salt Lake City, UT": "84101",
    "Des Moines, IA": "50301",
    "Columbus, OH": "43201",
    "Reno, NV": "89501",
}


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIMIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def optimize(vigilent_params: dict, verbose: bool = True) -> dict:
    """
    Run differential evolution to maximize composite score.

    vigilent_params: dict with investment_cost, energy_reduction_pct,
                     water_reduction_pct, num_years
    Returns: dict with optimal_params, optimal_score, result object
    """
    bounds = [(DC_PARAMS[k]["min"], DC_PARAMS[k]["max"]) for k in DC_KEYS]

    def objective(x):
        params = {DC_KEYS[i]: x[i] for i in range(len(DC_KEYS))}
        params.update(vigilent_params)
        r = compute_score(**params)
        return -r["composite_score"]  # Minimize negative = maximize

    if verbose:
        print("Running optimization (differential evolution)...")

    result = differential_evolution(
        objective, bounds,
        seed=42, maxiter=300, tol=1e-8,
        polish=True, disp=False,
    )

    optimal = {DC_KEYS[i]: result.x[i] for i in range(len(DC_KEYS))}
    optimal.update(vigilent_params)
    score_result = compute_score(**optimal)

    return {
        "optimal_params": optimal,
        "score_result": score_result,
        "de_result": result,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SENSITIVITY ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def sensitivity_analysis(optimal_params: dict, n_points: int = 100) -> dict:
    """
    For each DC parameter, sweep it across its full range while holding
    all others at their optimal values.

    Returns: {param_key: {"values": [...], "scores": [...]}}
    """
    results = {}
    for key in DC_KEYS:
        meta = DC_PARAMS[key]
        values = np.linspace(meta["min"], meta["max"], n_points)
        scores = []
        for v in values:
            params = dict(optimal_params)
            params[key] = v
            r = compute_score(**params)
            scores.append(r["composite_score"])
        results[key] = {"values": values.tolist(), "scores": scores}
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# SWEET SPOT RANGES
# ═══════════════════════════════════════════════════════════════════════════════

def compute_sweet_spot_ranges(optimal_params: dict,
                               threshold: float = SWEET_SPOT_THRESHOLD) -> dict:
    """
    For each DC parameter, find the min/max values where composite score >= threshold,
    holding all other params at their optimal values.

    Returns: {param_key: {"min": float, "max": float, "optimal": float}}
    """
    ranges = {}
    for key in DC_KEYS:
        meta = DC_PARAMS[key]
        optimal_val = optimal_params[key]
        sweep = np.linspace(meta["min"], meta["max"], 500)

        # Find all values where score >= threshold
        valid = []
        for v in sweep:
            params = dict(optimal_params)
            params[key] = v
            r = compute_score(**params)
            if r["composite_score"] >= threshold:
                valid.append(v)

        if valid:
            ranges[key] = {
                "min": min(valid),
                "max": max(valid),
                "optimal": optimal_val,
            }
        else:
            ranges[key] = {
                "min": optimal_val,
                "max": optimal_val,
                "optimal": optimal_val,
            }

    return ranges


# ═══════════════════════════════════════════════════════════════════════════════
# LOCATION MATCHING
# ═══════════════════════════════════════════════════════════════════════════════

def match_locations(optimal_price: float, tolerance: float = 0.03) -> list:
    """
    Query OpenEI for major DC markets and find locations with electricity
    prices within tolerance of optimal_price.
    """
    matches = []
    for location, zipcode in DC_MARKETS.items():
        try:
            rates = lookup_electricity_rate(zipcode)
            for r in rates[:3]:
                rate = r.get("blended_rate_per_kwh")
                if rate is not None and abs(rate - optimal_price) <= tolerance:
                    matches.append({
                        "location": location,
                        "utility": r["utility"],
                        "rate_name": r["rate_name"],
                        "rate": rate,
                        "diff": abs(rate - optimal_price),
                    })
        except Exception:
            continue

    matches.sort(key=lambda m: m["diff"])
    return matches


# ═══════════════════════════════════════════════════════════════════════════════
# CONSOLE REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def print_console_report(opt_result: dict, sensitivity: dict,
                          sweet_spots: dict, locations: list,
                          vigilent_params: dict):
    """Print a formatted console report."""
    sr = opt_result["score_result"]
    op = opt_result["optimal_params"]
    fs = sr["factor_scores"]

    print()
    print("=" * 65)
    print("  IDEAL DATA CENTER PROFILE FOR VIGILENT")
    print(f"  Composite Score: {sr['composite_score']:.1f} / 100")
    print("=" * 65)
    print()
    print(f"  DC Size:           {op['dc_size_mw']:.1f} MW")
    print(f"  Baseline PUE:      {op['baseline_pue']:.2f}")
    print(f"  Electricity Price: ${op['electricity_price']:.4f}/kWh")
    print(f"  Load Growth Rate:  {op['load_growth_rate']*100:.1f}%")
    print(f"  Energy % of OPEX:  {op['energy_pct_opex']*100:.1f}%")
    print()
    print("-" * 65)
    print("  VIGILENT PARAMETERS (held fixed during optimization)")
    print("-" * 65)
    print(f"  Investment Cost:    ${vigilent_params['investment_cost']:,.0f}")
    print(f"  Energy Reduction:   {vigilent_params['energy_reduction_pct']*100:.1f}%")
    print(f"  Water Reduction:    {vigilent_params['water_reduction_pct']*100:.1f}%")
    print(f"  Number of Years:    {vigilent_params.get('num_years', 1)}")
    print()
    print("-" * 65)
    print(f"  SWEET SPOT RANGES (score >= {SWEET_SPOT_THRESHOLD})")
    print("-" * 65)
    for key in DC_KEYS:
        meta = DC_PARAMS[key]
        ss = sweet_spots[key]
        label = meta["label"]
        fmt = meta.get("fmt", ".2f")
        mult = meta.get("display_mult", 1)
        unit = meta.get("unit", "")

        def _f(v):
            v2 = v * mult
            if "%" in fmt:
                return f"{v2:.1f}%"
            elif "," in fmt:
                return f"${v2:{fmt}}"
            elif unit == "$/kWh":
                return f"${v2:{fmt}}/kWh"
            elif unit == "MW":
                return f"{v2:.0f} MW"
            else:
                return f"{v2:{fmt}} {unit}".strip()

        print(f"  {label:25s}  {_f(ss['min'])} — {_f(ss['max'])}")

    print()
    print("-" * 65)
    print("  FACTOR BREAKDOWN AT OPTIMUM")
    print("-" * 65)
    for k, v in fs.items():
        w = SCORING_CONFIG[k]["weight"]
        print(f"  {k:25s}  {v:>6.1f} / 100  (weight {w*100:.0f}%)"
              f"  = {v * w:.1f}")
    print(f"  {'':25s}  {'─' * 30}")
    print(f"  {'TOTAL':25s}  {sr['composite_score']:>6.1f} / 100")
    print()
    print("-" * 65)
    print("  KEY METRICS AT OPTIMUM")
    print("-" * 65)
    print(f"  Annual Energy Cost:  ${sr['annual_energy_cost']:,.0f}")
    print(f"  Estimated Savings:   ${sr['estimated_savings']:,.0f}/yr")
    print(f"  Savings per MW:      ${sr['savings_per_mw']:,.0f}")
    print(f"  Payback Period:      {sr['payback_period_years']:.2f} yr")
    print(f"  OPEX Impact:         {sr['impact_on_opex_pct']*100:.1f}%")

    if locations:
        print()
        print("-" * 65)
        print("  MATCHING US LOCATIONS (electricity price match)")
        print("-" * 65)
        for loc in locations[:8]:
            print(f"  {loc['location']:35s} ${loc['rate']:.4f}/kWh"
                  f"  ({loc['utility']})")

    print()
    print("-" * 65)
    print("  INTERPRETATION")
    print("-" * 65)
    print(textwrap.fill(
        "DC Size: Smaller DCs achieve higher $/MW savings because Vigilent's "
        "fixed investment is spread over less capacity. Target smaller-to-mid "
        "sized facilities for maximum per-MW impact.", width=65,
        initial_indent="  ", subsequent_indent="  "))
    print()
    print(textwrap.fill(
        "PUE: Higher PUE = more total energy consumption = larger absolute "
        "savings and faster payback. Target inefficient facilities — they "
        "benefit most from cooling optimization.", width=65,
        initial_indent="  ", subsequent_indent="  "))
    print()
    print(textwrap.fill(
        "Electricity Price: Higher prices multiply the dollar value of every "
        "kWh saved. Target DCs in expensive electricity markets (CA, NY, NE) "
        "for maximum financial impact.", width=65,
        initial_indent="  ", subsequent_indent="  "))
    print()
    print(textwrap.fill(
        "Load Growth: Higher growth rates increase urgency and projected "
        "savings. Target DCs under capacity pressure from AI/cloud expansion.",
        width=65, initial_indent="  ", subsequent_indent="  "))
    print()
    print(textwrap.fill(
        "Energy % of OPEX: When energy dominates operating costs, Vigilent's "
        "savings have outsized impact on the bottom line. Target facilities "
        "where energy is 40%+ of OPEX.", width=65,
        initial_indent="  ", subsequent_indent="  "))
    print()
    print("=" * 65)


# ═══════════════════════════════════════════════════════════════════════════════
# HTML REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def generate_html_report(opt_result: dict, sensitivity: dict,
                          sweet_spots: dict, locations: list,
                          vigilent_params: dict, output_path: str):
    """Generate a self-contained HTML report with embedded Plotly charts."""
    import plotly.graph_objects as go
    import plotly.io as pio
    from plotly.subplots import make_subplots

    sr = opt_result["score_result"]
    op = opt_result["optimal_params"]
    fs = sr["factor_scores"]

    # ─── Chart 1: Radar / Spider chart of factor scores ───
    categories = list(fs.keys())
    labels = {
        "savings_per_mw": "$/MW Savings",
        "water_savings_pct": "Water Savings",
        "impact_on_opex": "OPEX Impact",
        "payback_period": "Payback Period",
        "load_growth": "Load Growth",
    }
    cat_labels = [labels.get(c, c) for c in categories]
    values = [fs[c] for c in categories]
    # Close the polygon
    cat_labels_closed = cat_labels + [cat_labels[0]]
    values_closed = values + [values[0]]

    radar_fig = go.Figure(data=go.Scatterpolar(
        r=values_closed,
        theta=cat_labels_closed,
        fill="toself",
        fillcolor="rgba(16, 117, 232, 0.25)",
        line=dict(color="#1075E8", width=2),
        marker=dict(size=8, color="#1075E8"),
        text=[f"{v:.1f}" for v in values_closed],
        hoverinfo="text+theta",
    ))
    radar_fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, range=[0, 100], tickvals=[25, 50, 75, 100]),
        ),
        title=dict(text=f"Factor Scores at Optimum (Composite: {sr['composite_score']:.1f})",
                   font=dict(size=16)),
        margin=dict(l=60, r=60, t=60, b=40),
        height=450,
    )

    # ─── Chart 2: Sensitivity curves (5 subplots) ───
    sens_fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=[DC_PARAMS[k]["label"] for k in DC_KEYS] + [""],
        vertical_spacing=0.12,
        horizontal_spacing=0.1,
    )

    colors = ["#1075E8", "#25ac01", "#FF6B35", "#8B5CF6", "#EC4899"]
    for idx, key in enumerate(DC_KEYS):
        row = idx // 2 + 1
        col = idx % 2 + 1
        sd = sensitivity[key]
        meta = DC_PARAMS[key]
        mult = meta.get("display_mult", 1)
        x_vals = [v * mult for v in sd["values"]]
        optimal_x = op[key] * mult

        # Score line
        sens_fig.add_trace(go.Scatter(
            x=x_vals, y=sd["scores"],
            mode="lines", name=meta["label"],
            line=dict(color=colors[idx], width=2.5),
            showlegend=False,
        ), row=row, col=col)

        # Threshold line at 75
        sens_fig.add_hline(y=75, line_dash="dash", line_color="#999",
                           line_width=1, row=row, col=col)

        # Optimal marker
        opt_score_at_opt = None
        for i, v in enumerate(sd["values"]):
            if abs(v - op[key]) < (meta["max"] - meta["min"]) / 100:
                opt_score_at_opt = sd["scores"][i]
                break
        if opt_score_at_opt is None:
            opt_score_at_opt = sr["composite_score"]

        sens_fig.add_trace(go.Scatter(
            x=[optimal_x], y=[opt_score_at_opt],
            mode="markers",
            marker=dict(size=12, color=colors[idx], symbol="diamond",
                       line=dict(width=2, color="white")),
            name="Optimal",
            showlegend=False,
            hovertext=f"Optimal: {optimal_x:.2f}",
            hoverinfo="text",
        ), row=row, col=col)

        # Sweet spot shading
        ss = sweet_spots[key]
        sens_fig.add_vrect(
            x0=ss["min"] * mult, x1=ss["max"] * mult,
            fillcolor="rgba(37, 172, 1, 0.1)",
            line_width=0,
            row=row, col=col,
        )

    sens_fig.update_layout(
        height=750,
        title=dict(text="Sensitivity Analysis — Composite Score vs Each Parameter",
                   font=dict(size=16)),
        margin=dict(l=50, r=30, t=70, b=40),
    )
    # Y-axis range for all subplots
    for i in range(1, 4):
        for j in range(1, 3):
            sens_fig.update_yaxes(range=[0, 105], row=i, col=j)

    # ─── Chart 3: Sweet spot range bars ───
    bar_labels = []
    bar_mins = []
    bar_maxs = []
    bar_optimals = []
    bar_colors = []

    for idx, key in enumerate(DC_KEYS):
        meta = DC_PARAMS[key]
        ss = sweet_spots[key]
        mult = meta.get("display_mult", 1)
        bar_labels.append(meta["label"])
        bar_mins.append(ss["min"] * mult)
        bar_maxs.append(ss["max"] * mult)
        bar_optimals.append(ss["optimal"] * mult)
        bar_colors.append(colors[idx])

    # Normalize each bar to 0-1 range for display
    range_fig = go.Figure()

    for i in range(len(DC_KEYS)):
        key = DC_KEYS[i]
        meta = DC_PARAMS[key]
        mult = meta.get("display_mult", 1)
        abs_min = meta["min"] * mult
        abs_max = meta["max"] * mult
        span = abs_max - abs_min if abs_max != abs_min else 1

        # Background bar (full range)
        range_fig.add_trace(go.Bar(
            y=[bar_labels[i]], x=[100],
            orientation="h",
            marker=dict(color="#f0f0f0"),
            showlegend=False,
            hoverinfo="skip",
        ))

        # Sweet spot bar
        ss_start_pct = (bar_mins[i] - abs_min) / span * 100
        ss_width_pct = (bar_maxs[i] - bar_mins[i]) / span * 100

        range_fig.add_trace(go.Bar(
            y=[bar_labels[i]], x=[ss_width_pct],
            base=[ss_start_pct],
            orientation="h",
            marker=dict(color=bar_colors[i], opacity=0.6),
            showlegend=False,
            hovertext=f"Sweet spot: {bar_mins[i]:.2f} – {bar_maxs[i]:.2f}",
            hoverinfo="text",
        ))

        # Optimal marker
        opt_pct = (bar_optimals[i] - abs_min) / span * 100
        range_fig.add_trace(go.Scatter(
            x=[opt_pct], y=[bar_labels[i]],
            mode="markers",
            marker=dict(size=14, color=bar_colors[i], symbol="diamond",
                       line=dict(width=2, color="white")),
            showlegend=False,
            hovertext=f"Optimal: {bar_optimals[i]:.2f}",
            hoverinfo="text",
        ))

    range_fig.update_layout(
        barmode="overlay",
        title=dict(text=f"Sweet Spot Ranges (Score >= {SWEET_SPOT_THRESHOLD})",
                   font=dict(size=16)),
        xaxis=dict(title="% of Parameter Range", range=[0, 100]),
        yaxis=dict(autorange="reversed"),
        height=350,
        margin=dict(l=160, r=30, t=60, b=50),
    )

    # ─── Convert charts to HTML ───
    radar_html = pio.to_html(radar_fig, full_html=False, include_plotlyjs=False)
    sens_html = pio.to_html(sens_fig, full_html=False, include_plotlyjs=False)
    range_html = pio.to_html(range_fig, full_html=False, include_plotlyjs=False)

    # ─── Build HTML ───
    # Format optimal values
    def _fval(key, val):
        meta = DC_PARAMS.get(key, VIGILENT_PARAMS.get(key, {}))
        mult = meta.get("display_mult", 1)
        fmt = meta.get("fmt", ".2f")
        unit = meta.get("unit", "")
        v = val * mult
        if "%" in fmt:
            return f"{v:{fmt.replace('%', '')}}%"
        elif "," in fmt:
            return f"${v:{fmt}}"
        elif unit == "$/kWh":
            return f"${v:{fmt}}/kWh"
        else:
            return f"{v:{fmt}} {unit}".strip()

    # Factor breakdown rows
    factor_rows = ""
    for k, v in fs.items():
        w = SCORING_CONFIG[k]["weight"]
        lbl = {
            "savings_per_mw": "$/MW Savings",
            "water_savings_pct": "Water Savings",
            "impact_on_opex": "OPEX Impact",
            "payback_period": "Payback Period",
            "load_growth": "Load Growth",
        }.get(k, k)
        factor_rows += f"""
        <tr>
            <td>{lbl}</td>
            <td style="text-align:center">{v:.1f}</td>
            <td style="text-align:center">{w*100:.0f}%</td>
            <td style="text-align:center;font-weight:600">{v*w:.1f}</td>
        </tr>"""

    # Sweet spot table rows
    sweet_rows = ""
    for key in DC_KEYS:
        meta = DC_PARAMS[key]
        ss = sweet_spots[key]
        sweet_rows += f"""
        <tr>
            <td>{meta['label']}</td>
            <td style="text-align:center">{_fval(key, ss['min'])}</td>
            <td style="text-align:center;font-weight:600">{_fval(key, ss['optimal'])}</td>
            <td style="text-align:center">{_fval(key, ss['max'])}</td>
        </tr>"""

    # Location rows
    location_rows = ""
    if locations:
        for loc in locations[:10]:
            location_rows += f"""
            <tr>
                <td>{loc['location']}</td>
                <td style="text-align:center">${loc['rate']:.4f}/kWh</td>
                <td>{loc['utility']}</td>
            </tr>"""

    location_section = ""
    if location_rows:
        location_section = f"""
        <div class="section">
            <h2>Matching US Locations</h2>
            <p>Data center markets with electricity rates within $0.03/kWh of optimal (${op['electricity_price']:.4f}/kWh):</p>
            <table>
                <tr><th>Location</th><th>Rate</th><th>Utility</th></tr>
                {location_rows}
            </table>
        </div>"""

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Vigilent Optimization Report</title>
    <script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            color: #333;
            background: #f5f6fa;
            line-height: 1.6;
        }}
        .header {{
            background: linear-gradient(135deg, #1075E8, #0a4ea0);
            color: white;
            padding: 30px 40px;
        }}
        .header h1 {{ font-size: 28px; margin-bottom: 8px; }}
        .header .subtitle {{ font-size: 14px; opacity: 0.85; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 30px 20px; }}

        .score-hero {{
            background: white;
            border-radius: 12px;
            padding: 30px;
            text-align: center;
            box-shadow: 0 2px 12px rgba(0,0,0,0.08);
            margin-bottom: 30px;
        }}
        .score-hero .big-score {{
            font-size: 72px;
            font-weight: 800;
            color: #1075E8;
            line-height: 1;
        }}
        .score-hero .score-label {{
            font-size: 16px;
            color: #666;
            margin-top: 4px;
        }}

        .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 30px; }}
        .card {{
            background: white;
            border-radius: 8px;
            padding: 16px;
            box-shadow: 0 1px 6px rgba(0,0,0,0.06);
        }}
        .card .card-label {{ font-size: 12px; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }}
        .card .card-value {{ font-size: 22px; font-weight: 700; color: #1075E8; margin-top: 4px; }}

        .section {{
            background: white;
            border-radius: 12px;
            padding: 24px 30px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.08);
            margin-bottom: 24px;
        }}
        .section h2 {{
            font-size: 18px;
            color: #1075E8;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 2px solid #e8f0fe;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th {{
            background: #f0f4ff;
            padding: 10px 12px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #dde4ef;
        }}
        td {{
            padding: 8px 12px;
            border-bottom: 1px solid #eee;
        }}
        tr:hover td {{ background: #f8faff; }}

        .interpretation {{
            background: #f0f8ff;
            border-left: 4px solid #1075E8;
            padding: 16px 20px;
            margin-top: 12px;
            border-radius: 0 8px 8px 0;
            font-size: 14px;
        }}
        .interpretation h4 {{ color: #1075E8; margin-bottom: 6px; }}

        .chart-container {{ margin: 20px 0; }}

        .vigilent-params {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }}
        .vparam {{
            background: #f8f9fa;
            border-radius: 6px;
            padding: 10px 14px;
        }}
        .vparam .vp-label {{ font-size: 11px; color: #888; text-transform: uppercase; }}
        .vparam .vp-value {{ font-size: 16px; font-weight: 600; }}

        .footer {{
            text-align: center;
            padding: 20px;
            font-size: 12px;
            color: #999;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Vigilent Optimization Report</h1>
        <div class="subtitle">Generated {datetime.now().strftime('%B %d, %Y at %I:%M %p')} | Ideal Data Center Profile Analysis</div>
    </div>

    <div class="container">
        <!-- Composite Score Hero -->
        <div class="score-hero">
            <div class="big-score">{sr['composite_score']:.1f}</div>
            <div class="score-label">Maximum Achievable Composite Score (out of 100)</div>
        </div>

        <!-- Vigilent Parameters Used -->
        <div class="section">
            <h2>Vigilent Parameters (Fixed During Optimization)</h2>
            <div class="vigilent-params">
                <div class="vparam">
                    <div class="vp-label">Investment Cost</div>
                    <div class="vp-value">${vigilent_params['investment_cost']:,.0f}</div>
                </div>
                <div class="vparam">
                    <div class="vp-label">Energy Reduction</div>
                    <div class="vp-value">{vigilent_params['energy_reduction_pct']*100:.1f}%</div>
                </div>
                <div class="vparam">
                    <div class="vp-label">Water Reduction</div>
                    <div class="vp-value">{vigilent_params['water_reduction_pct']*100:.1f}%</div>
                </div>
                <div class="vparam">
                    <div class="vp-label">Years</div>
                    <div class="vp-value">{vigilent_params.get('num_years', 1)}</div>
                </div>
            </div>
        </div>

        <!-- Optimal Profile Cards -->
        <div class="cards">
            <div class="card">
                <div class="card-label">DC Size</div>
                <div class="card-value">{op['dc_size_mw']:.0f} MW</div>
            </div>
            <div class="card">
                <div class="card-label">Baseline PUE</div>
                <div class="card-value">{op['baseline_pue']:.2f}</div>
            </div>
            <div class="card">
                <div class="card-label">Electricity Price</div>
                <div class="card-value">${op['electricity_price']:.4f}/kWh</div>
            </div>
            <div class="card">
                <div class="card-label">Load Growth</div>
                <div class="card-value">{op['load_growth_rate']*100:.1f}%</div>
            </div>
            <div class="card">
                <div class="card-label">Energy % of OPEX</div>
                <div class="card-value">{op['energy_pct_opex']*100:.0f}%</div>
            </div>
        </div>

        <!-- Key Metrics -->
        <div class="cards">
            <div class="card">
                <div class="card-label">Annual Energy Cost</div>
                <div class="card-value" style="color:#333">${sr['annual_energy_cost']:,.0f}</div>
            </div>
            <div class="card">
                <div class="card-label">Estimated Savings</div>
                <div class="card-value" style="color:#25ac01">${sr['estimated_savings']:,.0f}/yr</div>
            </div>
            <div class="card">
                <div class="card-label">Savings per MW</div>
                <div class="card-value" style="color:#25ac01">${sr['savings_per_mw']:,.0f}</div>
            </div>
            <div class="card">
                <div class="card-label">Payback Period</div>
                <div class="card-value" style="color:#333">{sr['payback_period_years']:.2f} yr</div>
            </div>
        </div>

        <!-- Factor Breakdown -->
        <div class="section">
            <h2>Factor Score Breakdown</h2>
            <table>
                <tr><th>Factor</th><th>Score (/100)</th><th>Weight</th><th>Weighted</th></tr>
                {factor_rows}
                <tr style="font-weight:700;border-top:2px solid #1075E8">
                    <td>TOTAL</td>
                    <td style="text-align:center">{sr['composite_score']:.1f}</td>
                    <td style="text-align:center">100%</td>
                    <td style="text-align:center;color:#1075E8">{sr['composite_score']:.1f}</td>
                </tr>
            </table>
        </div>

        <!-- Radar Chart -->
        <div class="section">
            <h2>Factor Score Radar</h2>
            <div class="chart-container">{radar_html}</div>
        </div>

        <!-- Sweet Spot Ranges -->
        <div class="section">
            <h2>Sweet Spot Ranges (Score &ge; {SWEET_SPOT_THRESHOLD})</h2>
            <table>
                <tr><th>Parameter</th><th>Min</th><th>Optimal</th><th>Max</th></tr>
                {sweet_rows}
            </table>
            <div class="chart-container">{range_html}</div>
        </div>

        <!-- Sensitivity Analysis -->
        <div class="section">
            <h2>Sensitivity Analysis</h2>
            <p style="font-size:14px;color:#666;margin-bottom:12px">
                Each chart sweeps one parameter while holding all others at optimal values.
                Green shading shows the sweet spot range. Diamond markers show the optimum.
            </p>
            <div class="chart-container">{sens_html}</div>
        </div>

        {location_section}

        <!-- Interpretation -->
        <div class="section">
            <h2>Interpretation Guide</h2>
            <div class="interpretation">
                <h4>DC Size</h4>
                <p>Smaller DCs achieve higher per-MW savings because Vigilent's fixed investment is spread over less capacity. Target smaller-to-mid sized facilities for maximum per-MW impact.</p>
            </div>
            <div class="interpretation" style="margin-top:8px">
                <h4>Baseline PUE</h4>
                <p>Higher PUE means more total energy consumption, leading to larger absolute savings and faster payback. Target inefficient facilities — they benefit most from cooling optimization.</p>
            </div>
            <div class="interpretation" style="margin-top:8px">
                <h4>Electricity Price</h4>
                <p>Higher prices multiply the dollar value of every kWh saved. Target DCs in expensive electricity markets (CA, NY, NE) for maximum financial impact.</p>
            </div>
            <div class="interpretation" style="margin-top:8px">
                <h4>Load Growth Rate</h4>
                <p>Higher growth rates increase urgency and projected savings. Target DCs under capacity pressure from AI/cloud expansion.</p>
            </div>
            <div class="interpretation" style="margin-top:8px">
                <h4>Energy % of OPEX</h4>
                <p>When energy dominates operating costs, Vigilent's savings have outsized impact on the bottom line. Target facilities where energy is 40%+ of OPEX.</p>
            </div>
        </div>
    </div>

    <div class="footer">
        Vigilent Optimization Report | Generated by optimizer.py | {datetime.now().strftime('%Y-%m-%d')}
    </div>
</body>
</html>"""

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        f.write(html_content)

    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        description="Vigilent Optimization Model — find the ideal data center profile",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python3 optimizer.py
          python3 optimizer.py --investment 1200000 --energy-red 0.10
          python3 optimizer.py --investment 2000000 --energy-red 0.15 --water-red 0.08 --years 3
        """),
    )
    parser.add_argument("--investment", type=float,
                        default=VIGILENT_PARAMS["investment_cost"]["default"],
                        help=f"Vigilent investment cost ($) [default: {VIGILENT_PARAMS['investment_cost']['default']:,.0f}]")
    parser.add_argument("--energy-red", type=float,
                        default=VIGILENT_PARAMS["energy_reduction_pct"]["default"],
                        help=f"Energy reduction fraction [default: {VIGILENT_PARAMS['energy_reduction_pct']['default']}]")
    parser.add_argument("--water-red", type=float,
                        default=VIGILENT_PARAMS["water_reduction_pct"]["default"],
                        help=f"Water reduction fraction [default: {VIGILENT_PARAMS['water_reduction_pct']['default']}]")
    parser.add_argument("--years", type=int,
                        default=VIGILENT_PARAMS["num_years"]["default"],
                        help=f"Number of years [default: {VIGILENT_PARAMS['num_years']['default']}]")
    parser.add_argument("--output", type=str,
                        default=None,
                        help="Output HTML path [default: output/optimization_report.html]")
    parser.add_argument("--no-locations", action="store_true",
                        help="Skip OpenEI location matching (faster)")
    return parser.parse_args()


def main():
    args = parse_args()

    vigilent_params = {
        "investment_cost": args.investment,
        "energy_reduction_pct": args.energy_red,
        "water_reduction_pct": args.water_red,
        "num_years": args.years,
    }

    print()
    print("=" * 65)
    print("  VIGILENT OPTIMIZATION MODEL")
    print("=" * 65)
    print()
    print(f"  Investment Cost:   ${vigilent_params['investment_cost']:,.0f}")
    print(f"  Energy Reduction:  {vigilent_params['energy_reduction_pct']*100:.1f}%")
    print(f"  Water Reduction:   {vigilent_params['water_reduction_pct']*100:.1f}%")
    print(f"  Years:             {vigilent_params['num_years']}")
    print()

    # 1. Optimize
    opt_result = optimize(vigilent_params)
    print(f"  Optimal composite score: {opt_result['score_result']['composite_score']:.1f}")
    print()

    # 2. Sensitivity analysis
    print("Running sensitivity analysis...")
    sensitivity = sensitivity_analysis(opt_result["optimal_params"])
    print("  Done.")

    # 3. Sweet spot ranges
    print("Computing sweet spot ranges...")
    sweet_spots = compute_sweet_spot_ranges(opt_result["optimal_params"])
    print("  Done.")

    # 4. Location matching
    locations = []
    if not args.no_locations:
        print("Matching locations via OpenEI (this may take a moment)...")
        optimal_price = opt_result["optimal_params"]["electricity_price"]
        locations = match_locations(optimal_price, tolerance=0.03)
        print(f"  Found {len(locations)} matching rate(s).")
    print()

    # 5. Console report
    print_console_report(opt_result, sensitivity, sweet_spots,
                         locations, vigilent_params)

    # 6. HTML report
    output_path = args.output or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "output", "optimization_report.html"
    )
    print(f"\nGenerating HTML report: {output_path}")
    generate_html_report(opt_result, sensitivity, sweet_spots,
                         locations, vigilent_params, output_path)
    print(f"  Report saved to: {output_path}")
    print()


if __name__ == "__main__":
    main()
