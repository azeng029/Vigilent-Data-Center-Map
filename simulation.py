"""
Vigilent Sweet Spot Simulator + Optimizer
==========================================
Interactive Dash + Plotly application with two tabs:
  1. Heatmap Simulator — explore scoring landscape
  2. Optimizer — find the ideal DC profile for Vigilent

Run:  python3 simulation.py
Open: http://127.0.0.1:8050
"""

import dash
from dash import dcc, html, Input, Output, State, callback, no_update, ctx
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
import json
import threading
import time

from vigilent_engine import (
    DC_PARAMS, VIGILENT_PARAMS, SCORING_CONFIG,
    compute_score, compute_score_grid, lookup_electricity_rate,
    compute_ej_impact, resolve_location,
    compute_exhaustive_sweep, extract_target_ranges, compute_pairwise_tradeoff,
    EGRID_CO2_LBS_PER_MWH, EGRID_FUEL_MIX,
    NATIONAL_AVG_ENERGY_BURDEN, NATIONAL_AVG_WATER_BILL,
    NATIONAL_AVG_POVERTY_RATE, NATIONAL_AVG_POC_PCT,
    HIGH_ENERGY_BURDEN_THRESHOLD,
)

# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

GRID_SIZE = 50  # 50×50 heatmap cells

# Axis-eligible parameters (non-Vigilent only)
AXIS_OPTIONS = [
    {"label": DC_PARAMS[k]["label"], "value": k}
    for k in DC_PARAMS
]

# Default axis selections
DEFAULT_X = "dc_size_mw"
DEFAULT_Y = "electricity_price"

# Discrete color bands — sharp boundaries (no gradient)
COLORSCALE = [
    [0.00, "#ffffff"],   # 0-25: white (Poor)
    [0.25, "#ffffff"],
    [0.25, "#d7e7f8"],   # 25-50: lighter blue (Low)
    [0.50, "#d7e7f8"],
    [0.50, "#88b4f2"],   # 50-75: light blue (Moderate)
    [0.75, "#88b4f2"],
    [0.75, "#1075E8"],   # 75-90: blue (Good)
    [0.90, "#1075E8"],
    [0.90, "#25ac01"],   # 90-100: green (Excellent)
    [1.00, "#25ac01"],
]

ZONE_LABELS = [
    ("90 – 100", "#25ac01", "Excellent"),
    ("75 – 89",  "#1075E8", "Good"),
    ("50 – 74",  "#88b4f2", "Moderate"),
    ("25 – 49",  "#d7e7f8", "Low"),
    ("0 – 24",   "#ffffff", "Poor"),
]

# Build default values dict (raw, not display-scaled)
ALL_DEFAULTS = {}
for k, meta in DC_PARAMS.items():
    ALL_DEFAULTS[k] = meta["default"]
for k, meta in VIGILENT_PARAMS.items():
    ALL_DEFAULTS[k] = meta["default"]

# DC keys used by optimizer
DC_KEYS = ["dc_size_mw", "baseline_pue", "electricity_price",
           "load_growth_rate", "energy_pct_opex"]

SWEET_SPOT_THRESHOLD = 75

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
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _fmt_val(key: str, val: float) -> str:
    """Format a parameter value for display."""
    meta = DC_PARAMS.get(key) or VIGILENT_PARAMS.get(key, {})
    fmt = meta.get("fmt", ".2f")
    unit = meta.get("unit", "")
    mult = meta.get("display_mult", 1)
    try:
        v = val * mult
        if "%" in fmt:
            return f"{v:.1f}%"
        elif fmt == "d":
            return f"{int(v)}{' ' + unit if unit else ''}"
        elif "," in fmt:
            return f"${val:{fmt}}"
        elif unit == "$/kWh":
            return f"${val:.2f}/kWh"
        elif unit == "MW":
            return f"{v:.0f} MW"
        else:
            return f"{v:{fmt}}{' ' + unit if unit else ''}"
    except Exception:
        return str(val)


def _to_display(key: str, raw_val: float) -> float:
    """Convert raw value to display value (apply display_mult)."""
    meta = DC_PARAMS.get(key) or VIGILENT_PARAMS.get(key, {})
    mult = meta.get("display_mult", 1)
    return raw_val * mult


def _to_raw(key: str, display_val: float) -> float:
    """Convert display value back to raw (undo display_mult)."""
    meta = DC_PARAMS.get(key) or VIGILENT_PARAMS.get(key, {})
    mult = meta.get("display_mult", 1)
    return display_val / mult if mult else display_val


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIMIZER LOGIC (ported from optimizer.py for in-browser use)
# ═══════════════════════════════════════════════════════════════════════════════

def run_optimization(vigilent_params: dict, scoring_config: dict = None) -> dict:
    """Run differential evolution to maximize composite score."""
    from scipy.optimize import differential_evolution

    bounds = [(DC_PARAMS[k]["min"], DC_PARAMS[k]["max"]) for k in DC_KEYS]

    def objective(x):
        params = {DC_KEYS[i]: x[i] for i in range(len(DC_KEYS))}
        params.update(vigilent_params)
        if scoring_config is not None:
            params["scoring_config"] = scoring_config
        r = compute_score(**params)
        return -r["composite_score"]

    result = differential_evolution(
        objective, bounds,
        seed=42, maxiter=300, tol=1e-8,
        polish=True, disp=False,
    )

    optimal = {DC_KEYS[i]: result.x[i] for i in range(len(DC_KEYS))}
    optimal.update(vigilent_params)
    cs_kwargs = dict(optimal)
    if scoring_config is not None:
        cs_kwargs["scoring_config"] = scoring_config
    score_result = compute_score(**cs_kwargs)

    return {
        "optimal_params": optimal,
        "score_result": score_result,
    }


def run_sensitivity(optimal_params: dict, n_points: int = 100,
                    scoring_config: dict = None) -> dict:
    """Sweep each DC param across its full range, others held at optimal."""
    results = {}
    for key in DC_KEYS:
        meta = DC_PARAMS[key]
        values = np.linspace(meta["min"], meta["max"], n_points)
        scores = []
        for v in values:
            params = dict(optimal_params)
            params[key] = v
            if scoring_config is not None:
                params["scoring_config"] = scoring_config
            r = compute_score(**params)
            scores.append(r["composite_score"])
        results[key] = {"values": values.tolist(), "scores": scores}
    return results


def run_sweet_spots(optimal_params: dict, threshold: float = SWEET_SPOT_THRESHOLD,
                    scoring_config: dict = None) -> dict:
    """Find min/max values where score >= threshold for each DC param."""
    ranges = {}
    for key in DC_KEYS:
        meta = DC_PARAMS[key]
        optimal_val = optimal_params[key]
        sweep = np.linspace(meta["min"], meta["max"], 500)
        valid = []
        for v in sweep:
            params = dict(optimal_params)
            params[key] = v
            if scoring_config is not None:
                params["scoring_config"] = scoring_config
            r = compute_score(**params)
            if r["composite_score"] >= threshold:
                valid.append(v)
        if valid:
            ranges[key] = {"min": min(valid), "max": max(valid), "optimal": optimal_val}
        else:
            ranges[key] = {"min": optimal_val, "max": optimal_val, "optimal": optimal_val}
    return ranges


def match_locations(optimal_price: float, tolerance: float = 0.03) -> list:
    """Query OpenEI for major DC markets near optimal_price."""
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
# LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

BRAND = "#1075E8"        # Vigilent primary blue
BRAND_DARK = "#0A5BBF"   # Darker hover state
BRAND_LIGHT = "#e8f0fe"  # Light background tint

app = dash.Dash(
    __name__,
    title="Vigilent Sweet Spot Simulator",
    suppress_callback_exceptions=True,
)

# --- Inject CSS to theme Dash sliders and inputs to Vigilent blue ---
app.index_string = '''
<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        {%css%}
        <style>
            /* === Dash 4.0 Slider theming (dash-slider-* classes) === */
            .dash-slider-range {
                background-color: ''' + BRAND + ''' !important;
            }
            .dash-slider-track {
                background-color: #d0d9e8 !important;
            }
            .dash-slider-thumb {
                width: 12px !important;
                height: 12px !important;
                background-color: ''' + BRAND + ''' !important;
                border-color: ''' + BRAND + ''' !important;
                box-shadow: 0 1px 4px rgba(16,117,232,0.4) !important;
            }
            .dash-slider-thumb:hover,
            .dash-slider-thumb:active,
            .dash-slider-thumb:focus {
                width: 14px !important;
                height: 14px !important;
                background-color: ''' + BRAND_DARK + ''' !important;
                border-color: ''' + BRAND_DARK + ''' !important;
                box-shadow: 0 0 0 4px rgba(16,117,232,0.2) !important;
            }
            .dash-slider-container {
                padding-top: 6px !important;
            }
            .dash-slider-tooltip {
                background-color: ''' + BRAND + ''' !important;
                color: white !important;
                border-radius: 4px !important;
            }
            .dash-slider-mark { color: #666 !important; font-size: 11px !important; }

            /* === Fallback for older Dash (rc-slider-* classes) === */
            .rc-slider-track { background-color: ''' + BRAND + ''' !important; }
            .rc-slider-rail  { background-color: #d0d9e8 !important; }
            .rc-slider-handle {
                width: 12px !important;
                height: 12px !important;
                margin-top: -4px !important;
                border-color: ''' + BRAND + ''' !important;
                background-color: ''' + BRAND + ''' !important;
                opacity: 1 !important;
                box-shadow: 0 1px 4px rgba(16,117,232,0.4) !important;
            }
            .rc-slider-handle:hover,
            .rc-slider-handle:active,
            .rc-slider-handle-dragging {
                border-color: ''' + BRAND_DARK + ''' !important;
                background-color: ''' + BRAND_DARK + ''' !important;
                box-shadow: 0 0 0 4px rgba(16,117,232,0.2) !important;
            }
            .rc-slider-tooltip-inner {
                background-color: ''' + BRAND + ''' !important;
                border-radius: 4px !important;
            }
            .rc-slider-mark-text { color: #666 !important; font-size: 11px !important; }

            /* === Number inputs === */
            input[type="number"]:focus, input[type="text"]:focus {
                outline: none !important;
                border-color: ''' + BRAND + ''' !important;
                box-shadow: 0 0 0 2px rgba(16,117,232,0.15) !important;
            }
            /* === Dropdown === */
            .Select-control { border-color: #c0cfe0 !important; }
            .Select.is-focused > .Select-control { border-color: ''' + BRAND + ''' !important; box-shadow: 0 0 0 2px rgba(16,117,232,0.15) !important; }
            .Select-option.is-selected { background-color: ''' + BRAND + ''' !important; color: white !important; }
            .Select-option.is-focused { background-color: ''' + BRAND_LIGHT + ''' !important; }
            /* === Headings === */
            h3 { color: ''' + BRAND + ''' !important; }
            /* === Scrollbar === */
            ::-webkit-scrollbar { width: 6px; }
            ::-webkit-scrollbar-thumb { background: #bbb; border-radius: 3px; }
            ::-webkit-scrollbar-thumb:hover { background: #999; }
            ::-webkit-scrollbar-track { background: #f0f0f0; }

            /* === Prevent graph flash on update === */
            .js-plotly-plot .plotly .main-svg {
                transition: none !important;
            }
            #heatmap {
                min-height: 500px;
            }

            /* === Axis bound inputs === */
            .axis-bound-input {
                width: 62px !important;
                padding: 3px 5px !important;
                border: 1px solid #ccc !important;
                border-radius: 3px !important;
                font-size: 11px !important;
                text-align: center !important;
                color: #444 !important;
                background: #f8f9fa !important;
            }
            .axis-bound-input:focus {
                background: white !important;
                border-color: ''' + BRAND + ''' !important;
                box-shadow: 0 0 0 2px rgba(16,117,232,0.15) !important;
            }

            /* === Tab styling === */
            .tab-bar {
                display: flex;
                gap: 0;
                padding: 0 20px;
                background: linear-gradient(135deg, ''' + BRAND + ''', ''' + BRAND_DARK + ''');
            }
            .tab-btn {
                padding: 10px 28px;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                border: none;
                background: transparent;
                color: rgba(255,255,255,0.65);
                border-bottom: 3px solid transparent;
                transition: all 0.2s;
                font-family: inherit;
            }
            .tab-btn:hover {
                color: rgba(255,255,255,0.9);
            }
            .tab-btn.active {
                color: white;
                border-bottom-color: white;
                background: rgba(255,255,255,0.1);
            }

            /* === Optimizer cards === */
            .opt-card {
                background: white;
                border-radius: 10px;
                padding: 16px 20px;
                box-shadow: 0 1px 8px rgba(0,0,0,0.07);
            }
            .opt-card-label {
                font-size: 11px;
                color: #888;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }
            .opt-card-value {
                font-size: 24px;
                font-weight: 700;
                color: ''' + BRAND + ''';
                margin-top: 4px;
            }
            .opt-section {
                background: white;
                border-radius: 10px;
                padding: 20px 24px;
                box-shadow: 0 1px 8px rgba(0,0,0,0.07);
                margin-bottom: 20px;
            }
            .opt-section h3 {
                font-size: 16px !important;
                margin-bottom: 12px !important;
                padding-bottom: 8px;
                border-bottom: 2px solid ''' + BRAND_LIGHT + ''';
            }
        </style>
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
'''


# ═══════════════════════════════════════════════════════════════════════════════
# SIMULATOR TAB LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

# --- Build Vigilent parameter controls (always visible) ---
def _make_vig_control(param_key, meta):
    """Create a labeled input for a Vigilent parameter."""
    display_mult = meta.get("display_mult", 1)
    mn = meta["min"] * display_mult
    mx = meta["max"] * display_mult
    default = meta["default"] * display_mult
    step = meta.get("step", 0.01) * display_mult
    return html.Div([
        html.Label(meta["label"], style={"fontWeight": "600", "fontSize": "12px",
                                          "marginBottom": "2px", "display": "block"}),
        dcc.Input(
            id=f"vig-{param_key}",
            type="number",
            value=default,
            min=mn, max=mx, step=step,
            style={"width": "100%", "padding": "6px", "border": "1px solid #ccc",
                   "borderRadius": "4px", "fontSize": "13px", "boxSizing": "border-box"},
            debounce=True,
        ),
    ], style={"marginBottom": "10px"})


vig_controls = [_make_vig_control(k, VIGILENT_PARAMS[k]) for k in VIGILENT_PARAMS]

# --- Axis bounds controls ---
def _make_axis_bounds(axis_prefix, default_param):
    """Create min/max bound inputs for an axis."""
    meta = DC_PARAMS[default_param]
    mult = meta.get("display_mult", 1)
    return html.Div([
        html.Div([
            html.Label("Min:", style={"fontSize": "11px", "color": "#666", "marginRight": "4px"}),
            dcc.Input(
                id=f"{axis_prefix}-bound-min",
                type="number",
                value=meta["min"] * mult,
                step=meta.get("step", 0.01) * mult,
                debounce=True,
                className="axis-bound-input",
            ),
            html.Label("Max:", style={"fontSize": "11px", "color": "#666",
                                       "marginLeft": "10px", "marginRight": "4px"}),
            dcc.Input(
                id=f"{axis_prefix}-bound-max",
                type="number",
                value=meta["max"] * mult,
                step=meta.get("step", 0.01) * mult,
                debounce=True,
                className="axis-bound-input",
            ),
        ], style={"display": "flex", "alignItems": "center", "marginTop": "4px"}),
    ], style={"marginBottom": "8px"})


# --- Simulator: Left panel ---
sim_left_panel = html.Div([
    html.H3("Axis Selection", style={"marginTop": "0"}),
    html.Label("X-Axis Parameter", style={"fontWeight": "600", "fontSize": "13px"}),
    dcc.Dropdown(id="x-axis-dropdown", options=AXIS_OPTIONS, value=DEFAULT_X,
                 clearable=False, style={"marginBottom": "4px"}),
    _make_axis_bounds("x", DEFAULT_X),

    html.Label("Y-Axis Parameter", style={"fontWeight": "600", "fontSize": "13px"}),
    dcc.Dropdown(id="y-axis-dropdown", options=AXIS_OPTIONS, value=DEFAULT_Y,
                 clearable=False, style={"marginBottom": "4px"}),
    _make_axis_bounds("y", DEFAULT_Y),

    html.Hr(),
    html.H3("Vigilent Parameters"),
    *vig_controls,

    html.Hr(),
    html.H3("Electricity Rate Lookup"),
    html.Div([
        dcc.Input(id="zip-input", type="text", placeholder="Zip code (e.g. 95110)",
                  style={"width": "130px", "padding": "6px", "border": "1px solid #ccc",
                         "borderRadius": "4px", "fontSize": "13px"}),
        html.Button("Lookup", id="zip-lookup-btn",
                     style={"marginLeft": "8px", "padding": "6px 14px",
                            "backgroundColor": "#1075E8", "color": "white",
                            "border": "none", "borderRadius": "4px",
                            "cursor": "pointer", "fontSize": "13px"}),
    ], style={"display": "flex", "alignItems": "center", "marginBottom": "10px"}),
    html.Div(id="rate-results", style={"fontSize": "13px", "marginBottom": "8px"}),
    html.Button("Use Selected Rate", id="use-rate-btn",
                 style={"padding": "6px 14px", "backgroundColor": "#25ac01",
                        "color": "white", "border": "none", "borderRadius": "4px",
                        "cursor": "pointer", "fontSize": "13px", "display": "none"}),
    dcc.Store(id="fetched-rate-store", data=None),
], style={"width": "280px", "padding": "16px", "overflowY": "auto",
          "borderRight": "1px solid #ddd", "flexShrink": "0"})

# --- Simulator: Center (heatmap) ---
sim_center_panel = html.Div([
    dcc.Graph(id="heatmap",
              config={"displayModeBar": True, "scrollZoom": False},
              style={"height": "calc(100vh - 120px)"}),
], style={"flex": "1", "padding": "8px 12px", "minWidth": "0"})

# --- Simulator: Right panel (DC param sliders) ---
dc_slider_divs = []
for k, meta in DC_PARAMS.items():
    display_mult = meta.get("display_mult", 1)
    step = meta.get("step", 0.01) * display_mult
    mn = meta["min"] * display_mult
    mx = meta["max"] * display_mult
    default = meta["default"] * display_mult

    marks = {}
    if display_mult > 1:
        marks = {mn: f"{mn:.0f}%", mx: f"{mx:.0f}%"}
    elif "," in meta.get("fmt", ""):
        marks = {mn: f"${mn:,.0f}", mx: f"${mx:,.0f}"}
    else:
        marks = {mn: _fmt_val(k, meta["min"]), mx: _fmt_val(k, meta["max"])}

    dc_slider_divs.append(
        html.Div([
            html.Label(meta["label"], style={"fontWeight": "600", "fontSize": "13px",
                                              "marginBottom": "6px", "display": "block"}),
            html.Div([
                dcc.Slider(
                    id=f"dc-slider-{k}",
                    min=mn, max=mx, step=step,
                    value=default,
                    marks=marks,
                    tooltip={"placement": "bottom", "always_visible": False},
                    updatemode="drag",
                ),
                dcc.Input(
                    id=f"dc-input-{k}",
                    type="number",
                    value=default,
                    min=mn, max=mx, step=step,
                    debounce=True,
                    style={"width": "80px", "textAlign": "center", "marginLeft": "8px",
                           "border": "1px solid #ccc", "borderRadius": "4px",
                           "padding": "4px", "fontSize": "13px"},
                ),
            ], style={"display": "flex", "alignItems": "center", "gap": "4px"}),
        ], id=f"dc-row-{k}", style={"marginBottom": "18px"})
    )

sim_right_panel = html.Div([
    html.H3("Fixed Parameters", style={"marginTop": "0"}),
    html.P("Adjust the parameters not on the axes:",
           style={"fontSize": "12px", "color": "#666", "marginBottom": "12px"}),
    *dc_slider_divs,
], style={"width": "300px", "padding": "16px", "overflowY": "auto",
          "borderLeft": "1px solid #ddd", "flexShrink": "0"})

# --- Simulator: Bottom bar ---
sim_bottom_bar = html.Div([
    html.Div(id="hover-info",
             children="Hover over the heatmap to see score details.",
             style={"fontSize": "13px", "color": "#555"}),
], style={"padding": "8px 20px", "borderTop": "1px solid #ddd",
          "backgroundColor": "#f8f9fa", "minHeight": "36px"})

# --- Full simulator tab content ---
simulator_content = html.Div([
    html.Div([sim_left_panel, sim_center_panel, sim_right_panel],
             style={"display": "flex", "flex": "1", "overflow": "hidden"}),
    sim_bottom_bar,
], style={"display": "flex", "flexDirection": "column", "flex": "1"})


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIMIZER TAB LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

def _make_opt_vig_control(param_key, meta):
    """Create a labeled input for a Vigilent parameter on the optimizer tab."""
    display_mult = meta.get("display_mult", 1)
    mn = meta["min"] * display_mult
    mx = meta["max"] * display_mult
    default = meta["default"] * display_mult
    step = meta.get("step", 0.01) * display_mult
    return html.Div([
        html.Label(meta["label"], style={"fontWeight": "600", "fontSize": "13px",
                                          "marginBottom": "4px", "display": "block",
                                          "whiteSpace": "nowrap", "overflow": "hidden",
                                          "textOverflow": "ellipsis"}),
        dcc.Input(
            id=f"opt-vig-{param_key}",
            type="number",
            value=default,
            min=mn, max=mx, step=step,
            style={"width": "100%", "padding": "8px", "border": "1px solid #ccc",
                   "borderRadius": "6px", "fontSize": "14px", "boxSizing": "border-box"},
            debounce=True,
        ),
    ], style={"flex": "1 1 calc(50% - 8px)", "minWidth": "140px"})


# --- Scoring config inputs ---
SCORING_FACTOR_LABELS = {
    "savings_per_mw": "$/MW Savings",
    "water_savings_pct": "Water Savings",
    "impact_on_opex": "OPEX Impact",
    "payback_period": "Payback Period (inverted)",
    "load_growth": "Load Growth",
}

_input_style = {"width": "80px", "padding": "6px", "border": "1px solid #ccc",
                "borderRadius": "4px", "fontSize": "13px", "textAlign": "center",
                "boxSizing": "border-box"}

def _make_scoring_row(key, cfg):
    """Create a row for editing a scoring factor's weight, min and max threshold."""
    return html.Tr([
        html.Td(SCORING_FACTOR_LABELS.get(key, key),
                style={"padding": "6px 10px", "fontSize": "13px", "fontWeight": "500",
                       "whiteSpace": "nowrap"}),
        html.Td(
            dcc.Input(
                id=f"opt-weight-{key}", type="number",
                value=cfg["weight"], min=0, max=1, step=0.01,
                debounce=True, style=_input_style,
            ),
            style={"padding": "4px 6px", "textAlign": "center"},
        ),
        html.Td(
            dcc.Input(
                id=f"opt-min-{key}", type="number",
                value=cfg.get("min", 0), step=0.01,
                debounce=True, style=_input_style,
            ),
            style={"padding": "4px 6px", "textAlign": "center"},
        ),
        html.Td(
            dcc.Input(
                id=f"opt-max-{key}", type="number",
                value=cfg["max"], min=0.001, step=0.01,
                debounce=True, style=_input_style,
            ),
            style={"padding": "4px 6px", "textAlign": "center"},
        ),
    ])


scoring_config_table = html.Div([
    html.H3("Scoring Configuration", style={"marginTop": "0", "marginBottom": "8px"}),
    html.P("Adjust the weights (must sum to 1.0) and min/max thresholds for each scoring factor.",
           style={"fontSize": "12px", "color": "#666", "marginBottom": "10px", "lineHeight": "1.4"}),
    html.Table([
        html.Thead(html.Tr([
            html.Th("Factor", style={"padding": "8px 10px", "background": "#f0f4ff",
                                      "textAlign": "left", "fontSize": "12px", "fontWeight": "600"}),
            html.Th("Weight", style={"padding": "8px 10px", "background": "#f0f4ff",
                                      "textAlign": "center", "fontSize": "12px", "fontWeight": "600"}),
            html.Th("Min", style={"padding": "8px 10px", "background": "#f0f4ff",
                                   "textAlign": "center", "fontSize": "12px", "fontWeight": "600"}),
            html.Th("Max", style={"padding": "8px 10px", "background": "#f0f4ff",
                                   "textAlign": "center", "fontSize": "12px", "fontWeight": "600"}),
        ])),
        html.Tbody([
            _make_scoring_row(k, SCORING_CONFIG[k])
            for k in SCORING_CONFIG
        ]),
    ], style={"width": "100%", "borderCollapse": "collapse", "marginBottom": "8px"}),
    html.Div(id="opt-weight-status", style={"fontSize": "12px", "color": "#666"}),
], className="opt-section", style={"marginTop": "20px"})

optimizer_content = html.Div([
    html.Div([
        # --- Inputs area (two sections side-by-side on wide screens) ---
        html.Div([
            # Vigilent parameters
            html.Div([
                html.H3("Vigilent Parameters", style={"marginTop": "0", "marginBottom": "14px"}),
                html.P("Set Vigilent's product parameters, then run the optimizer to find the ideal "
                       "data center profile that maximizes the composite score.",
                       style={"fontSize": "13px", "color": "#666", "marginBottom": "16px",
                              "lineHeight": "1.5"}),
                html.Div([
                    _make_opt_vig_control(k, VIGILENT_PARAMS[k])
                    for k in VIGILENT_PARAMS
                ], style={"display": "flex", "flexWrap": "wrap", "gap": "14px",
                          "marginBottom": "20px"}),
            ], className="opt-section"),

            # Scoring config
            scoring_config_table,

            # Run button
            html.Div([
                html.Div([
                    dcc.Checklist(
                        id="opt-include-locations",
                        options=[{"label": " Include location matching (slower — queries OpenEI)",
                                  "value": "yes"}],
                        value=[],
                        style={"fontSize": "13px", "color": "#555"},
                    ),
                ], style={"marginBottom": "16px"}),

                html.Button(
                    "Run Optimization",
                    id="opt-run-btn",
                    style={"padding": "12px 32px", "backgroundColor": BRAND,
                           "color": "white", "border": "none", "borderRadius": "8px",
                           "cursor": "pointer", "fontSize": "15px", "fontWeight": "600",
                           "boxShadow": "0 2px 8px rgba(16,117,232,0.3)",
                           "transition": "all 0.2s"},
                ),
                html.Div(id="opt-status",
                         style={"marginTop": "12px", "fontSize": "13px", "color": "#666"}),
            ], style={"marginTop": "20px"}),
        ], style={"width": "100%", "maxWidth": "750px"}),
    ], style={"padding": "24px 30px"}),

    # --- Results area ---
    html.Div(id="opt-results",
             style={"padding": "0 30px 30px 30px"}),

    # Store for optimization results
    dcc.Store(id="opt-results-store", data=None),

], style={"flex": "1", "overflowY": "auto", "background": "#f5f6fa"})


# ═══════════════════════════════════════════════════════════════════════════════
# EJ CALCULATOR TAB LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

EJ_TEAL = "#0097A7"
EJ_GREEN = "#25ac01"
EJ_RED = "#D32F2F"

_ej_input_style = {"width": "100%", "padding": "10px", "border": "1px solid #ccc",
                    "borderRadius": "6px", "fontSize": "14px", "boxSizing": "border-box"}

ej_content = html.Div([
    html.Div([
        # --- Intro ---
        html.Div([
            html.H2("Environmental Justice Impact Calculator",
                     style={"margin": "0 0 8px 0", "color": "#1b285b", "fontSize": "22px"}),
            html.P("Analyze how Vigilent's energy savings help de-marginalize communities "
                   "burdened by high energy costs, water stress, and environmental pollution. "
                   "Enter a zip code and data center parameters to see the community's current "
                   "marginalization profile and Vigilent's de-marginalization impact.",
                   style={"fontSize": "14px", "color": "#666", "lineHeight": "1.6",
                          "margin": "0 0 20px 0"}),
        ]),

        # --- Input section ---
        html.Div([
            html.H3("Input Parameters", style={"marginTop": "0", "marginBottom": "14px"}),
            html.Div([
                # Row 1: Zip + DC Size + PUE
                html.Div([
                    html.Label("Zip Code", style={"fontWeight": "600", "fontSize": "13px",
                                                    "display": "block", "marginBottom": "4px"}),
                    dcc.Input(id="ej-zip", type="text", value="20147",
                              placeholder="e.g. 20147",
                              style={**_ej_input_style, "width": "140px"}),
                ], style={"flex": "0 0 140px"}),
                html.Div([
                    html.Label("DC Size (MW)", style={"fontWeight": "600", "fontSize": "13px",
                                                        "display": "block", "marginBottom": "4px"}),
                    dcc.Input(id="ej-dc-size", type="number", value=20, min=1, max=500, step=1,
                              style=_ej_input_style),
                ], style={"flex": "1", "minWidth": "100px"}),
                html.Div([
                    html.Label("Baseline PUE", style={"fontWeight": "600", "fontSize": "13px",
                                                        "display": "block", "marginBottom": "4px"}),
                    dcc.Input(id="ej-pue", type="number", value=1.55, min=1.0, max=3.0, step=0.01,
                              style=_ej_input_style),
                ], style={"flex": "1", "minWidth": "100px"}),
                html.Div([
                    html.Label("Load Growth (%)", style={"fontWeight": "600", "fontSize": "13px",
                                                           "display": "block", "marginBottom": "4px"}),
                    dcc.Input(id="ej-growth", type="number", value=10, min=0, max=30, step=1,
                              style=_ej_input_style),
                ], style={"flex": "1", "minWidth": "100px"}),
                html.Div([
                    html.Label("Energy Reduction w/ Vigilent (%)",
                               style={"fontWeight": "600", "fontSize": "13px",
                                      "display": "block", "marginBottom": "4px"}),
                    dcc.Input(id="ej-energy-red", type="number", value=10, min=1, max=40, step=1,
                              style=_ej_input_style),
                ], style={"flex": "1", "minWidth": "140px"}),
            ], style={"display": "flex", "gap": "14px", "flexWrap": "wrap", "marginBottom": "20px"}),

            html.Button(
                "Calculate Impact",
                id="ej-calc-btn",
                style={"padding": "12px 32px", "backgroundColor": EJ_TEAL,
                       "color": "white", "border": "none", "borderRadius": "8px",
                       "cursor": "pointer", "fontSize": "15px", "fontWeight": "600",
                       "boxShadow": "0 2px 8px rgba(0,151,167,0.3)"},
            ),
            html.Div(id="ej-status",
                     style={"marginTop": "10px", "fontSize": "13px", "color": "#666"}),
        ], className="opt-section"),

        # --- Results area ---
        html.Div(id="ej-results",
                 style={"marginTop": "20px"}),

    ], style={"padding": "24px 40px", "maxWidth": "1400px", "margin": "0 auto"}),
], style={"flex": "1", "overflowY": "auto", "background": "#f5f6fa", "width": "100%"})


# ═══════════════════════════════════════════════════════════════════════════════
# DC FINDER TAB LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

FINDER_TEAL = "#0097A7"

# Param pair options for tradeoff explorer
_FINDER_PAIR_OPTIONS = [
    {"label": DC_PARAMS[k]["label"], "value": k}
    for k in DC_PARAMS
]

def _make_finder_vig_control(param_key, meta):
    """Create a labeled input for a Vigilent parameter on the DC Finder tab."""
    display_mult = meta.get("display_mult", 1)
    mn = meta["min"] * display_mult
    mx = meta["max"] * display_mult
    default = meta["default"] * display_mult
    step = meta.get("step", 0.01) * display_mult
    return html.Div([
        html.Label(meta["label"], style={"fontWeight": "600", "fontSize": "13px",
                                          "marginBottom": "4px", "display": "block",
                                          "whiteSpace": "nowrap", "overflow": "hidden",
                                          "textOverflow": "ellipsis"}),
        dcc.Input(
            id=f"finder-vig-{param_key}",
            type="number",
            value=default,
            min=mn, max=mx, step=step,
            style={"width": "100%", "padding": "8px", "border": "1px solid #ccc",
                   "borderRadius": "6px", "fontSize": "14px", "boxSizing": "border-box"},
            debounce=True,
        ),
    ], style={"flex": "1 1 calc(25% - 12px)", "minWidth": "140px"})


finder_content = html.Div([
    html.Div([
        # --- Intro ---
        html.H2("DC Finder",
                 style={"margin": "0 0 4px 0", "color": "#1b285b", "fontSize": "22px"}),
        html.P("Find all data center profiles that benefit most from Vigilent. "
               "Unlike the Simulator (which holds some variables constant), the DC Finder "
               "sweeps all 5 DC parameters simultaneously to find every qualifying combination.",
               style={"fontSize": "14px", "color": "#666", "lineHeight": "1.6",
                      "margin": "0 0 20px 0"}),

        # --- Input section ---
        html.Div([
            html.H3("Vigilent Parameters",
                     style={"marginTop": "0", "marginBottom": "14px"}),
            html.Div([
                _make_finder_vig_control(k, VIGILENT_PARAMS[k])
                for k in VIGILENT_PARAMS
            ], style={"display": "flex", "flexWrap": "wrap", "gap": "14px",
                      "marginBottom": "20px"}),

            # Threshold selector
            html.Div([
                html.Label("Score Threshold",
                           style={"fontWeight": "600", "fontSize": "13px",
                                  "marginBottom": "4px", "display": "block"}),
                dcc.Dropdown(
                    id="finder-threshold",
                    options=[
                        {"label": "50 — Moderate", "value": 50},
                        {"label": "75 — Good", "value": 75},
                        {"label": "90 — Excellent", "value": 90},
                    ],
                    value=75,
                    clearable=False,
                    style={"width": "220px", "fontSize": "14px"},
                ),
            ], style={"marginBottom": "20px"}),

            html.Button(
                "Find Matching DCs",
                id="finder-run-btn",
                style={"padding": "12px 32px", "backgroundColor": FINDER_TEAL,
                       "color": "white", "border": "none", "borderRadius": "8px",
                       "cursor": "pointer", "fontSize": "15px", "fontWeight": "600",
                       "boxShadow": "0 2px 8px rgba(0,151,167,0.3)"},
            ),
            html.Div(id="finder-status",
                     style={"marginTop": "10px", "fontSize": "13px", "color": "#666"}),
        ], className="opt-section"),

        # --- Results area ---
        html.Div(id="finder-results",
                 style={"marginTop": "20px"}),

        # Store for the 5D composite array (serialized as list)
        dcc.Store(id="finder-composite-store", data=None),
        dcc.Store(id="finder-grids-store", data=None),
        dcc.Store(id="finder-threshold-store", data=None),

    ], style={"padding": "24px 40px", "maxWidth": "1400px", "margin": "0 auto"}),
], style={"flex": "1", "overflowY": "auto", "background": "#f5f6fa", "width": "100%"})


# ═══════════════════════════════════════════════════════════════════════════════
# FULL APP LAYOUT
# ═══════════════════════════════════════════════════════════════════════════════

app.layout = html.Div([
    # Title bar
    html.Div([
        html.Div([
            html.H1("Vigilent",
                    style={"margin": "0", "fontSize": "22px", "fontWeight": "700",
                           "color": "white"}),
            html.Span("Data Center Scoring & Optimization",
                      style={"fontSize": "13px", "color": "rgba(255,255,255,0.8)",
                             "marginLeft": "12px"}),
        ], style={"display": "flex", "alignItems": "baseline"}),
    ], style={"padding": "14px 20px",
              "background": f"linear-gradient(135deg, {BRAND}, {BRAND_DARK})"}),

    # Tab bar
    html.Div([
        html.Button("Simulator", id="tab-sim-btn", className="tab-btn active",
                     n_clicks=0),
        html.Button("Optimizer", id="tab-opt-btn", className="tab-btn",
                     n_clicks=0),
        html.Button("EJ Calculator", id="tab-ej-btn", className="tab-btn",
                     n_clicks=0),
        html.Button("DC Finder", id="tab-finder-btn", className="tab-btn",
                     n_clicks=0),
    ], className="tab-bar"),

    # Tab content
    html.Div(id="tab-content",
             children=[simulator_content],
             style={"display": "flex", "flex": "1", "overflow": "hidden"}),

    # Hidden stores
    dcc.Store(id="active-tab", data="simulator"),
    dcc.Store(id="params-store", data=ALL_DEFAULTS),
], style={"display": "flex", "flexDirection": "column", "height": "100vh",
          "fontFamily": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif"})


# ═══════════════════════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

# --- 0. Tab switching ---
@callback(
    Output("tab-content", "children"),
    Output("tab-sim-btn", "className"),
    Output("tab-opt-btn", "className"),
    Output("tab-ej-btn", "className"),
    Output("tab-finder-btn", "className"),
    Output("active-tab", "data"),
    Input("tab-sim-btn", "n_clicks"),
    Input("tab-opt-btn", "n_clicks"),
    Input("tab-ej-btn", "n_clicks"),
    Input("tab-finder-btn", "n_clicks"),
    State("active-tab", "data"),
)
def switch_tab(sim_clicks, opt_clicks, ej_clicks, finder_clicks, current_tab):
    triggered = ctx.triggered_id
    if triggered == "tab-opt-btn":
        return optimizer_content, "tab-btn", "tab-btn active", "tab-btn", "tab-btn", "optimizer"
    if triggered == "tab-ej-btn":
        return ej_content, "tab-btn", "tab-btn", "tab-btn active", "tab-btn", "ej"
    if triggered == "tab-finder-btn":
        return finder_content, "tab-btn", "tab-btn", "tab-btn", "tab-btn active", "finder"
    # Default to simulator
    return simulator_content, "tab-btn active", "tab-btn", "tab-btn", "tab-btn", "simulator"


# --- 1. Ensure X and Y axes are never the same ---
@callback(
    Output("y-axis-dropdown", "options"),
    Output("x-axis-dropdown", "options"),
    Input("x-axis-dropdown", "value"),
    Input("y-axis-dropdown", "value"),
)
def update_axis_options(x_val, y_val):
    y_opts = [{"label": o["label"], "value": o["value"],
               "disabled": o["value"] == x_val} for o in AXIS_OPTIONS]
    x_opts = [{"label": o["label"], "value": o["value"],
               "disabled": o["value"] == y_val} for o in AXIS_OPTIONS]
    return y_opts, x_opts


# --- 2. Update axis bound defaults when axis selection changes ---
@callback(
    Output("x-bound-min", "value"),
    Output("x-bound-max", "value"),
    Output("x-bound-min", "step"),
    Output("x-bound-max", "step"),
    Input("x-axis-dropdown", "value"),
)
def update_x_bounds(x_param):
    meta = DC_PARAMS[x_param]
    mult = meta.get("display_mult", 1)
    step = meta.get("step", 0.01) * mult
    return meta["min"] * mult, meta["max"] * mult, step, step


@callback(
    Output("y-bound-min", "value"),
    Output("y-bound-max", "value"),
    Output("y-bound-min", "step"),
    Output("y-bound-max", "step"),
    Input("y-axis-dropdown", "value"),
)
def update_y_bounds(y_param):
    meta = DC_PARAMS[y_param]
    mult = meta.get("display_mult", 1)
    step = meta.get("step", 0.01) * mult
    return meta["min"] * mult, meta["max"] * mult, step, step


# --- 3. Show/hide DC slider rows based on axis selection ---
for _k in DC_PARAMS:
    @callback(
        Output(f"dc-row-{_k}", "style"),
        Input("x-axis-dropdown", "value"),
        Input("y-axis-dropdown", "value"),
    )
    def toggle_slider(_x, _y, key=_k):
        if key in (_x, _y):
            return {"display": "none"}
        return {"marginBottom": "18px"}


# --- 4. Sync DC sliders ↔ text inputs ---
for _k in DC_PARAMS:
    @callback(
        Output(f"dc-input-{_k}", "value"),
        Input(f"dc-slider-{_k}", "value"),
        prevent_initial_call=True,
    )
    def sync_dc_slider_to_input(slider_val, key=_k):
        return slider_val

    @callback(
        Output(f"dc-slider-{_k}", "value"),
        Input(f"dc-input-{_k}", "value"),
        prevent_initial_call=True,
    )
    def sync_dc_input_to_slider(input_val, key=_k):
        return input_val


# --- 5. OpenEI rate lookup ---
@callback(
    Output("rate-results", "children"),
    Output("fetched-rate-store", "data"),
    Output("use-rate-btn", "style"),
    Input("zip-lookup-btn", "n_clicks"),
    State("zip-input", "value"),
    prevent_initial_call=True,
)
def lookup_rate(n_clicks, zip_code):
    if not zip_code or not zip_code.strip():
        return "Enter a zip code.", None, {"display": "none"}

    rates = lookup_electricity_rate(zip_code.strip())
    if not rates or rates[0].get("blended_rate_per_kwh") is None:
        msg = rates[0].get("rate_name", "No rates found") if rates else "No rates found"
        return html.Span(msg, style={"color": "#c00"}), None, {"display": "none"}

    items = []
    best_rate = None
    for r in rates[:5]:
        rate = r["blended_rate_per_kwh"]
        if best_rate is None:
            best_rate = rate
        items.append(
            html.Div(f"{r['utility']} -- {r['rate_name']}: ${rate:.4f}/kWh",
                     style={"padding": "3px 0", "fontSize": "12px"})
        )

    btn_style = {"padding": "6px 14px", "backgroundColor": "#25ac01",
                 "color": "white", "border": "none", "borderRadius": "4px",
                 "cursor": "pointer", "fontSize": "13px", "marginTop": "6px"}

    return html.Div(items), best_rate, btn_style


# --- 6. "Use This Rate" → smart routing ---
@callback(
    Output("dc-slider-electricity_price", "value", allow_duplicate=True),
    Output("dc-input-electricity_price", "value", allow_duplicate=True),
    Input("use-rate-btn", "n_clicks"),
    State("fetched-rate-store", "data"),
    State("x-axis-dropdown", "value"),
    State("y-axis-dropdown", "value"),
    prevent_initial_call=True,
)
def use_fetched_rate(n_clicks, rate, x_param, y_param):
    if rate is None:
        return no_update, no_update
    rounded = round(rate, 2)
    return rounded, rounded


# --- 7. MAIN HEATMAP UPDATE ---
@callback(
    Output("heatmap", "figure"),
    Input("x-axis-dropdown", "value"),
    Input("y-axis-dropdown", "value"),
    Input("x-bound-min", "value"),
    Input("x-bound-max", "value"),
    Input("y-bound-min", "value"),
    Input("y-bound-max", "value"),
    Input("dc-slider-dc_size_mw", "value"),
    Input("dc-slider-baseline_pue", "value"),
    Input("dc-slider-electricity_price", "value"),
    Input("dc-slider-load_growth_rate", "value"),
    Input("dc-slider-energy_pct_opex", "value"),
    Input("vig-num_years", "value"),
    Input("vig-investment_cost", "value"),
    Input("vig-energy_reduction_pct", "value"),
    Input("vig-water_reduction_pct", "value"),
    State("fetched-rate-store", "data"),
)
def update_heatmap(x_param, y_param,
                   x_bound_min, x_bound_max, y_bound_min, y_bound_max,
                   dc_size_mw, baseline_pue, electricity_price,
                   load_growth_rate, energy_pct_opex,
                   num_years, investment_cost,
                   energy_reduction_pct, water_reduction_pct,
                   fetched_rate):

    # Convert display values back to raw (undo display_mult)
    dc_raw = {
        "dc_size_mw": dc_size_mw or DC_PARAMS["dc_size_mw"]["default"],
        "baseline_pue": baseline_pue or DC_PARAMS["baseline_pue"]["default"],
        "electricity_price": electricity_price or DC_PARAMS["electricity_price"]["default"],
        "load_growth_rate": (load_growth_rate or 0) / DC_PARAMS["load_growth_rate"].get("display_mult", 1),
        "energy_pct_opex": (energy_pct_opex or 0) / DC_PARAMS["energy_pct_opex"].get("display_mult", 1),
    }
    vig_raw = {
        "num_years": int(num_years or VIGILENT_PARAMS["num_years"]["default"]),
        "investment_cost": investment_cost or VIGILENT_PARAMS["investment_cost"]["default"],
        "energy_reduction_pct": (energy_reduction_pct or 0) / VIGILENT_PARAMS["energy_reduction_pct"].get("display_mult", 1),
        "water_reduction_pct": (water_reduction_pct or 0) / VIGILENT_PARAMS["water_reduction_pct"].get("display_mult", 1),
    }

    fixed = {**dc_raw, **vig_raw}

    # Build axis arrays using custom bounds
    x_meta = DC_PARAMS[x_param]
    y_meta = DC_PARAMS[y_param]
    x_mult = x_meta.get("display_mult", 1)
    y_mult = y_meta.get("display_mult", 1)

    # Convert bounds from display to raw
    x_lo = (x_bound_min or x_meta["min"] * x_mult) / x_mult
    x_hi = (x_bound_max or x_meta["max"] * x_mult) / x_mult
    y_lo = (y_bound_min or y_meta["min"] * y_mult) / y_mult
    y_hi = (y_bound_max or y_meta["max"] * y_mult) / y_mult

    # Ensure lo < hi
    if x_lo >= x_hi:
        x_lo, x_hi = x_meta["min"], x_meta["max"]
    if y_lo >= y_hi:
        y_lo, y_hi = y_meta["min"], y_meta["max"]

    x_vals = np.linspace(x_lo, x_hi, GRID_SIZE)
    y_vals = np.linspace(y_lo, y_hi, GRID_SIZE)

    # Compute grid
    Z = compute_score_grid(x_param, x_vals, y_param, y_vals, fixed)

    # Build hover text
    hover_text = []
    for i, yv in enumerate(y_vals):
        row = []
        for j, xv in enumerate(x_vals):
            score = Z[i, j]
            if score >= 90:
                zone = "Excellent"
            elif score >= 75:
                zone = "Good"
            elif score >= 50:
                zone = "Moderate"
            elif score >= 25:
                zone = "Low"
            else:
                zone = "Poor"
            txt = (
                f"<b>Score: {score:.1f} ({zone})</b><br>"
                f"{x_meta['label']}: {_fmt_val(x_param, xv)}<br>"
                f"{y_meta['label']}: {_fmt_val(y_param, yv)}"
            )
            row.append(txt)
        hover_text.append(row)

    # Build held-constant annotation
    dc_keys = list(DC_PARAMS.keys())
    vig_keys = list(VIGILENT_PARAMS.keys())
    held = []
    for k in dc_keys:
        if k not in (x_param, y_param):
            held.append(f"{DC_PARAMS[k]['label']}: {_fmt_val(k, fixed[k])}")
    for k in vig_keys:
        held.append(f"{VIGILENT_PARAMS[k]['label']}: {_fmt_val(k, fixed[k])}")
    held_text = "Held constant: " + " | ".join(held)

    # Create figure
    fig = go.Figure(data=go.Heatmap(
        z=Z,
        x=x_vals,
        y=y_vals,
        colorscale=COLORSCALE,
        zmin=0, zmax=100,
        text=hover_text,
        hoverinfo="text",
        colorbar=dict(
            title="Composite Score",
            tickvals=[0, 12.5, 25, 37.5, 50, 62.5, 75, 82.5, 90, 95, 100],
            ticktext=["0", "Poor", "25", "Low", "50", "Moderate", "75", "Good", "90", "Excellent", "100"],
            len=0.85,
            thickness=18,
            outlinewidth=1,
            outlinecolor="#ccc",
        ),
    ))

    # --- Draw reference line if electricity rate was looked up ---
    if fetched_rate is not None:
        if x_param == "electricity_price":
            fig.add_shape(
                type="line",
                x0=fetched_rate, x1=fetched_rate,
                y0=y_lo, y1=y_hi,
                line=dict(color="#FF6600", width=2.5, dash="dash"),
            )
            fig.add_annotation(
                x=fetched_rate, y=y_hi,
                text=f"Lookup: ${fetched_rate:.3f}/kWh",
                showarrow=True, arrowhead=2,
                ax=40, ay=-25,
                font=dict(size=11, color="#FF6600", family="Arial Black"),
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor="#FF6600", borderwidth=1, borderpad=3,
            )
        elif y_param == "electricity_price":
            fig.add_shape(
                type="line",
                x0=x_lo, x1=x_hi,
                y0=fetched_rate, y1=fetched_rate,
                line=dict(color="#FF6600", width=2.5, dash="dash"),
            )
            fig.add_annotation(
                x=x_hi, y=fetched_rate,
                text=f"Lookup: ${fetched_rate:.3f}/kWh",
                showarrow=True, arrowhead=2,
                ax=-40, ay=-25,
                font=dict(size=11, color="#FF6600", family="Arial Black"),
                bgcolor="rgba(255,255,255,0.85)",
                bordercolor="#FF6600", borderwidth=1, borderpad=3,
            )

    # Format axis tick labels
    x_tick_fmt = x_meta.get("fmt", ".2f")
    y_tick_fmt = y_meta.get("fmt", ".2f")

    fig.update_layout(
        uirevision=f"{x_param}-{y_param}",
        title=dict(
            text=f"Composite Score: {x_meta['label']} vs {y_meta['label']}",
            font=dict(size=16),
        ),
        xaxis=dict(
            title=x_meta["label"],
            tickformat=x_tick_fmt if "%" not in x_tick_fmt else ".0%",
        ),
        yaxis=dict(
            title=y_meta["label"],
            tickformat=y_tick_fmt if "%" not in y_tick_fmt else ".0%",
        ),
        margin=dict(l=60, r=20, t=50, b=80),
        paper_bgcolor="white",
        plot_bgcolor="white",
        annotations=[
            dict(
                text=held_text,
                xref="paper", yref="paper",
                x=0.5, y=-0.12,
                showarrow=False,
                font=dict(size=11, color="#666"),
                xanchor="center",
            )
        ],
    )

    return fig


# --- 8. Hover info in bottom bar ---
@callback(
    Output("hover-info", "children"),
    Input("heatmap", "hoverData"),
    State("x-axis-dropdown", "value"),
    State("y-axis-dropdown", "value"),
)
def update_hover_info(hover_data, x_param, y_param):
    if hover_data is None:
        return "Hover over the heatmap to see score details."

    try:
        point = hover_data["points"][0]
        z = point.get("z", 0)
        x_val = point.get("x", 0)
        y_val = point.get("y", 0)

        x_label = DC_PARAMS[x_param]["label"]
        y_label = DC_PARAMS[y_param]["label"]

        if z >= 90:
            zone, color = "Excellent", "#25ac01"
        elif z >= 75:
            zone, color = "Good", BRAND
        elif z >= 50:
            zone, color = "Moderate", "#88b4f2"
        elif z >= 25:
            zone, color = "Low", "#999"
        else:
            zone, color = "Poor", "#ccc"

        return html.Span([
            html.B(f"Score: {z:.1f}", style={"color": color}),
            f" ({zone})  |  ",
            f"{x_label}: {_fmt_val(x_param, x_val)}  |  ",
            f"{y_label}: {_fmt_val(y_param, y_val)}",
        ])
    except Exception:
        return "Hover over the heatmap to see score details."


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIMIZER CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

@callback(
    Output("opt-results", "children"),
    Output("opt-status", "children"),
    Input("opt-run-btn", "n_clicks"),
    State("opt-vig-num_years", "value"),
    State("opt-vig-investment_cost", "value"),
    State("opt-vig-energy_reduction_pct", "value"),
    State("opt-vig-water_reduction_pct", "value"),
    State("opt-include-locations", "value"),
    # Scoring config weights
    State("opt-weight-savings_per_mw", "value"),
    State("opt-weight-water_savings_pct", "value"),
    State("opt-weight-impact_on_opex", "value"),
    State("opt-weight-payback_period", "value"),
    State("opt-weight-load_growth", "value"),
    # Scoring config min thresholds
    State("opt-min-savings_per_mw", "value"),
    State("opt-min-water_savings_pct", "value"),
    State("opt-min-impact_on_opex", "value"),
    State("opt-min-payback_period", "value"),
    State("opt-min-load_growth", "value"),
    # Scoring config max thresholds
    State("opt-max-savings_per_mw", "value"),
    State("opt-max-water_savings_pct", "value"),
    State("opt-max-impact_on_opex", "value"),
    State("opt-max-payback_period", "value"),
    State("opt-max-load_growth", "value"),
    prevent_initial_call=True,
)
def run_optimizer_callback(n_clicks, num_years, investment_cost,
                           energy_reduction_pct, water_reduction_pct,
                           include_locations,
                           w_spm, w_ws, w_io, w_pp, w_lg,
                           mn_spm, mn_ws, mn_io, mn_pp, mn_lg,
                           m_spm, m_ws, m_io, m_pp, m_lg):
    if not n_clicks:
        return no_update, no_update

    # Build custom scoring config from inputs
    custom_scoring = {
        "savings_per_mw":    {"weight": w_spm or 0.35, "min": mn_spm or 0, "max": m_spm or 300_000},
        "water_savings_pct": {"weight": w_ws  or 0.10, "min": mn_ws  or 0, "max": m_ws  or 0.08},
        "impact_on_opex":    {"weight": w_io  or 0.20, "min": mn_io  or 0, "max": m_io  or 0.10},
        "payback_period":    {"weight": w_pp  or 0.25, "min": mn_pp  or 0, "max": m_pp  or 5.0},
        "load_growth":       {"weight": w_lg  or 0.10, "min": mn_lg  or 0, "max": m_lg  or 0.15},
    }

    # Convert display values to raw
    vig = {
        "num_years": int(num_years or VIGILENT_PARAMS["num_years"]["default"]),
        "investment_cost": investment_cost or VIGILENT_PARAMS["investment_cost"]["default"],
        "energy_reduction_pct": (energy_reduction_pct or 0) / VIGILENT_PARAMS["energy_reduction_pct"].get("display_mult", 1),
        "water_reduction_pct": (water_reduction_pct or 0) / VIGILENT_PARAMS["water_reduction_pct"].get("display_mult", 1),
    }

    # 1. Optimize
    opt_result = run_optimization(vig, scoring_config=custom_scoring)
    sr = opt_result["score_result"]
    op = opt_result["optimal_params"]
    fs = sr["factor_scores"]

    # 2. Sensitivity
    sensitivity = run_sensitivity(op, scoring_config=custom_scoring)

    # 3. Sweet spots
    sweet_spots = run_sweet_spots(op, scoring_config=custom_scoring)

    # 3b. Score ≥ 90 sweet spots (tight ranges for ideal targeting)
    sweet_spots_90 = run_sweet_spots(op, threshold=90, scoring_config=custom_scoring)

    # 4. Location matching (optional)
    locations = []
    if include_locations and "yes" in include_locations:
        locations = match_locations(op["electricity_price"], tolerance=0.03)

    # ─── Build result display ───

    # Score hero
    score_color = "#25ac01" if sr["composite_score"] >= 90 else (
        BRAND if sr["composite_score"] >= 75 else "#88b4f2")
    score_hero = html.Div([
        html.Div(f"{sr['composite_score']:.1f}",
                 style={"fontSize": "64px", "fontWeight": "800", "color": score_color,
                        "lineHeight": "1"}),
        html.Div("Maximum Achievable Composite Score",
                 style={"fontSize": "14px", "color": "#666", "marginTop": "4px"}),
    ], style={"textAlign": "center", "padding": "24px", "background": "white",
              "borderRadius": "12px", "boxShadow": "0 2px 12px rgba(0,0,0,0.08)",
              "marginBottom": "20px"})

    # Vigilent params used
    vig_display = html.Div([
        html.H3("Vigilent Parameters (Fixed During Optimization)"),
        html.Div([
            html.Div([
                html.Div("Investment Cost", className="opt-card-label"),
                html.Div(f"${vig['investment_cost']:,.0f}", style={"fontSize": "16px", "fontWeight": "600"}),
            ], style={"background": "#f8f9fa", "borderRadius": "6px", "padding": "10px 14px", "flex": "1"}),
            html.Div([
                html.Div("Energy Reduction", className="opt-card-label"),
                html.Div(f"{vig['energy_reduction_pct']*100:.1f}%", style={"fontSize": "16px", "fontWeight": "600"}),
            ], style={"background": "#f8f9fa", "borderRadius": "6px", "padding": "10px 14px", "flex": "1"}),
            html.Div([
                html.Div("Water Reduction", className="opt-card-label"),
                html.Div(f"{vig['water_reduction_pct']*100:.1f}%", style={"fontSize": "16px", "fontWeight": "600"}),
            ], style={"background": "#f8f9fa", "borderRadius": "6px", "padding": "10px 14px", "flex": "1"}),
            html.Div([
                html.Div("Years", className="opt-card-label"),
                html.Div(f"{vig['num_years']}", style={"fontSize": "16px", "fontWeight": "600"}),
            ], style={"background": "#f8f9fa", "borderRadius": "6px", "padding": "10px 14px", "flex": "1"}),
        ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap"}),
    ], className="opt-section")

    # Optimal profile cards
    profile_cards = html.Div([
        html.Div([
            html.Div("DC Size", className="opt-card-label"),
            html.Div(f"{op['dc_size_mw']:.0f} MW", className="opt-card-value"),
        ], className="opt-card", style={"flex": "1"}),
        html.Div([
            html.Div("Baseline PUE", className="opt-card-label"),
            html.Div(f"{op['baseline_pue']:.2f}", className="opt-card-value"),
        ], className="opt-card", style={"flex": "1"}),
        html.Div([
            html.Div("Electricity Price", className="opt-card-label"),
            html.Div(f"${op['electricity_price']:.4f}/kWh", className="opt-card-value"),
        ], className="opt-card", style={"flex": "1"}),
        html.Div([
            html.Div("Load Growth", className="opt-card-label"),
            html.Div(f"{op['load_growth_rate']*100:.1f}%", className="opt-card-value"),
        ], className="opt-card", style={"flex": "1"}),
        html.Div([
            html.Div("Energy % of OPEX", className="opt-card-label"),
            html.Div(f"{op['energy_pct_opex']*100:.0f}%", className="opt-card-value"),
        ], className="opt-card", style={"flex": "1"}),
    ], style={"display": "flex", "gap": "14px", "marginBottom": "20px", "flexWrap": "wrap"})

    # ─── Score ≥ 90 Sweet Spot Ranges (below profile cards) ───
    def _fmt_range_val(key, val):
        """Format a parameter value for display in the range table."""
        meta = DC_PARAMS[key]
        mult = meta.get("display_mult", 1)
        v = val * mult
        if "%" in meta.get("fmt", ""):
            return f"{v:.1f}%"
        elif meta.get("unit") == "$/kWh":
            return f"${val:.2f}"
        elif meta.get("unit") == "MW":
            return f"{val:.0f} MW"
        else:
            return f"{v:.2f}"

    ss90_rows = []
    for key in DC_KEYS:
        meta = DC_PARAMS[key]
        ss90 = sweet_spots_90[key]
        lo = _fmt_range_val(key, ss90["min"])
        hi = _fmt_range_val(key, ss90["max"])
        opt = _fmt_range_val(key, ss90["optimal"])
        # Highlight if range is narrow (less than 20% of parameter span)
        param_span = meta["max"] - meta["min"]
        range_span = ss90["max"] - ss90["min"]
        range_pct = (range_span / param_span * 100) if param_span > 0 else 0
        range_color = "#25ac01" if range_pct >= 50 else ("#FF8C00" if range_pct >= 20 else "#c00")
        ss90_rows.append(html.Tr([
            html.Td(meta["label"], style={"padding": "6px 12px", "fontSize": "13px",
                                          "whiteSpace": "nowrap"}),
            html.Td(lo, style={"padding": "6px 10px", "textAlign": "center", "fontSize": "13px"}),
            html.Td(hi, style={"padding": "6px 10px", "textAlign": "center", "fontSize": "13px"}),
            html.Td(f"{range_pct:.0f}%", style={"padding": "6px 10px", "textAlign": "center",
                                                  "fontSize": "13px", "fontWeight": "600",
                                                  "color": range_color}),
        ]))

    sweet90_section = html.Div([
        html.Div([
            html.H4("\u2728 Score \u2265 90 Sweet Spot Ranges",
                     style={"margin": "0", "fontSize": "15px", "color": "#1b285b"}),
            html.Span("Parameter ranges that keep the composite score above 90",
                       style={"fontSize": "12px", "color": "#666"}),
        ], style={"marginBottom": "10px"}),
        html.Table([
            html.Thead(html.Tr([
                html.Th("Parameter", style={"padding": "8px 12px", "background": "#e8f5e9",
                                            "textAlign": "left", "fontSize": "12px",
                                            "borderBottom": "2px solid #25ac01"}),
                html.Th("Min", style={"padding": "8px 10px", "background": "#e8f5e9",
                                      "textAlign": "center", "fontSize": "12px",
                                      "borderBottom": "2px solid #25ac01"}),
                html.Th("Max", style={"padding": "8px 10px", "background": "#e8f5e9",
                                      "textAlign": "center", "fontSize": "12px",
                                      "borderBottom": "2px solid #25ac01"}),
                html.Th("Range Width", style={"padding": "8px 10px", "background": "#e8f5e9",
                                              "textAlign": "center", "fontSize": "12px",
                                              "borderBottom": "2px solid #25ac01"}),
            ])),
            html.Tbody(ss90_rows),
        ], style={"width": "100%", "borderCollapse": "collapse", "marginBottom": "0"}),
    ], style={"background": "white", "border": "2px solid #25ac01", "borderRadius": "10px",
              "padding": "16px", "marginBottom": "20px",
              "boxShadow": "0 2px 8px rgba(37,172,1,0.12)"})

    # Key metrics cards
    metrics_cards = html.Div([
        html.Div([
            html.Div("Annual Energy Cost", className="opt-card-label"),
            html.Div(f"${sr['annual_energy_cost']:,.0f}",
                     style={"fontSize": "20px", "fontWeight": "700", "color": "#333", "marginTop": "4px"}),
        ], className="opt-card", style={"flex": "1"}),
        html.Div([
            html.Div("Estimated Savings", className="opt-card-label"),
            html.Div(f"${sr['estimated_savings']:,.0f}/yr",
                     style={"fontSize": "20px", "fontWeight": "700", "color": "#25ac01", "marginTop": "4px"}),
        ], className="opt-card", style={"flex": "1"}),
        html.Div([
            html.Div("Savings per MW", className="opt-card-label"),
            html.Div(f"${sr['savings_per_mw']:,.0f}",
                     style={"fontSize": "20px", "fontWeight": "700", "color": "#25ac01", "marginTop": "4px"}),
        ], className="opt-card", style={"flex": "1"}),
        html.Div([
            html.Div("Payback Period", className="opt-card-label"),
            html.Div(f"{sr['payback_period_years']:.2f} yr",
                     style={"fontSize": "20px", "fontWeight": "700", "color": "#333", "marginTop": "4px"}),
        ], className="opt-card", style={"flex": "1"}),
    ], style={"display": "flex", "gap": "14px", "marginBottom": "20px", "flexWrap": "wrap"})

    # Factor breakdown table
    factor_labels = {
        "savings_per_mw": "$/MW Savings",
        "water_savings_pct": "Water Savings",
        "impact_on_opex": "OPEX Impact",
        "payback_period": "Payback Period",
        "load_growth": "Load Growth",
    }
    factor_rows = []
    for k, v in fs.items():
        w = custom_scoring[k]["weight"]
        factor_rows.append(html.Tr([
            html.Td(factor_labels.get(k, k), style={"padding": "8px 12px"}),
            html.Td(f"{v:.1f}", style={"padding": "8px 12px", "textAlign": "center"}),
            html.Td(f"{w*100:.0f}%", style={"padding": "8px 12px", "textAlign": "center"}),
            html.Td(f"{v*w:.1f}", style={"padding": "8px 12px", "textAlign": "center", "fontWeight": "600"}),
        ]))
    factor_rows.append(html.Tr([
        html.Td("TOTAL", style={"padding": "8px 12px", "fontWeight": "700", "borderTop": f"2px solid {BRAND}"}),
        html.Td(f"{sr['composite_score']:.1f}", style={"padding": "8px 12px", "textAlign": "center",
                                                         "fontWeight": "700", "borderTop": f"2px solid {BRAND}"}),
        html.Td("100%", style={"padding": "8px 12px", "textAlign": "center",
                                "borderTop": f"2px solid {BRAND}"}),
        html.Td(f"{sr['composite_score']:.1f}", style={"padding": "8px 12px", "textAlign": "center",
                                                         "fontWeight": "700", "color": BRAND,
                                                         "borderTop": f"2px solid {BRAND}"}),
    ]))

    factor_table = html.Div([
        html.H3("Factor Score Breakdown"),
        html.Table([
            html.Thead(html.Tr([
                html.Th("Factor", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "left"}),
                html.Th("Score", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "center"}),
                html.Th("Weight", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "center"}),
                html.Th("Weighted", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "center"}),
            ])),
            html.Tbody(factor_rows),
        ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": "14px"}),
    ], className="opt-section")

    # ─── Radar chart ───
    categories = list(fs.keys())
    cat_labels = [factor_labels.get(c, c) for c in categories]
    values = [fs[c] for c in categories]
    cat_labels_closed = cat_labels + [cat_labels[0]]
    values_closed = values + [values[0]]

    radar_fig = go.Figure(data=go.Scatterpolar(
        r=values_closed,
        theta=cat_labels_closed,
        fill="toself",
        fillcolor="rgba(16, 117, 232, 0.25)",
        line=dict(color=BRAND, width=2),
        marker=dict(size=8, color=BRAND),
        text=[f"{v:.1f}" for v in values_closed],
        hoverinfo="text+theta",
    ))
    radar_fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100], tickvals=[25, 50, 75, 100])),
        title=dict(text=f"Factor Scores at Optimum (Composite: {sr['composite_score']:.1f})",
                   font=dict(size=15)),
        margin=dict(l=60, r=60, t=60, b=40),
        height=420,
        paper_bgcolor="white",
    )

    radar_chart = html.Div([
        html.H3("Factor Score Radar"),
        dcc.Graph(figure=radar_fig, config={"displayModeBar": False}),
    ], className="opt-section")

    # ─── Sweet spot table + bar chart ───
    def _fval(key, val):
        meta = DC_PARAMS.get(key, {})
        mult = meta.get("display_mult", 1)
        fmt = meta.get("fmt", ".2f")
        unit = meta.get("unit", "")
        v = val * mult
        if "%" in fmt:
            return f"{v:.1f}%"
        elif "," in fmt:
            return f"${v:{fmt}}"
        elif unit == "$/kWh":
            return f"${v:{fmt}}/kWh"
        elif unit == "MW":
            return f"{v:.0f} MW"
        else:
            return f"{v:{fmt}} {unit}".strip()

    ss_rows = []
    for key in DC_KEYS:
        meta = DC_PARAMS[key]
        ss = sweet_spots[key]
        ss_rows.append(html.Tr([
            html.Td(meta["label"], style={"padding": "8px 12px"}),
            html.Td(_fval(key, ss["min"]), style={"padding": "8px 12px", "textAlign": "center"}),
            html.Td(_fval(key, ss["optimal"]), style={"padding": "8px 12px", "textAlign": "center", "fontWeight": "600"}),
            html.Td(_fval(key, ss["max"]), style={"padding": "8px 12px", "textAlign": "center"}),
        ]))

    # Sweet spot bar chart
    colors_list = ["#1075E8", "#25ac01", "#FF6B35", "#8B5CF6", "#EC4899"]
    range_fig = go.Figure()
    for i, key in enumerate(DC_KEYS):
        meta = DC_PARAMS[key]
        mult = meta.get("display_mult", 1)
        ss = sweet_spots[key]
        abs_min = meta["min"] * mult
        abs_max = meta["max"] * mult
        span = abs_max - abs_min if abs_max != abs_min else 1

        # Full range background
        range_fig.add_trace(go.Bar(
            y=[meta["label"]], x=[100], orientation="h",
            marker=dict(color="#f0f0f0"), showlegend=False, hoverinfo="skip",
        ))
        # Sweet spot
        ss_start_pct = (ss["min"] * mult - abs_min) / span * 100
        ss_width_pct = (ss["max"] * mult - ss["min"] * mult) / span * 100
        range_fig.add_trace(go.Bar(
            y=[meta["label"]], x=[ss_width_pct], base=[ss_start_pct], orientation="h",
            marker=dict(color=colors_list[i], opacity=0.6), showlegend=False,
            hovertext=f"Sweet spot: {_fval(key, ss['min'])} – {_fval(key, ss['max'])}",
            hoverinfo="text",
        ))
        # Optimal marker
        opt_pct = (ss["optimal"] * mult - abs_min) / span * 100
        range_fig.add_trace(go.Scatter(
            x=[opt_pct], y=[meta["label"]], mode="markers",
            marker=dict(size=14, color=colors_list[i], symbol="diamond",
                       line=dict(width=2, color="white")),
            showlegend=False,
            hovertext=f"Optimal: {_fval(key, ss['optimal'])}",
            hoverinfo="text",
        ))

    range_fig.update_layout(
        barmode="overlay",
        title=dict(text=f"Sweet Spot Ranges (Score ≥ {SWEET_SPOT_THRESHOLD})", font=dict(size=15)),
        xaxis=dict(title="% of Parameter Range", range=[0, 100]),
        yaxis=dict(autorange="reversed"),
        height=320, margin=dict(l=150, r=30, t=50, b=50),
        paper_bgcolor="white", plot_bgcolor="white",
    )

    sweet_section = html.Div([
        html.H3(f"Sweet Spot Ranges (Score ≥ {SWEET_SPOT_THRESHOLD})"),
        html.Table([
            html.Thead(html.Tr([
                html.Th("Parameter", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "left"}),
                html.Th("Min", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "center"}),
                html.Th("Optimal", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "center"}),
                html.Th("Max", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "center"}),
            ])),
            html.Tbody(ss_rows),
        ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": "14px",
                  "marginBottom": "16px"}),
        dcc.Graph(figure=range_fig, config={"displayModeBar": False}),
    ], className="opt-section")

    # ─── Sensitivity chart ───
    sens_fig = make_subplots(
        rows=3, cols=2,
        subplot_titles=[DC_PARAMS[k]["label"] for k in DC_KEYS] + [""],
        vertical_spacing=0.12, horizontal_spacing=0.1,
    )
    for idx, key in enumerate(DC_KEYS):
        row = idx // 2 + 1
        col = idx % 2 + 1
        sd = sensitivity[key]
        meta = DC_PARAMS[key]
        mult = meta.get("display_mult", 1)
        x_vals = [v * mult for v in sd["values"]]
        optimal_x = op[key] * mult

        sens_fig.add_trace(go.Scatter(
            x=x_vals, y=sd["scores"], mode="lines",
            name=meta["label"], line=dict(color=colors_list[idx], width=2.5),
            showlegend=False,
        ), row=row, col=col)

        sens_fig.add_hline(y=75, line_dash="dash", line_color="#999",
                           line_width=1, row=row, col=col)

        # Optimal marker
        opt_score_at_opt = sr["composite_score"]
        for i_s, v in enumerate(sd["values"]):
            if abs(v - op[key]) < (meta["max"] - meta["min"]) / 100:
                opt_score_at_opt = sd["scores"][i_s]
                break

        sens_fig.add_trace(go.Scatter(
            x=[optimal_x], y=[opt_score_at_opt], mode="markers",
            marker=dict(size=12, color=colors_list[idx], symbol="diamond",
                       line=dict(width=2, color="white")),
            showlegend=False,
            hovertext=f"Optimal: {optimal_x:.2f}",
            hoverinfo="text",
        ), row=row, col=col)

        # Sweet spot shading
        ss = sweet_spots[key]
        sens_fig.add_vrect(
            x0=ss["min"] * mult, x1=ss["max"] * mult,
            fillcolor="rgba(37, 172, 1, 0.1)", line_width=0,
            row=row, col=col,
        )

    sens_fig.update_layout(
        height=700,
        title=dict(text="Sensitivity Analysis — Composite Score vs Each Parameter",
                   font=dict(size=15)),
        margin=dict(l=50, r=30, t=70, b=40),
        paper_bgcolor="white", plot_bgcolor="white",
    )
    for i_r in range(1, 4):
        for j_c in range(1, 3):
            sens_fig.update_yaxes(range=[0, 105], row=i_r, col=j_c)

    sens_section = html.Div([
        html.H3("Sensitivity Analysis"),
        html.P("Each chart sweeps one parameter while holding all others at optimal. "
               "Green shading = sweet spot range. Diamond = optimum.",
               style={"fontSize": "13px", "color": "#666", "marginBottom": "10px"}),
        dcc.Graph(figure=sens_fig, config={"displayModeBar": False}),
    ], className="opt-section")

    # ─── Location matching section ───
    location_section = html.Div()
    if locations:
        loc_rows = []
        for loc in locations[:10]:
            loc_rows.append(html.Tr([
                html.Td(loc["location"], style={"padding": "8px 12px"}),
                html.Td(f"${loc['rate']:.4f}/kWh", style={"padding": "8px 12px", "textAlign": "center"}),
                html.Td(loc["utility"], style={"padding": "8px 12px"}),
            ]))
        location_section = html.Div([
            html.H3("Matching US Locations"),
            html.P(f"Data center markets with electricity rates within $0.03/kWh of optimal (${op['electricity_price']:.4f}/kWh):",
                   style={"fontSize": "13px", "color": "#666", "marginBottom": "10px"}),
            html.Table([
                html.Thead(html.Tr([
                    html.Th("Location", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "left"}),
                    html.Th("Rate", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "center"}),
                    html.Th("Utility", style={"padding": "10px 12px", "background": "#f0f4ff", "textAlign": "left"}),
                ])),
                html.Tbody(loc_rows),
            ], style={"width": "100%", "borderCollapse": "collapse", "fontSize": "14px"}),
        ], className="opt-section")

    # ─── Interpretation guide ───
    interp_items = [
        ("DC Size", "Smaller DCs achieve higher per-MW savings because Vigilent's fixed investment "
         "is spread over less capacity. Target smaller-to-mid sized facilities."),
        ("Baseline PUE", "Higher PUE = more total energy consumption = larger absolute savings and "
         "faster payback. Target inefficient facilities."),
        ("Electricity Price", "Higher prices multiply the dollar value of every kWh saved. "
         "Target DCs in expensive electricity markets (CA, NY, NE)."),
        ("Load Growth Rate", "Higher growth rates increase urgency and projected savings. "
         "Target DCs under capacity pressure from AI/cloud expansion."),
        ("Energy % of OPEX", "When energy dominates operating costs, Vigilent's savings have "
         "outsized impact on the bottom line. Target facilities where energy is 40%+ of OPEX."),
    ]
    interp_divs = []
    for title, text in interp_items:
        interp_divs.append(html.Div([
            html.H4(title, style={"color": BRAND, "marginBottom": "4px", "fontSize": "14px"}),
            html.P(text, style={"fontSize": "13px", "margin": "0", "lineHeight": "1.5"}),
        ], style={"background": "#f0f8ff", "borderLeft": f"4px solid {BRAND}",
                  "padding": "12px 16px", "borderRadius": "0 8px 8px 0",
                  "marginBottom": "8px"}))

    interp_section = html.Div([
        html.H3("Interpretation Guide"),
        *interp_divs,
    ], className="opt-section")

    # ─── Assemble results ───
    results_layout = html.Div([
        score_hero,
        vig_display,
        html.H3("Ideal Data Center Profile", style={"marginBottom": "14px", "marginTop": "4px"}),
        profile_cards,
        sweet90_section,
        metrics_cards,
        factor_table,
        radar_chart,
        sweet_section,
        sens_section,
        location_section,
        interp_section,
    ])

    return results_layout, html.Span(
        f"Optimization complete — score: {sr['composite_score']:.1f}/100",
        style={"color": "#25ac01", "fontWeight": "600"})


# --- Weight sum validation ---
@callback(
    Output("opt-weight-status", "children"),
    Input("opt-weight-savings_per_mw", "value"),
    Input("opt-weight-water_savings_pct", "value"),
    Input("opt-weight-impact_on_opex", "value"),
    Input("opt-weight-payback_period", "value"),
    Input("opt-weight-load_growth", "value"),
)
def validate_weights(w1, w2, w3, w4, w5):
    total = sum(v or 0 for v in [w1, w2, w3, w4, w5])
    if abs(total - 1.0) < 0.001:
        return html.Span(f"Weights sum: {total:.2f} ✓",
                         style={"color": "#25ac01", "fontWeight": "600"})
    else:
        return html.Span(f"Weights sum: {total:.2f} (should be 1.00)",
                         style={"color": "#c00", "fontWeight": "600"})


# ═══════════════════════════════════════════════════════════════════════════════
# EJ CALCULATOR CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

@callback(
    Output("ej-results", "children"),
    Output("ej-status", "children"),
    Input("ej-calc-btn", "n_clicks"),
    State("ej-zip", "value"),
    State("ej-dc-size", "value"),
    State("ej-pue", "value"),
    State("ej-growth", "value"),
    State("ej-energy-red", "value"),
    prevent_initial_call=True,
)
def run_ej_calculator(n_clicks, zip_code, dc_size, pue, growth_pct, energy_red_pct):
    if not n_clicks:
        return no_update, no_update

    if not zip_code or not zip_code.strip():
        return html.Div("Please enter a zip code.", style={"color": "#c00"}), ""

    # Convert percentage inputs to fractions
    growth = (growth_pct or 10) / 100
    energy_red = (energy_red_pct or 10) / 100
    dc = dc_size or 20
    p = pue or 1.55

    ej = compute_ej_impact(
        dc_size_mw=dc,
        baseline_pue=p,
        load_growth_rate=growth,
        energy_reduction_pct=energy_red,
        zip_code=zip_code.strip(),
    )

    if ej is None:
        return html.Div(
            f"Could not resolve zip code '{zip_code}'. Please enter a valid US zip code.",
            style={"color": "#c00", "fontSize": "14px"}
        ), ""

    # ═══════════════════════════════════════════════════════
    # SECTION 1: COMMUNITY MARGINALIZATION PROFILE
    # ═══════════════════════════════════════════════════════

    # Helper for marginalization cards
    def _margin_card(title, value_str, avg_str, ratio, ratio_label, accent_color,
                     max_val, current_val, icon):
        # Color: red if worse than average, green if better
        is_worse = ratio > 1.0
        ratio_color = EJ_RED if is_worse else EJ_GREEN
        direction = "above" if is_worse else "below"
        pct_text = f"{abs(ratio - 1) * 100:.0f}% {direction} avg" if abs(ratio - 1) > 0.01 else "at average"

        # Progress bar
        bar_pct = min(current_val / max_val * 100, 100) if max_val > 0 else 0

        return html.Div([
            html.Div(icon, style={"fontSize": "24px", "marginBottom": "4px"}),
            html.Div(title, style={"fontSize": "11px", "color": "#888",
                                    "textTransform": "uppercase", "letterSpacing": "0.5px",
                                    "fontWeight": "600"}),
            html.Div(value_str, style={"fontSize": "28px", "fontWeight": "800",
                                        "color": accent_color, "margin": "4px 0"}),
            html.Div(f"National avg: {avg_str}",
                     style={"fontSize": "12px", "color": "#888", "marginBottom": "6px"}),
            html.Div(pct_text, style={"fontSize": "13px", "fontWeight": "600",
                                       "color": ratio_color, "marginBottom": "8px"}),
            # Progress bar
            html.Div([
                html.Div(style={"width": f"{bar_pct}%", "height": "6px",
                                 "backgroundColor": accent_color, "borderRadius": "3px",
                                 "transition": "width 0.3s"}),
            ], style={"width": "100%", "height": "6px", "backgroundColor": "#e0e0e0",
                       "borderRadius": "3px"}),
        ], style={"background": "white", "borderRadius": "10px", "padding": "18px",
                  "boxShadow": "0 1px 8px rgba(0,0,0,0.07)", "flex": "1",
                  "minWidth": "180px", "textAlign": "center"})

    eb = ej["energy_burden_pct"]
    wb = ej["water_bill_monthly"]
    pov = ej["poverty_rate"]
    di = ej["demographic_index"]

    marginalization_cards = html.Div([
        _margin_card(
            "Energy Burden", f"{eb}%", f"{NATIONAL_AVG_ENERGY_BURDEN}%",
            ej["energy_burden_ratio"], "above avg",
            "#E65100", 8.0, eb, "\u26a1"  # ⚡
        ),
        _margin_card(
            "Water Bill", f"${wb}/mo", f"${NATIONAL_AVG_WATER_BILL:.0f}/mo",
            wb / NATIONAL_AVG_WATER_BILL if NATIONAL_AVG_WATER_BILL > 0 else 1,
            "above avg",
            "#0277BD", 120, wb, "\U0001F4A7"  # 💧
        ),
        _margin_card(
            "Poverty Rate", f"{pov}%", f"{NATIONAL_AVG_POVERTY_RATE}%",
            ej["poverty_ratio"], "above avg",
            "#AD1457", 25.0, pov, "\U0001F3E0"  # 🏠
        ),
        _margin_card(
            "Demographic Index", f"{di}", f"{ej['national_demo_index']}",
            di / ej["national_demo_index"] if ej["national_demo_index"] > 0 else 1,
            "above avg",
            "#6A1B9A", 60.0, di, "\U0001F465"  # 👥
        ),
    ], style={"display": "flex", "gap": "14px", "flexWrap": "wrap", "marginBottom": "20px"})

    # Marginalization comparison chart
    categories = ["Energy Burden (%)", "Poverty Rate (%)", "People of Color (%)",
                   "Demographic Index"]
    community_vals = [eb, pov, ej["people_of_color_pct"], di]
    national_vals = [NATIONAL_AVG_ENERGY_BURDEN, NATIONAL_AVG_POVERTY_RATE,
                     NATIONAL_AVG_POC_PCT, ej["national_demo_index"]]

    margin_fig = go.Figure()
    margin_fig.add_trace(go.Bar(
        name=f"{ej['state_name']} ({ej['state']})",
        x=categories, y=community_vals,
        marker_color=[EJ_RED if c > n else EJ_GREEN for c, n in zip(community_vals, national_vals)],
        text=[f"{v:.1f}" for v in community_vals],
        textposition="outside",
    ))
    margin_fig.add_trace(go.Bar(
        name="National Average",
        x=categories, y=national_vals,
        marker_color="#B0BEC5",
        text=[f"{v:.1f}" for v in national_vals],
        textposition="outside",
    ))
    margin_fig.update_layout(
        barmode="group",
        title=dict(text="Community vs National Average", font=dict(size=15)),
        yaxis=dict(title="Value", range=[0, max(max(community_vals), max(national_vals)) * 1.3]),
        height=360, margin=dict(l=50, r=30, t=50, b=60),
        paper_bgcolor="white", plot_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )

    margin_chart = html.Div([
        dcc.Graph(figure=margin_fig, config={"displayModeBar": False}),
    ], style={"background": "white", "borderRadius": "10px", "padding": "16px",
              "boxShadow": "0 1px 8px rgba(0,0,0,0.07)", "marginBottom": "20px"})

    section1 = html.Div([
        html.Div([
            html.H3(f"\U0001F4CA Community Marginalization Profile — {ej['state_name']}",
                     style={"margin": "0", "color": "#1b285b", "fontSize": "18px"}),
            html.Span(f"Zip code {ej['zip_code']} | eGRID subregion: {ej['egrid_subregion']}",
                       style={"fontSize": "13px", "color": "#888"}),
        ], style={"marginBottom": "16px"}),
        marginalization_cards,
        margin_chart,
    ], style={"marginBottom": "30px"})

    # ═══════════════════════════════════════════════════════
    # SECTION 2: HOW VIGILENT HELPS
    # ═══════════════════════════════════════════════════════

    # Impact hero cards
    def _impact_card(title, value_str, subtitle, accent_color, icon):
        return html.Div([
            html.Div(icon, style={"fontSize": "28px", "marginBottom": "4px"}),
            html.Div(title, style={"fontSize": "11px", "color": "#888",
                                    "textTransform": "uppercase", "letterSpacing": "0.5px",
                                    "fontWeight": "600"}),
            html.Div(value_str, style={"fontSize": "28px", "fontWeight": "800",
                                        "color": accent_color, "margin": "4px 0"}),
            html.Div(subtitle, style={"fontSize": "12px", "color": "#666"}),
        ], style={"background": "white", "borderRadius": "10px", "padding": "18px",
                  "boxShadow": "0 1px 8px rgba(0,0,0,0.07)", "flex": "1",
                  "minWidth": "200px", "textAlign": "center"})

    # Format large numbers nicely
    def _fmt_big(n):
        if n >= 1_000_000:
            return f"{n/1_000_000:,.1f}M"
        elif n >= 1_000:
            return f"{n/1_000:,.1f}K"
        else:
            return f"{n:,.0f}"

    impact_cards = html.Div([
        _impact_card(
            "CO2 Avoided",
            f"{ej['co2_avoided_metric_tons']:,.0f}",
            "metric tons / year",
            EJ_GREEN, "\U0001F30D"  # 🌍
        ),
        _impact_card(
            "Water Saved",
            f"{_fmt_big(ej['water_saved_gallons'])}",
            "gallons / year (upstream grid)",
            "#0277BD", "\U0001F4A7"  # 💧
        ),
        _impact_card(
            "Grid Relief",
            f"{ej['mw_freed']:.1f} MW",
            f"{ej['grid_relief_pct']:.3f}% of {ej['state_name']} peak demand",
            "#E65100", "\u26a1"  # ⚡
        ),
    ], style={"display": "flex", "gap": "14px", "flexWrap": "wrap", "marginBottom": "20px"})

    # EPA equivalencies row
    equiv_items = [
        ("\U0001F697", f"{ej['cars_equivalent']:,.0f}", "cars removed from the road"),
        ("\U0001F3E0", f"{ej['homes_equivalent']:,.0f}", "homes powered for a year"),
        ("\U0001F333", f"{ej['trees_equivalent']:,.0f}", "trees planted for a year"),
        ("\U0001F3CA", f"{ej['pools_equivalent']:,.1f}", "Olympic pools of water saved"),
    ]
    equiv_divs = []
    for icon, val, label in equiv_items:
        equiv_divs.append(html.Div([
            html.Span(icon, style={"fontSize": "24px"}),
            html.Span(f" {val} ", style={"fontSize": "20px", "fontWeight": "700",
                                          "color": "#1b285b"}),
            html.Span(label, style={"fontSize": "13px", "color": "#666"}),
        ], style={"textAlign": "center", "flex": "1", "minWidth": "160px",
                  "padding": "12px"}))

    equiv_section = html.Div(equiv_divs,
        style={"display": "flex", "flexWrap": "wrap", "gap": "10px",
               "background": "white", "borderRadius": "10px", "padding": "14px",
               "boxShadow": "0 1px 8px rgba(0,0,0,0.07)", "marginBottom": "20px"})

    # Narrative block — how Vigilent helps this community
    eb_above_pct = abs(ej["energy_burden_ratio"] - 1) * 100
    eb_direction = "above" if ej["energy_burden_ratio"] > 1 else "below"
    narrative_parts = []
    narrative_parts.append(
        f"Vigilent's cooling optimization at this {dc:.0f} MW data center in "
        f"{ej['state_name']} avoids {ej['co2_avoided_metric_tons']:,.0f} metric tons of CO2 "
        f"annually — equivalent to removing {ej['cars_equivalent']:,.0f} cars from the road."
    )
    if ej["energy_burden_ratio"] > 1.05:
        narrative_parts.append(
            f"This community's energy burden is {eb_above_pct:.0f}% above the national average "
            f"({eb}% vs {NATIONAL_AVG_ENERGY_BURDEN}%). By reducing grid strain by {ej['mw_freed']:.1f} MW "
            f"({ej['grid_relief_pct']:.3f}% of state peak demand), Vigilent helps ease pressure on "
            f"local energy infrastructure, contributing to lower energy costs for the "
            f"households in this area that face disproportionate energy burden."
        )
    else:
        narrative_parts.append(
            f"This community's energy burden ({eb}%) is near or below the national average "
            f"({NATIONAL_AVG_ENERGY_BURDEN}%). Vigilent's {ej['mw_freed']:.1f} MW of grid strain relief "
            f"helps maintain affordable energy access for local households."
        )

    if ej["water_saved_gallons"] > 1_000_000:
        narrative_parts.append(
            f"The {_fmt_big(ej['water_saved_gallons'])} gallons of upstream water savings "
            f"reduce the water footprint of the regional power grid, helping address water stress "
            f"in a state where the average water bill is ${wb}/month."
        )

    narrative_block = html.Div([
        html.H4("How This Helps the Community",
                 style={"color": EJ_TEAL, "marginBottom": "8px", "fontSize": "15px"}),
        *[html.P(p, style={"fontSize": "14px", "lineHeight": "1.7", "color": "#333",
                            "margin": "0 0 10px 0"})
          for p in narrative_parts],
    ], style={"background": "#E0F7FA", "borderLeft": f"4px solid {EJ_TEAL}",
              "padding": "18px 22px", "borderRadius": "0 10px 10px 0",
              "marginBottom": "20px"})

    # Charts row: Fuel mix donut + CO2 comparison
    fuel_mix = ej["fuel_mix"]
    fuel_labels = list(fuel_mix.keys())
    fuel_values = [fuel_mix[f] * 100 for f in fuel_labels]
    fuel_colors = {
        "coal": "#424242", "gas": "#FF8F00", "nuclear": "#7B1FA2",
        "hydro": "#0288D1", "wind": "#4CAF50", "solar": "#FDD835", "other": "#90A4AE",
    }

    fuel_fig = go.Figure(data=[go.Pie(
        labels=[f.title() for f in fuel_labels],
        values=fuel_values,
        hole=0.45,
        marker=dict(colors=[fuel_colors.get(f, "#ccc") for f in fuel_labels]),
        textinfo="label+percent",
        textfont=dict(size=11),
        hoverinfo="label+percent",
    )])
    fuel_fig.update_layout(
        title=dict(text=f"Grid Fuel Mix — {ej['egrid_subregion']}", font=dict(size=14)),
        height=320, margin=dict(l=20, r=20, t=50, b=20),
        paper_bgcolor="white",
        showlegend=False,
    )

    # CO2 comparison bar
    # Find cleanest and dirtiest eGRID subregions
    sorted_regions = sorted(EGRID_CO2_LBS_PER_MWH.items(), key=lambda x: x[1])
    cleanest = sorted_regions[0]
    dirtiest = sorted_regions[-1]
    national_avg_co2 = sum(EGRID_CO2_LBS_PER_MWH.values()) / len(EGRID_CO2_LBS_PER_MWH)

    co2_cats = [f"{ej['egrid_subregion']}\n(This Region)", "US Average",
                f"{cleanest[0]}\n(Cleanest)", f"{dirtiest[0]}\n(Dirtiest)"]
    co2_vals = [ej["co2_rate_lbs_per_mwh"], national_avg_co2, cleanest[1], dirtiest[1]]
    co2_colors = [EJ_TEAL, "#78909C", EJ_GREEN, EJ_RED]

    co2_fig = go.Figure(data=[go.Bar(
        x=co2_cats, y=co2_vals,
        marker_color=co2_colors,
        text=[f"{v:.0f}" for v in co2_vals],
        textposition="outside",
    )])
    co2_fig.update_layout(
        title=dict(text="CO2 Emission Rate Comparison (lbs/MWh)", font=dict(size=14)),
        yaxis=dict(title="lbs CO2 / MWh", range=[0, max(co2_vals) * 1.2]),
        height=320, margin=dict(l=50, r=30, t=50, b=80),
        paper_bgcolor="white", plot_bgcolor="white",
    )

    charts_row = html.Div([
        html.Div([
            dcc.Graph(figure=fuel_fig, config={"displayModeBar": False}),
        ], style={"flex": "1", "minWidth": "300px", "background": "white",
                  "borderRadius": "10px", "boxShadow": "0 1px 8px rgba(0,0,0,0.07)"}),
        html.Div([
            dcc.Graph(figure=co2_fig, config={"displayModeBar": False}),
        ], style={"flex": "1", "minWidth": "300px", "background": "white",
                  "borderRadius": "10px", "boxShadow": "0 1px 8px rgba(0,0,0,0.07)"}),
    ], style={"display": "flex", "gap": "14px", "flexWrap": "wrap", "marginBottom": "20px"})

    section2 = html.Div([
        html.Div([
            html.H3("\U0001F331 How Vigilent Helps — De-Marginalization Impact",
                     style={"margin": "0", "color": "#1b285b", "fontSize": "18px"}),
            html.Span("Quantified environmental and community benefits",
                       style={"fontSize": "13px", "color": "#888"}),
        ], style={"marginBottom": "16px"}),
        impact_cards,
        equiv_section,
        narrative_block,
        charts_row,
    ], style={"marginBottom": "30px"})

    # ═══════════════════════════════════════════════════════
    # SECTION 3: METHODOLOGY & SOURCES
    # ═══════════════════════════════════════════════════════

    sources = [
        ("Energy Burden", "DOE LEAD Tool, EIA RECS 2020, ACEEE", "State-level % of household income spent on energy"),
        ("Water Affordability", "DataPandas.org, EPA Water Affordability Assessment 2024", "State-level avg monthly water bill"),
        ("Demographics", "US Census ACS 2023, EPA EJScreen v2.3", "Poverty rate, People of Color %, Demographic Index"),
        ("Emissions", "EPA eGRID2022", "CO2 lbs/MWh by eGRID subregion"),
        ("Water Intensity", "NREL, USGS, Macknick et al. (2012)", "Gallons/MWh by fuel type (upstream grid cooling)"),
        ("Grid Capacity", "EIA", "State peak demand (MW)"),
        ("EPA Equivalencies", "EPA Greenhouse Gas Equivalencies Calculator", "Cars, homes, trees conversions"),
    ]
    source_rows = [
        html.Tr([
            html.Td(s[0], style={"padding": "6px 12px", "fontWeight": "600", "fontSize": "13px"}),
            html.Td(s[1], style={"padding": "6px 12px", "fontSize": "13px"}),
            html.Td(s[2], style={"padding": "6px 12px", "fontSize": "12px", "color": "#666"}),
        ]) for s in sources
    ]

    methodology_section = html.Div([
        html.Details([
            html.Summary("Methodology & Data Sources",
                         style={"fontWeight": "600", "fontSize": "15px", "color": "#1b285b",
                                "cursor": "pointer", "marginBottom": "12px"}),
            html.Div([
                html.P([
                    html.B("CO2 Avoided"), " = Energy Saved (MWh) x Regional CO2 Rate (lbs/MWh)",
                ], style={"fontSize": "13px", "fontFamily": "monospace", "background": "#f5f6fa",
                          "padding": "8px 12px", "borderRadius": "4px", "marginBottom": "6px"}),
                html.P([
                    html.B("Water Saved"), " = Energy Saved (MWh) x Weighted Water Intensity from Regional Fuel Mix",
                ], style={"fontSize": "13px", "fontFamily": "monospace", "background": "#f5f6fa",
                          "padding": "8px 12px", "borderRadius": "4px", "marginBottom": "6px"}),
                html.P([
                    html.B("Grid Relief"), " = DC Size x PUE x Energy Reduction %",
                ], style={"fontSize": "13px", "fontFamily": "monospace", "background": "#f5f6fa",
                          "padding": "8px 12px", "borderRadius": "4px", "marginBottom": "6px"}),
                html.P([
                    html.B("Demographic Index"), " = (% Low Income approx + % People of Color) / 2 (EPA EJScreen v2.3)",
                ], style={"fontSize": "13px", "fontFamily": "monospace", "background": "#f5f6fa",
                          "padding": "8px 12px", "borderRadius": "4px", "marginBottom": "14px"}),
                html.Table([
                    html.Thead(html.Tr([
                        html.Th("Category", style={"padding": "8px 12px", "background": "#E0F2F1",
                                                    "textAlign": "left", "fontSize": "12px"}),
                        html.Th("Source", style={"padding": "8px 12px", "background": "#E0F2F1",
                                                  "textAlign": "left", "fontSize": "12px"}),
                        html.Th("Notes", style={"padding": "8px 12px", "background": "#E0F2F1",
                                                  "textAlign": "left", "fontSize": "12px"}),
                    ])),
                    html.Tbody(source_rows),
                ], style={"width": "100%", "borderCollapse": "collapse"}),
            ]),
        ]),
    ], style={"background": "white", "borderRadius": "10px", "padding": "18px 22px",
              "boxShadow": "0 1px 8px rgba(0,0,0,0.07)", "marginBottom": "20px"})

    # ═══════════════════════════════════════════════════════
    # ASSEMBLE RESULTS
    # ═══════════════════════════════════════════════════════

    results_layout = html.Div([
        section1,
        section2,
        methodology_section,
    ])

    status = html.Span(
        f"Analysis complete — {ej['state_name']} ({ej['egrid_subregion']})",
        style={"color": EJ_TEAL, "fontWeight": "600"})

    return results_layout, status


# ═══════════════════════════════════════════════════════════════════════════════
# DC FINDER CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════════

_FINDER_PARAM_LABELS = {
    "dc_size_mw": "DC Size (MW)",
    "baseline_pue": "Baseline PUE",
    "electricity_price": "Electricity Price ($/kWh)",
    "load_growth_rate": "Load Growth Rate (%)",
    "energy_pct_opex": "Energy % of OPEX",
}

_FINDER_PARAM_FMT = {
    "dc_size_mw": (".0f", "MW", 1),
    "baseline_pue": (".2f", "", 1),
    "electricity_price": (".2f", "$/kWh", 1),
    "load_growth_rate": (".0f", "%", 100),
    "energy_pct_opex": (".0f", "%", 100),
}


@callback(
    Output("finder-results", "children"),
    Output("finder-status", "children"),
    Output("finder-composite-store", "data"),
    Output("finder-grids-store", "data"),
    Output("finder-threshold-store", "data"),
    Input("finder-run-btn", "n_clicks"),
    State("finder-vig-num_years", "value"),
    State("finder-vig-investment_cost", "value"),
    State("finder-vig-energy_reduction_pct", "value"),
    State("finder-vig-water_reduction_pct", "value"),
    State("finder-threshold", "value"),
    prevent_initial_call=True,
)
def run_finder(n_clicks, num_years, investment, energy_red, water_red, threshold):
    if not n_clicks:
        return no_update, no_update, no_update, no_update, no_update

    # Convert display values to raw
    e_red = (energy_red or 10) / 100
    w_red = (water_red or 5) / 100
    inv = investment or 1_500_000
    threshold = threshold or 75

    vigilent_params = {
        "investment_cost": inv,
        "energy_reduction_pct": e_red,
        "water_reduction_pct": w_red,
    }

    # Run exhaustive sweep
    composite, grids = compute_exhaustive_sweep(vigilent_params, steps=15)
    ranges, feasibility, total_passing, total_combos = extract_target_ranges(
        composite, grids, threshold)

    # --- Build results layout ---

    # 1. Summary card
    zone_color = "#25ac01" if threshold >= 90 else (BRAND if threshold >= 75 else "#88b4f2")
    zone_label = "Excellent" if threshold >= 90 else ("Good" if threshold >= 75 else "Moderate")

    summary_card = html.Div([
        html.Div([
            html.Span(f"{feasibility:.1f}%",
                      style={"fontSize": "48px", "fontWeight": "700",
                             "color": zone_color, "lineHeight": "1"}),
            html.Span(f" of all DC profiles score {chr(8805)} {threshold}",
                      style={"fontSize": "18px", "color": "#444", "marginLeft": "12px"}),
        ]),
        html.P(f"{total_passing:,} of {total_combos:,} parameter combinations tested",
               style={"fontSize": "13px", "color": "#888", "marginTop": "4px"}),
    ], style={"padding": "24px", "background": "white", "borderRadius": "12px",
              "boxShadow": "0 2px 12px rgba(0,0,0,0.06)", "marginBottom": "20px"})

    # 2. Ranges table
    table_rows = []
    for param_key in ["dc_size_mw", "baseline_pue", "electricity_price",
                       "load_growth_rate", "energy_pct_opex"]:
        r = ranges[param_key]
        fmt, unit, mult = _FINDER_PARAM_FMT[param_key]
        label = _FINDER_PARAM_LABELS[param_key]
        pct = r["pct_of_range"]

        if r["min"] is not None:
            mn_str = format(r["min"] * mult, fmt)
            mx_str = format(r["max"] * mult, fmt)
            if unit:
                mn_str += f" {unit}"
                mx_str += f" {unit}"
        else:
            mn_str = mx_str = "—"

        # Flexibility label
        if pct >= 95:
            flex_label = "Any"
            flex_color = "#25ac01"
        elif pct >= 60:
            flex_label = f"{pct:.0f}%"
            flex_color = "#25ac01"
        elif pct >= 30:
            flex_label = f"{pct:.0f}%"
            flex_color = "#FF8C00"
        elif pct > 0:
            flex_label = f"{pct:.0f}%"
            flex_color = "#c00"
        else:
            flex_label = "None"
            flex_color = "#c00"

        # Flexibility bar
        bar = html.Div([
            html.Div(style={"width": f"{min(pct, 100):.0f}%", "height": "100%",
                            "backgroundColor": flex_color, "borderRadius": "4px",
                            "transition": "width 0.3s"}),
        ], style={"width": "80px", "height": "8px", "backgroundColor": "#e8e8e8",
                  "borderRadius": "4px", "display": "inline-block",
                  "verticalAlign": "middle", "marginRight": "8px"})

        table_rows.append(html.Tr([
            html.Td(label, style={"padding": "10px 14px", "fontSize": "14px",
                                   "fontWeight": "500", "whiteSpace": "nowrap"}),
            html.Td(mn_str, style={"padding": "10px 14px", "fontSize": "14px",
                                    "textAlign": "center", "fontFamily": "monospace"}),
            html.Td(mx_str, style={"padding": "10px 14px", "fontSize": "14px",
                                    "textAlign": "center", "fontFamily": "monospace"}),
            html.Td([bar, html.Span(flex_label, style={"fontSize": "13px",
                                                         "fontWeight": "600",
                                                         "color": flex_color})],
                    style={"padding": "10px 14px"}),
        ]))

    ranges_table = html.Div([
        html.H3("Target DC Ranges",
                 style={"marginTop": "0", "marginBottom": "12px", "color": "#1b285b"}),
        html.P("Each range shows parameter values where at least one combination of the "
               "other parameters produces a passing score. Wider = more flexibility.",
               style={"fontSize": "13px", "color": "#888", "marginBottom": "14px"}),
        html.Table([
            html.Thead(html.Tr([
                html.Th("Parameter", style={"padding": "10px 14px", "background": "#f0f4ff",
                                             "textAlign": "left", "fontSize": "12px",
                                             "fontWeight": "600"}),
                html.Th("Min", style={"padding": "10px 14px", "background": "#f0f4ff",
                                       "textAlign": "center", "fontSize": "12px",
                                       "fontWeight": "600"}),
                html.Th("Max", style={"padding": "10px 14px", "background": "#f0f4ff",
                                       "textAlign": "center", "fontSize": "12px",
                                       "fontWeight": "600"}),
                html.Th("Flexibility", style={"padding": "10px 14px", "background": "#f0f4ff",
                                               "textAlign": "left", "fontSize": "12px",
                                               "fontWeight": "600"}),
            ])),
            html.Tbody(table_rows),
        ], style={"width": "100%", "borderCollapse": "collapse"}),
    ], style={"padding": "24px", "background": "white", "borderRadius": "12px",
              "boxShadow": "0 2px 12px rgba(0,0,0,0.06)", "marginBottom": "20px"})

    # 3. Tradeoff explorer
    tradeoff_section = html.Div([
        html.H3("Tradeoff Explorer",
                 style={"marginTop": "0", "marginBottom": "8px", "color": "#1b285b"}),
        html.P("Select two DC parameters to see how they trade off. The heatmap shows what "
               "percentage of all other parameter combinations produce a passing score for "
               "each pair of values.",
               style={"fontSize": "13px", "color": "#888", "marginBottom": "14px"}),
        html.Div([
            html.Div([
                html.Label("X Axis", style={"fontWeight": "600", "fontSize": "13px",
                                             "marginBottom": "4px", "display": "block"}),
                dcc.Dropdown(id="finder-tradeoff-x",
                             options=_FINDER_PAIR_OPTIONS,
                             value="baseline_pue",
                             clearable=False,
                             style={"fontSize": "14px"}),
            ], style={"flex": "1", "minWidth": "180px"}),
            html.Div([
                html.Label("Y Axis", style={"fontWeight": "600", "fontSize": "13px",
                                             "marginBottom": "4px", "display": "block"}),
                dcc.Dropdown(id="finder-tradeoff-y",
                             options=_FINDER_PAIR_OPTIONS,
                             value="electricity_price",
                             clearable=False,
                             style={"fontSize": "14px"}),
            ], style={"flex": "1", "minWidth": "180px"}),
        ], style={"display": "flex", "gap": "14px", "marginBottom": "16px",
                  "maxWidth": "500px"}),
        dcc.Graph(id="finder-tradeoff-heatmap",
                  style={"height": "500px"}),
    ], style={"padding": "24px", "background": "white", "borderRadius": "12px",
              "boxShadow": "0 2px 12px rgba(0,0,0,0.06)"})

    results_layout = html.Div([summary_card, ranges_table, tradeoff_section])

    status = html.Span(
        f"Sweep complete — {total_combos:,} combinations analyzed",
        style={"color": FINDER_TEAL, "fontWeight": "600"})

    # Serialize for stores (convert numpy to lists)
    composite_list = composite.tolist()
    grids_ser = {k: v.tolist() for k, v in grids.items()}

    return results_layout, status, composite_list, grids_ser, threshold


# --- DC Finder: Tradeoff heatmap ---
@callback(
    Output("finder-tradeoff-heatmap", "figure"),
    Input("finder-tradeoff-x", "value"),
    Input("finder-tradeoff-y", "value"),
    State("finder-composite-store", "data"),
    State("finder-grids-store", "data"),
    State("finder-threshold-store", "data"),
    prevent_initial_call=True,
)
def update_finder_tradeoff(x_param, y_param, composite_list, grids_ser, threshold):
    if composite_list is None or x_param == y_param:
        fig = go.Figure()
        if x_param == y_param:
            fig.add_annotation(text="Select two different parameters",
                             xref="paper", yref="paper", x=0.5, y=0.5,
                             showarrow=False, font=dict(size=16, color="#999"))
        fig.update_layout(
            xaxis=dict(visible=False), yaxis=dict(visible=False),
            plot_bgcolor="white", paper_bgcolor="white", height=450)
        return fig

    composite = np.array(composite_list)
    grids = {k: np.array(v) for k, v in grids_ser.items()}

    tradeoff = compute_pairwise_tradeoff(composite, x_param, y_param, threshold)

    x_vals = grids[x_param]
    y_vals = grids[y_param]

    # Format tick labels
    x_fmt, x_unit, x_mult = _FINDER_PARAM_FMT[x_param]
    y_fmt, y_unit, y_mult = _FINDER_PARAM_FMT[y_param]
    x_labels = [format(v * x_mult, x_fmt) for v in x_vals]
    y_labels = [format(v * y_mult, y_fmt) for v in y_vals]

    # Build hover text
    hover_text = []
    for i, yv in enumerate(y_vals):
        row = []
        for j, xv in enumerate(x_vals):
            pct = tradeoff[i, j] if tradeoff.shape == (len(y_vals), len(x_vals)) \
                  else tradeoff[j, i]
            row.append(
                f"{_FINDER_PARAM_LABELS[x_param]}: {format(xv * x_mult, x_fmt)}"
                f"{'  ' + x_unit if x_unit else ''}<br>"
                f"{_FINDER_PARAM_LABELS[y_param]}: {format(yv * y_mult, y_fmt)}"
                f"{'  ' + y_unit if y_unit else ''}<br>"
                f"<b>{pct:.0f}% of other combos pass</b>")
            row[-1] = row[-1].replace("  ", " ")
        hover_text.append(row)

    # Ensure tradeoff is oriented (y, x)
    z_data = tradeoff.T if tradeoff.shape[0] == len(x_vals) else tradeoff

    fig = go.Figure(data=go.Heatmap(
        z=z_data,
        x=x_labels,
        y=y_labels,
        text=hover_text,
        hoverinfo="text",
        colorscale=[
            [0.0, "#ffffff"],
            [0.3, "#d7e7f8"],
            [0.6, "#88b4f2"],
            [0.8, "#1075E8"],
            [1.0, "#25ac01"],
        ],
        zmin=0, zmax=100,
        colorbar=dict(
            title=dict(text="% Passing", font=dict(size=12)),
            ticksuffix="%",
            len=0.8,
        ),
    ))

    fig.update_layout(
        title=dict(
            text=f"Tradeoff: {_FINDER_PARAM_LABELS[x_param]} vs "
                 f"{_FINDER_PARAM_LABELS[y_param]}",
            font=dict(size=15)),
        xaxis=dict(title=_FINDER_PARAM_LABELS[x_param], tickfont=dict(size=11)),
        yaxis=dict(title=_FINDER_PARAM_LABELS[y_param], tickfont=dict(size=11)),
        plot_bgcolor="white",
        paper_bgcolor="white",
        height=450,
        margin=dict(l=80, r=40, t=50, b=60),
    )

    return fig


# ═══════════════════════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  VIGILENT SWEET SPOT SIMULATOR + OPTIMIZER")
    print("  Open http://127.0.0.1:8050 in your browser")
    print("=" * 60 + "\n")
    app.run(debug=False, port=8050)
