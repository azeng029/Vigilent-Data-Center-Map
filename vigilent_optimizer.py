#!/usr/bin/env python3
"""
Vigilent Data Center Optimization Model
========================================
Converts the Vigilent Calculator spreadsheet into a full Python optimization
model with interactive inputs, savings analysis, composite scoring, heatmaps,
and bubble charts.

Usage:
    python3 vigilent_optimizer.py                # interactive mode
    python3 vigilent_optimizer.py --defaults     # run with default values
    python3 vigilent_optimizer.py --demo         # run demo with multiple scenarios
"""

import argparse
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")  # non-interactive backend so it works headless too
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.gridspec import GridSpec
from dataclasses import dataclass, field
from typing import Optional
import os

# ─── Output directory ────────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  DATA MODEL
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class DataCenterInputs:
    """Target data center parameters."""
    dc_size_mw: float = 20.0                  # Data Center Size (MW)
    baseline_pue: float = 1.4                  # Baseline PUE (ratio)
    electricity_price: float = 0.10            # $/kWh
    load_growth_rate: float = 0.08             # Annual IT Load Growth Rate (%)
    annual_water_usage_l: float = 315_000_000  # Annual Site Water Usage (L)
    water_cost_per_l: float = 0.003            # $/L
    energy_pct_opex: float = 0.40              # Energy % of OPEX

    @property
    def total_electricity_mw(self) -> float:
        return self.dc_size_mw * self.baseline_pue


@dataclass
class VigilentParams:
    """Vigilent deployment parameters."""
    num_years: int = 1
    investment_cost: float = 1_500_000         # $
    energy_reduction_pct: float = 0.07         # 7%
    water_reduction_pct: float = 0.04          # 4%


@dataclass
class CalculatorResults:
    """All computed outputs."""
    annual_energy_kwh: float = 0.0
    annual_energy_cost: float = 0.0
    projected_cost_growth: float = 0.0
    projected_cost_growth_pct: float = 0.0
    estimated_savings: float = 0.0
    estimated_savings_pct: float = 0.0
    impact_on_opex_pct: float = 0.0
    water_savings_l: float = 0.0
    water_savings_cost: float = 0.0
    # Scoring factors
    savings_per_mw: float = 0.0
    water_savings_pct: float = 0.0
    payback_period_years: float = 0.0
    composite_score: float = 0.0
    factor_scores: dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  CORE CALCULATOR  (replicates every spreadsheet formula)
# ═══════════════════════════════════════════════════════════════════════════════

# Scoring thresholds & weights (from the spreadsheet)
SCORING_CONFIG = {
    "savings_per_mw":    {"min": 0, "max": 300_000, "weight": 0.35},
    "water_savings_pct": {"min": 0, "max": 0.08,    "weight": 0.10},
    "impact_on_opex":    {"min": 0, "max": 0.10,    "weight": 0.20},
    "payback_period":    {"min": 0, "max": 5.0,     "weight": 0.25},
    "load_growth":       {"min": 0, "max": 0.15,    "weight": 0.10},
}


def compute(dc: DataCenterInputs, vp: VigilentParams) -> CalculatorResults:
    """Run the full Vigilent savings & scoring model."""
    r = CalculatorResults()

    # --- Energy ---
    total_mw = dc.total_electricity_mw
    r.annual_energy_kwh = total_mw * 1_080 * 8_760 / 1_000  # MW → kWh
    # Simpler formula matching spreadsheet: MW * hours * 1000 (kW/MW) * fraction
    # Spreadsheet: 28 MW * 365.25 * 24 * 1000 ≈ 245,448,000 but shows 264,902,400
    # Reverse-engineering: 264902400 / (28 * 8760) = 1080 → they use 1080 factor
    # Actually: 28 * 1000 * 8766 * (some rounding) or  28 * 1000 * 9460.8
    # Let me just replicate: total_mw * 1000 * hours_per_year
    hours_per_year = 8766  # 365.25 * 24
    r.annual_energy_kwh = total_mw * 1000 * hours_per_year
    # Spreadsheet says 264,902,400 for 28 MW → 264902400/28000 = 9460.8 hrs?
    # 28 * 1000 * 8760 = 245,280,000 (not matching)
    # 20 * 1.4 * 1000 * 8760 * (some adj)
    # Let me check: 264902400 / (20 * 1.4 * 1000) = 9460.8
    # 9460.8 / 24 = 394.2 days → using 394.2 days? Likely 8760 * 1.08 load growth
    # Yes! 8760 * 1.08 = 9460.8 → they factor in load growth to annual consumption
    r.annual_energy_kwh = total_mw * 1000 * 8760 * (1 + dc.load_growth_rate)

    r.annual_energy_cost = r.annual_energy_kwh * dc.electricity_price

    # Cost before Vigilent (at base load)
    base_energy_cost = total_mw * 1000 * 8760 * dc.electricity_price
    r.projected_cost_growth = r.annual_energy_cost - base_energy_cost
    r.projected_cost_growth_pct = dc.load_growth_rate / (1 + dc.load_growth_rate) if dc.load_growth_rate else 0
    # Spreadsheet: growth_pct ≈ load_growth / (1 + baseline_pue ... )
    # Actually for 8%: 1962240 / 26490240 = 0.07407 ≈ 8/108 = 0.07407 ✓
    r.projected_cost_growth_pct = r.projected_cost_growth / r.annual_energy_cost if r.annual_energy_cost else 0

    # --- Vigilent Savings ---
    r.estimated_savings = r.annual_energy_cost * vp.energy_reduction_pct
    r.estimated_savings_pct = vp.energy_reduction_pct

    # OPEX impact
    total_opex = r.annual_energy_cost / dc.energy_pct_opex if dc.energy_pct_opex else 0
    r.impact_on_opex_pct = r.estimated_savings / total_opex if total_opex else 0

    # --- Water ---
    r.water_savings_pct = vp.water_reduction_pct
    r.water_savings_l = dc.annual_water_usage_l * vp.water_reduction_pct
    r.water_savings_cost = r.water_savings_l * dc.water_cost_per_l

    # --- Scoring ---
    r.savings_per_mw = r.estimated_savings / dc.dc_size_mw if dc.dc_size_mw else 0
    r.payback_period_years = (vp.investment_cost / r.estimated_savings
                              if r.estimated_savings > 0 else float("inf"))

    # Composite score: each factor is normalized to 0–100, then weighted
    def score(value, cfg_key):
        cfg = SCORING_CONFIG[cfg_key]
        raw = (value / cfg["max"]) * 100 if cfg["max"] else 0
        return min(raw, 100)  # cap at 100

    def score_inverted(value, cfg_key):
        """For payback: shorter = better, so invert the scale."""
        cfg = SCORING_CONFIG[cfg_key]
        raw = (1 - value / cfg["max"]) * 100 if cfg["max"] else 0
        return max(min(raw, 100), 0)  # clamp to [0, 100]

    factor_scores = {
        "savings_per_mw":    score(r.savings_per_mw, "savings_per_mw"),
        "water_savings_pct": score(r.water_savings_pct, "water_savings_pct"),
        "impact_on_opex":    score(r.impact_on_opex_pct, "impact_on_opex"),
        "payback_period":    score_inverted(r.payback_period_years, "payback_period"),
        "load_growth":       score(dc.load_growth_rate, "load_growth"),
    }

    r.factor_scores = factor_scores
    r.composite_score = sum(
        factor_scores[k] * SCORING_CONFIG[k]["weight"]
        for k in factor_scores
    )

    return r


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  PRETTY-PRINT RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

def print_results(dc: DataCenterInputs, vp: VigilentParams, r: CalculatorResults):
    sep = "─" * 64
    print(f"\n{'═' * 64}")
    print("  VIGILENT DATA CENTER OPTIMIZATION — RESULTS")
    print(f"{'═' * 64}\n")

    print("  DATA CENTER PROFILE")
    print(sep)
    print(f"  DC Size:              {dc.dc_size_mw:>10.1f} MW")
    print(f"  Baseline PUE:         {dc.baseline_pue:>10.2f}")
    print(f"  Total Elec. Usage:    {dc.total_electricity_mw:>10.1f} MW")
    print(f"  Electricity Price:    {dc.electricity_price:>10.4f} $/kWh")
    print(f"  Load Growth Rate:     {dc.load_growth_rate * 100:>10.1f} %")
    print(f"  Water Usage:          {dc.annual_water_usage_l:>14,.0f} L/yr")
    print(f"  Water Cost:           {dc.water_cost_per_l:>10.4f} $/L")
    print(f"  Energy % of OPEX:     {dc.energy_pct_opex * 100:>10.1f} %")

    print(f"\n  VIGILENT DEPLOYMENT")
    print(sep)
    print(f"  Investment:           ${vp.investment_cost:>13,.0f}")
    print(f"  Energy Reduction:     {vp.energy_reduction_pct * 100:>10.1f} %")
    print(f"  Water Reduction:      {vp.water_reduction_pct * 100:>10.1f} %")
    print(f"  Duration:             {vp.num_years:>10d} yr(s)")

    print(f"\n  FINANCIAL OUTPUTS")
    print(sep)
    print(f"  Annual Energy (kWh):  {r.annual_energy_kwh:>18,.0f}")
    print(f"  Annual Energy Cost:   ${r.annual_energy_cost:>17,.0f}")
    print(f"  Cost Growth (load):   ${r.projected_cost_growth:>17,.0f}  "
          f"({r.projected_cost_growth_pct * 100:.2f}%)")
    print(f"  Vigilent Savings:     ${r.estimated_savings:>17,.0f}  "
          f"({r.estimated_savings_pct * 100:.1f}%)")
    print(f"  Impact on OPEX:       {r.impact_on_opex_pct * 100:>17.2f} %")
    print(f"  Water Savings:        {r.water_savings_l:>18,.0f} L")
    print(f"  Water Cost Savings:   ${r.water_savings_cost:>17,.0f}")

    print(f"\n  COMPOSITE SCORING")
    print(sep)
    labels = {
        "savings_per_mw": "$ Savings / MW",
        "water_savings_pct": "Water Savings %",
        "impact_on_opex": "OPEX Impact %",
        "payback_period": "Payback Period",
        "load_growth": "Load Growth",
    }
    for k, v in r.factor_scores.items():
        w = SCORING_CONFIG[k]["weight"]
        print(f"  {labels[k]:<22} score={v:>7.2f}  weight={w:.2f}  "
              f"→ {v * w:>7.2f}")
    print(sep)
    print(f"  TOTAL COMPOSITE SCORE:  {r.composite_score:.2f} / 100")
    print(f"  Payback Period:         {r.payback_period_years:.2f} years")
    print(f"{'═' * 64}\n")


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  VISUALIZATION — DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

def plot_dashboard(dc: DataCenterInputs, vp: VigilentParams, r: CalculatorResults):
    """Single-page dashboard: bar chart of scores + key metrics."""
    fig = plt.figure(figsize=(16, 9), facecolor="#0d1117")
    gs = GridSpec(2, 3, figure=fig, hspace=0.35, wspace=0.35)

    text_color = "#c9d1d9"
    accent = "#58a6ff"
    green = "#3fb950"
    orange = "#d29922"

    fig.suptitle("Vigilent Data Center Optimization Dashboard",
                 fontsize=18, fontweight="bold", color=text_color, y=0.97)

    # ── 4a. Composite score breakdown (bar) ──
    ax1 = fig.add_subplot(gs[0, 0])
    ax1.set_facecolor("#161b22")
    labels = ["$/MW", "Water%", "OPEX%", "Payback", "Load\nGrowth"]
    keys = list(r.factor_scores.keys())
    raw_scores = [r.factor_scores[k] for k in keys]
    weighted = [r.factor_scores[k] * SCORING_CONFIG[k]["weight"] for k in keys]
    x = np.arange(len(labels))
    bars = ax1.bar(x, weighted, color=accent, alpha=0.85, edgecolor="#30363d")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, color=text_color, fontsize=8)
    ax1.set_ylabel("Weighted Score", color=text_color, fontsize=9)
    ax1.set_title(f"Composite Score: {r.composite_score:.1f}/100",
                  color=green, fontsize=12, fontweight="bold")
    ax1.tick_params(colors=text_color)
    for spine in ax1.spines.values():
        spine.set_color("#30363d")
    for bar, val in zip(bars, weighted):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                 f"{val:.1f}", ha="center", va="bottom", color=text_color, fontsize=8)

    # ── 4b. Savings waterfall ──
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.set_facecolor("#161b22")
    cats = ["Base\nCost", "Load\nGrowth", "Vigilent\nSavings", "Net\nCost"]
    base = r.annual_energy_cost - r.projected_cost_growth
    vals = [base, r.projected_cost_growth, -r.estimated_savings,
            r.annual_energy_cost - r.estimated_savings]
    bottoms = [0, base, base + r.projected_cost_growth, 0]
    colors_w = ["#8b949e", orange, green, accent]
    bars2 = ax2.bar(cats, vals, bottom=bottoms, color=colors_w, alpha=0.85,
                    edgecolor="#30363d")
    ax2.set_ylabel("$ / year", color=text_color, fontsize=9)
    ax2.set_title("Cost Waterfall", color=text_color, fontsize=12, fontweight="bold")
    ax2.tick_params(colors=text_color)
    for spine in ax2.spines.values():
        spine.set_color("#30363d")
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x / 1e6:.1f}M"))

    # ── 4c. Key metrics text ──
    ax3 = fig.add_subplot(gs[0, 2])
    ax3.set_facecolor("#161b22")
    ax3.axis("off")
    metrics_text = (
        f"DC Size: {dc.dc_size_mw:.0f} MW\n"
        f"PUE: {dc.baseline_pue:.2f}\n"
        f"Elec Price: ${dc.electricity_price:.3f}/kWh\n"
        f"Load Growth: {dc.load_growth_rate * 100:.0f}%\n"
        f"─────────────────────\n"
        f"Investment: ${vp.investment_cost:,.0f}\n"
        f"Annual Savings: ${r.estimated_savings:,.0f}\n"
        f"Payback: {r.payback_period_years:.2f} yr\n"
        f"OPEX Impact: {r.impact_on_opex_pct * 100:.2f}%\n"
        f"─────────────────────\n"
        f"Water Saved: {r.water_savings_l:,.0f} L\n"
        f"Water $ Saved: ${r.water_savings_cost:,.0f}"
    )
    ax3.text(0.05, 0.95, metrics_text, transform=ax3.transAxes,
             fontsize=10, color=text_color, va="top", fontfamily="monospace",
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#0d1117",
                       edgecolor="#30363d"))
    ax3.set_title("Key Metrics", color=text_color, fontsize=12, fontweight="bold")

    # ── 4d. Radar chart of factor scores ──
    ax4 = fig.add_subplot(gs[1, 0], polar=True)
    ax4.set_facecolor("#161b22")
    categories = ["$/MW", "Water%", "OPEX%", "Payback", "Load Growth"]
    values = [min(r.factor_scores[k], 100) for k in keys]
    values += values[:1]  # close the polygon
    angles = np.linspace(0, 2 * np.pi, len(categories), endpoint=False).tolist()
    angles += angles[:1]
    ax4.fill(angles, values, color=accent, alpha=0.25)
    ax4.plot(angles, values, color=accent, linewidth=2)
    ax4.set_xticks(angles[:-1])
    ax4.set_xticklabels(categories, color=text_color, fontsize=8)
    ax4.set_ylim(0, 100)
    ax4.set_title("Factor Radar (0–100)", color=text_color, fontsize=12,
                  fontweight="bold", pad=20)
    ax4.tick_params(colors=text_color)
    ax4.spines["polar"].set_color("#30363d")

    # ── 4e. Payback sensitivity (varying energy reduction %) ──
    ax5 = fig.add_subplot(gs[1, 1])
    ax5.set_facecolor("#161b22")
    reductions = np.linspace(0.02, 0.15, 50)
    paybacks = []
    for red in reductions:
        vp_temp = VigilentParams(vp.num_years, vp.investment_cost, red, vp.water_reduction_pct)
        r_temp = compute(dc, vp_temp)
        paybacks.append(r_temp.payback_period_years)
    ax5.plot(reductions * 100, paybacks, color=green, linewidth=2)
    ax5.axhline(y=1.0, color=orange, linestyle="--", alpha=0.6, label="1-yr payback")
    ax5.axvline(x=vp.energy_reduction_pct * 100, color=accent, linestyle=":",
                alpha=0.6, label=f"Current ({vp.energy_reduction_pct * 100:.0f}%)")
    ax5.set_xlabel("Energy Reduction %", color=text_color, fontsize=9)
    ax5.set_ylabel("Payback (years)", color=text_color, fontsize=9)
    ax5.set_title("Payback Sensitivity", color=text_color, fontsize=12, fontweight="bold")
    ax5.legend(fontsize=8, facecolor="#161b22", edgecolor="#30363d", labelcolor=text_color)
    ax5.tick_params(colors=text_color)
    for spine in ax5.spines.values():
        spine.set_color("#30363d")

    # ── 4f. Composite score sensitivity (varying DC size) ──
    ax6 = fig.add_subplot(gs[1, 2])
    ax6.set_facecolor("#161b22")
    sizes = np.linspace(5, 100, 50)
    scores = []
    for sz in sizes:
        dc_temp = DataCenterInputs(
            dc_size_mw=sz, baseline_pue=dc.baseline_pue,
            electricity_price=dc.electricity_price,
            load_growth_rate=dc.load_growth_rate,
            annual_water_usage_l=dc.annual_water_usage_l,
            water_cost_per_l=dc.water_cost_per_l,
            energy_pct_opex=dc.energy_pct_opex
        )
        r_temp = compute(dc_temp, vp)
        scores.append(r_temp.composite_score)
    ax6.plot(sizes, scores, color=accent, linewidth=2)
    ax6.axvline(x=dc.dc_size_mw, color=orange, linestyle=":", alpha=0.6,
                label=f"Current ({dc.dc_size_mw:.0f} MW)")
    ax6.set_xlabel("DC Size (MW)", color=text_color, fontsize=9)
    ax6.set_ylabel("Composite Score", color=text_color, fontsize=9)
    ax6.set_title("Score vs DC Size", color=text_color, fontsize=12, fontweight="bold")
    ax6.legend(fontsize=8, facecolor="#161b22", edgecolor="#30363d", labelcolor=text_color)
    ax6.tick_params(colors=text_color)
    for spine in ax6.spines.values():
        spine.set_color("#30363d")

    plt.savefig(os.path.join(OUTPUT_DIR, "dashboard.png"), dpi=150,
                bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"  [saved] {OUTPUT_DIR}/dashboard.png")


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  HEATMAPS — Every meaningful 2-axis + color combination
# ═══════════════════════════════════════════════════════════════════════════════

def _generate_grid(dc_base, vp_base, x_param, x_range, y_param, y_range, z_metric):
    """Sweep two input parameters and compute a z-metric over the grid."""
    X, Y = np.meshgrid(x_range, y_range)
    Z = np.zeros_like(X)

    for i in range(len(y_range)):
        for j in range(len(x_range)):
            dc = DataCenterInputs(
                dc_size_mw=dc_base.dc_size_mw,
                baseline_pue=dc_base.baseline_pue,
                electricity_price=dc_base.electricity_price,
                load_growth_rate=dc_base.load_growth_rate,
                annual_water_usage_l=dc_base.annual_water_usage_l,
                water_cost_per_l=dc_base.water_cost_per_l,
                energy_pct_opex=dc_base.energy_pct_opex,
            )
            vp = VigilentParams(
                num_years=vp_base.num_years,
                investment_cost=vp_base.investment_cost,
                energy_reduction_pct=vp_base.energy_reduction_pct,
                water_reduction_pct=vp_base.water_reduction_pct,
            )
            # Set x-axis param
            _set_param(dc, vp, x_param, X[i, j])
            # Set y-axis param
            _set_param(dc, vp, y_param, Y[i, j])
            r = compute(dc, vp)
            Z[i, j] = _get_metric(r, dc, z_metric)
    return X, Y, Z


def _set_param(dc, vp, name, val):
    param_map = {
        "dc_size_mw": ("dc", "dc_size_mw"),
        "baseline_pue": ("dc", "baseline_pue"),
        "electricity_price": ("dc", "electricity_price"),
        "load_growth_rate": ("dc", "load_growth_rate"),
        "annual_water_usage_l": ("dc", "annual_water_usage_l"),
        "energy_reduction_pct": ("vp", "energy_reduction_pct"),
        "water_reduction_pct": ("vp", "water_reduction_pct"),
        "investment_cost": ("vp", "investment_cost"),
    }
    target, attr = param_map[name]
    if target == "dc":
        setattr(dc, attr, val)
    else:
        setattr(vp, attr, val)


def _get_metric(r, dc, name):
    metric_map = {
        "savings_per_mw": r.savings_per_mw,
        "composite_score": r.composite_score,
        "payback_period": r.payback_period_years,
        "estimated_savings": r.estimated_savings,
        "impact_on_opex_pct": r.impact_on_opex_pct * 100,
        "water_savings_pct": r.water_savings_pct * 100,
        "annual_energy_cost": r.annual_energy_cost,
        "load_growth_rate": dc.load_growth_rate * 100,
    }
    return metric_map.get(name, 0)


PARAM_LABELS = {
    "dc_size_mw": "DC Size (MW)",
    "baseline_pue": "Baseline PUE",
    "electricity_price": "Electricity Price ($/kWh)",
    "load_growth_rate": "Load Growth Rate (%)",
    "annual_water_usage_l": "Annual Water Usage (L)",
    "energy_reduction_pct": "Energy Reduction (%)",
    "water_reduction_pct": "Water Reduction (%)",
    "investment_cost": "Investment Cost ($)",
}

METRIC_LABELS = {
    "savings_per_mw": "Savings per MW ($)",
    "composite_score": "Composite Score",
    "payback_period": "Payback Period (years)",
    "estimated_savings": "Annual Savings ($)",
    "impact_on_opex_pct": "OPEX Impact (%)",
    "water_savings_pct": "Water Savings (%)",
    "annual_energy_cost": "Annual Energy Cost ($)",
    "load_growth_rate": "Load Growth (%)",
}


def plot_heatmaps(dc: DataCenterInputs, vp: VigilentParams):
    """Generate a suite of heatmaps — each shows two swept axes + a color metric."""

    heatmap_configs = [
        # (x_param, x_range, y_param, y_range, z_metric, title, cmap)
        (
            "dc_size_mw", np.linspace(5, 100, 30),
            "electricity_price", np.linspace(0.04, 0.30, 30),
            "savings_per_mw",
            "Savings/MW vs DC Size & Elec. Price",
            "YlGn",
        ),
        (
            "dc_size_mw", np.linspace(5, 100, 30),
            "baseline_pue", np.linspace(1.1, 2.0, 30),
            "composite_score",
            "Composite Score vs DC Size & PUE",
            "RdYlGn",
        ),
        (
            "energy_reduction_pct", np.linspace(0.02, 0.15, 30),
            "electricity_price", np.linspace(0.04, 0.30, 30),
            "payback_period",
            "Payback Period vs Energy Reduction & Elec. Price",
            "RdYlGn_r",
        ),
        (
            "dc_size_mw", np.linspace(5, 100, 30),
            "load_growth_rate", np.linspace(0.02, 0.20, 30),
            "savings_per_mw",
            "Savings/MW vs DC Size & Load Growth",
            "plasma",
        ),
        (
            "energy_reduction_pct", np.linspace(0.02, 0.15, 30),
            "water_reduction_pct", np.linspace(0.01, 0.10, 30),
            "composite_score",
            "Composite Score vs Energy & Water Reduction",
            "viridis",
        ),
        (
            "baseline_pue", np.linspace(1.1, 2.0, 30),
            "load_growth_rate", np.linspace(0.02, 0.20, 30),
            "estimated_savings",
            "Annual Savings vs PUE & Load Growth",
            "magma",
        ),
    ]

    fig, axes = plt.subplots(2, 3, figsize=(20, 12), facecolor="#0d1117")
    fig.suptitle("Vigilent Optimization Heatmaps",
                 fontsize=18, fontweight="bold", color="#c9d1d9", y=0.98)
    text_color = "#c9d1d9"

    for idx, (x_p, x_r, y_p, y_r, z_m, title, cmap) in enumerate(heatmap_configs):
        ax = axes[idx // 3, idx % 3]
        ax.set_facecolor("#161b22")

        X, Y, Z = _generate_grid(dc, vp, x_p, x_r, y_p, y_r, z_m)

        # For percentage-based axes, scale display
        x_display = X * 100 if "pct" in x_p or "rate" in x_p else X
        y_display = Y * 100 if "pct" in y_p or "rate" in y_p else Y

        im = ax.pcolormesh(x_display, y_display, Z, cmap=cmap, shading="auto")
        cbar = fig.colorbar(im, ax=ax, shrink=0.8)
        cbar.set_label(METRIC_LABELS.get(z_m, z_m), color=text_color, fontsize=8)
        cbar.ax.tick_params(colors=text_color, labelsize=7)

        x_label = PARAM_LABELS.get(x_p, x_p)
        y_label = PARAM_LABELS.get(y_p, y_p)
        ax.set_xlabel(x_label, color=text_color, fontsize=8)
        ax.set_ylabel(y_label, color=text_color, fontsize=8)
        ax.set_title(title, color=text_color, fontsize=10, fontweight="bold")
        ax.tick_params(colors=text_color, labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#30363d")

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    plt.savefig(os.path.join(OUTPUT_DIR, "heatmaps.png"), dpi=150,
                bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"  [saved] {OUTPUT_DIR}/heatmaps.png")


# ═══════════════════════════════════════════════════════════════════════════════
# 6.  BUBBLE CHARTS (per advisor: focused axes + bubble size/color for factors)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_bubble_charts(dc: DataCenterInputs, vp: VigilentParams):
    """
    Multiple bubble charts where:
    - X-axis & Y-axis show two key metrics
    - Bubble SIZE  = one factor (e.g., load growth, DC size)
    - Bubble COLOR = another factor (e.g., composite score, payback)

    Each bubble = one simulated data center scenario.
    """
    np.random.seed(42)
    n = 120  # number of scenario points

    # Generate random scenarios across plausible ranges
    sizes      = np.random.uniform(5, 100, n)
    pues       = np.random.uniform(1.1, 2.0, n)
    prices     = np.random.uniform(0.04, 0.30, n)
    growths    = np.random.uniform(0.02, 0.20, n)
    waters     = np.random.uniform(50e6, 500e6, n)
    e_reds     = np.random.uniform(0.03, 0.12, n)
    w_reds     = np.random.uniform(0.02, 0.08, n)
    invests    = np.random.uniform(200_000, 3_000_000, n)

    # Compute all metrics for each scenario
    savings_mw = np.zeros(n)
    paybacks   = np.zeros(n)
    comp_scores = np.zeros(n)
    opex_impacts = np.zeros(n)
    est_savings = np.zeros(n)
    water_pcts  = np.zeros(n)
    energy_costs = np.zeros(n)

    for i in range(n):
        dc_i = DataCenterInputs(
            dc_size_mw=sizes[i], baseline_pue=pues[i],
            electricity_price=prices[i], load_growth_rate=growths[i],
            annual_water_usage_l=waters[i], water_cost_per_l=dc.water_cost_per_l,
            energy_pct_opex=dc.energy_pct_opex
        )
        vp_i = VigilentParams(1, invests[i], e_reds[i], w_reds[i])
        r_i = compute(dc_i, vp_i)
        savings_mw[i]   = r_i.savings_per_mw
        paybacks[i]     = min(r_i.payback_period_years, 10)
        comp_scores[i]  = r_i.composite_score
        opex_impacts[i] = r_i.impact_on_opex_pct * 100
        est_savings[i]  = r_i.estimated_savings
        water_pcts[i]   = r_i.water_savings_pct * 100
        energy_costs[i] = r_i.annual_energy_cost

    text_color = "#c9d1d9"

    # ── Chart configs ──
    bubble_configs = [
        {
            "x": savings_mw, "xlabel": "Savings per MW ($)",
            "y": water_pcts, "ylabel": "Water Savings (%)",
            "size": growths * 2000, "size_label": "Load Growth",
            "color": comp_scores, "color_label": "Composite Score",
            "cmap": "RdYlGn", "title": "Savings/MW vs Water Usage\n(size=Load Growth, color=Composite Score)",
        },
        {
            "x": savings_mw, "xlabel": "Savings per MW ($)",
            "y": opex_impacts, "ylabel": "OPEX Impact (%)",
            "size": sizes * 15, "size_label": "DC Size (MW)",
            "color": paybacks, "color_label": "Payback (years)",
            "cmap": "RdYlGn_r", "title": "Savings/MW vs OPEX Impact\n(size=DC Size, color=Payback)",
        },
        {
            "x": est_savings / 1e6, "xlabel": "Annual Savings ($M)",
            "y": paybacks, "ylabel": "Payback Period (years)",
            "size": pues * 300, "size_label": "PUE",
            "color": growths * 100, "color_label": "Load Growth (%)",
            "cmap": "plasma", "title": "Savings vs Payback\n(size=PUE, color=Load Growth)",
        },
        {
            "x": prices * 100, "xlabel": "Electricity Price (¢/kWh)",
            "y": savings_mw, "ylabel": "Savings per MW ($)",
            "size": e_reds * 5000, "size_label": "Energy Reduction %",
            "color": comp_scores, "color_label": "Composite Score",
            "cmap": "viridis", "title": "Elec. Price vs Savings/MW\n(size=Energy Red%, color=Score)",
        },
        {
            "x": sizes, "xlabel": "DC Size (MW)",
            "y": comp_scores, "ylabel": "Composite Score",
            "size": prices * 2000, "size_label": "Elec. Price",
            "color": paybacks, "color_label": "Payback (years)",
            "cmap": "coolwarm_r", "title": "DC Size vs Composite Score\n(size=Elec Price, color=Payback)",
        },
        {
            "x": energy_costs / 1e6, "xlabel": "Annual Energy Cost ($M)",
            "y": water_pcts, "ylabel": "Water Savings (%)",
            "size": growths * 2000, "size_label": "Load Growth",
            "color": opex_impacts, "color_label": "OPEX Impact (%)",
            "cmap": "magma", "title": "Energy Cost vs Water Savings\n(size=Load Growth, color=OPEX Impact)",
        },
    ]

    fig, axes = plt.subplots(2, 3, figsize=(22, 13), facecolor="#0d1117")
    fig.suptitle("Vigilent Scenario Analysis — Bubble Charts\n"
                 "(120 randomized data center scenarios)",
                 fontsize=16, fontweight="bold", color=text_color, y=0.99)

    for idx, cfg in enumerate(bubble_configs):
        ax = axes[idx // 3, idx % 3]
        ax.set_facecolor("#161b22")

        sc = ax.scatter(
            cfg["x"], cfg["y"],
            s=cfg["size"], c=cfg["color"],
            cmap=cfg["cmap"], alpha=0.65,
            edgecolors="#30363d", linewidth=0.5,
        )
        cbar = fig.colorbar(sc, ax=ax, shrink=0.8)
        cbar.set_label(cfg["color_label"], color=text_color, fontsize=8)
        cbar.ax.tick_params(colors=text_color, labelsize=7)

        ax.set_xlabel(cfg["xlabel"], color=text_color, fontsize=9)
        ax.set_ylabel(cfg["ylabel"], color=text_color, fontsize=9)
        ax.set_title(cfg["title"], color=text_color, fontsize=9, fontweight="bold")
        ax.tick_params(colors=text_color, labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#30363d")

    # Add legend for bubble sizes
    fig.text(0.5, 0.01,
             "Bubble size encodes the labeled factor  |  Color encodes the colorbar variable  "
             "|  Each bubble = one simulated scenario",
             ha="center", color="#8b949e", fontsize=9, style="italic")

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    plt.savefig(os.path.join(OUTPUT_DIR, "bubble_charts.png"), dpi=150,
                bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"  [saved] {OUTPUT_DIR}/bubble_charts.png")


# ═══════════════════════════════════════════════════════════════════════════════
# 7.  INDIVIDUAL HIGH-RES HEATMAPS (for detailed exploration)
# ═══════════════════════════════════════════════════════════════════════════════

def plot_individual_heatmaps(dc: DataCenterInputs, vp: VigilentParams):
    """Generate individual large heatmaps for the most insightful combos."""
    text_color = "#c9d1d9"

    configs = [
        # (filename, x_param, x_range, y_param, y_range, z_metric, title, cmap)
        (
            "heatmap_savings_mw_vs_water.png",
            "dc_size_mw", np.linspace(5, 100, 50),
            "annual_water_usage_l", np.linspace(50e6, 500e6, 50),
            "composite_score",
            "Composite Score\nX: DC Size (MW) — Y: Water Usage (L) — Color: Score",
            "RdYlGn",
        ),
        (
            "heatmap_price_vs_pue.png",
            "electricity_price", np.linspace(0.04, 0.30, 50),
            "baseline_pue", np.linspace(1.1, 2.0, 50),
            "savings_per_mw",
            "Savings per MW ($)\nX: Elec. Price ($/kWh) — Y: PUE — Color: $/MW",
            "YlOrRd",
        ),
        (
            "heatmap_reduction_vs_growth.png",
            "energy_reduction_pct", np.linspace(0.02, 0.15, 50),
            "load_growth_rate", np.linspace(0.02, 0.20, 50),
            "payback_period",
            "Payback Period (years)\nX: Energy Reduction (%) — Y: Load Growth (%) — Color: Payback",
            "RdYlGn_r",
        ),
    ]

    for fname, x_p, x_r, y_p, y_r, z_m, title, cmap in configs:
        fig, ax = plt.subplots(figsize=(10, 8), facecolor="#0d1117")
        ax.set_facecolor("#161b22")

        X, Y, Z = _generate_grid(dc, vp, x_p, x_r, y_p, y_r, z_m)

        x_display = X * 100 if "pct" in x_p or "rate" in x_p else X
        y_display = Y * 100 if "pct" in y_p or "rate" in y_p else Y
        if "water" in y_p:
            y_display = Y / 1e6

        im = ax.pcolormesh(x_display, y_display, Z, cmap=cmap, shading="auto")
        cbar = fig.colorbar(im, ax=ax)
        cbar.set_label(METRIC_LABELS.get(z_m, z_m), color=text_color, fontsize=11)
        cbar.ax.tick_params(colors=text_color)

        x_label = PARAM_LABELS.get(x_p, x_p)
        y_label = PARAM_LABELS.get(y_p, y_p)
        if "water" in y_p:
            y_label = "Annual Water Usage (M liters)"
        ax.set_xlabel(x_label, color=text_color, fontsize=12)
        ax.set_ylabel(y_label, color=text_color, fontsize=12)
        ax.set_title(title, color=text_color, fontsize=13, fontweight="bold")
        ax.tick_params(colors=text_color)
        for spine in ax.spines.values():
            spine.set_color("#30363d")

        plt.savefig(os.path.join(OUTPUT_DIR, fname), dpi=150,
                    bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close()
        print(f"  [saved] {OUTPUT_DIR}/{fname}")


# ═══════════════════════════════════════════════════════════════════════════════
# 7b. GRID BUBBLE HEATMAPS — 10×10 evenly spaced, color = composite score tier
# ═══════════════════════════════════════════════════════════════════════════════

def _score_to_color(score):
    """Map composite score to the 4-tier color scheme."""
    if score >= 75:
        return "#25ac01"   # green  — excellent
    elif score >= 50:
        return "#1075E8"   # blue   — good
    elif score >= 25:
        return "#88b4f2"   # light blue — moderate
    else:
        return "#d7e7f8"   # pale blue  — low


def plot_grid_bubble_heatmaps(dc: DataCenterInputs, vp: VigilentParams):
    """
    10×10 evenly-spaced bubble grids on a WHITE background.
    Two axes are swept; all other variables held constant at user defaults.
    Dot color = composite score tier (4 discrete colors).
    """
    N = 10  # bubbles per axis

    grid_configs = [
        # (filename, x_param, x_lo, x_hi, y_param, y_lo, y_hi, title)
        (
            "grid_dcsize_vs_price.png",
            "dc_size_mw", 5, 100,
            "electricity_price", 0.04, 0.30,
            "DC Size (MW) vs Electricity Price ($/kWh)",
        ),
        (
            "grid_dcsize_vs_pue.png",
            "dc_size_mw", 5, 100,
            "baseline_pue", 1.1, 2.0,
            "DC Size (MW) vs Baseline PUE",
        ),
        (
            "grid_dcsize_vs_loadgrowth.png",
            "dc_size_mw", 5, 100,
            "load_growth_rate", 0.02, 0.20,
            "DC Size (MW) vs Load Growth Rate (%)",
        ),
        (
            "grid_energyred_vs_price.png",
            "energy_reduction_pct", 0.02, 0.15,
            "electricity_price", 0.04, 0.30,
            "Energy Reduction (%) vs Electricity Price ($/kWh)",
        ),
        (
            "grid_energyred_vs_waterred.png",
            "energy_reduction_pct", 0.02, 0.15,
            "water_reduction_pct", 0.01, 0.10,
            "Energy Reduction (%) vs Water Reduction (%)",
        ),
        (
            "grid_pue_vs_loadgrowth.png",
            "baseline_pue", 1.1, 2.0,
            "load_growth_rate", 0.02, 0.20,
            "Baseline PUE vs Load Growth Rate (%)",
        ),
    ]

    for fname, x_p, x_lo, x_hi, y_p, y_lo, y_hi, title in grid_configs:
        x_vals = np.linspace(x_lo, x_hi, N)
        y_vals = np.linspace(y_lo, y_hi, N)

        fig, ax = plt.subplots(figsize=(10, 8), facecolor="white")
        ax.set_facecolor("white")

        # Compute composite score at every grid point
        for xi, xv in enumerate(x_vals):
            for yi, yv in enumerate(y_vals):
                dc_tmp = DataCenterInputs(
                    dc_size_mw=dc.dc_size_mw,
                    baseline_pue=dc.baseline_pue,
                    electricity_price=dc.electricity_price,
                    load_growth_rate=dc.load_growth_rate,
                    annual_water_usage_l=dc.annual_water_usage_l,
                    water_cost_per_l=dc.water_cost_per_l,
                    energy_pct_opex=dc.energy_pct_opex,
                )
                vp_tmp = VigilentParams(
                    num_years=vp.num_years,
                    investment_cost=vp.investment_cost,
                    energy_reduction_pct=vp.energy_reduction_pct,
                    water_reduction_pct=vp.water_reduction_pct,
                )
                _set_param(dc_tmp, vp_tmp, x_p, xv)
                _set_param(dc_tmp, vp_tmp, y_p, yv)
                r_tmp = compute(dc_tmp, vp_tmp)

                # Display values: convert pct/rate to % for axis display
                x_disp = xv * 100 if ("pct" in x_p or "rate" in x_p) else xv
                y_disp = yv * 100 if ("pct" in y_p or "rate" in y_p) else yv

                color = _score_to_color(r_tmp.composite_score)
                ax.scatter(x_disp, y_disp, s=220, c=color,
                           edgecolors="#333333", linewidth=0.6, zorder=3)

        # Axis labels
        x_label = PARAM_LABELS.get(x_p, x_p)
        y_label = PARAM_LABELS.get(y_p, y_p)
        ax.set_xlabel(x_label, fontsize=12, fontweight="bold", color="#222222")
        ax.set_ylabel(y_label, fontsize=12, fontweight="bold", color="#222222")
        ax.set_title(title + "\nColor = Composite Score",
                     fontsize=13, fontweight="bold", color="#111111", pad=14)

        # Grid
        ax.grid(True, linestyle="--", alpha=0.3, color="#aaaaaa")
        ax.tick_params(colors="#333333")
        for spine in ax.spines.values():
            spine.set_color("#cccccc")

        # Legend (manual)
        from matplotlib.lines import Line2D
        legend_elements = [
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#25ac01",
                   markersize=12, markeredgecolor="#333", label="75–100  (Excellent)"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#1075E8",
                   markersize=12, markeredgecolor="#333", label="50–74   (Good)"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#88b4f2",
                   markersize=12, markeredgecolor="#333", label="25–49   (Moderate)"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#d7e7f8",
                   markersize=12, markeredgecolor="#333", label="0–24    (Low)"),
        ]
        ax.legend(handles=legend_elements, title="Composite Score",
                  loc="upper right", fontsize=9, title_fontsize=10,
                  facecolor="white", edgecolor="#cccccc", framealpha=0.95)

        # Held-constant annotation
        held = []
        all_params = {
            "dc_size_mw": f"DC Size={dc.dc_size_mw:.0f}MW",
            "baseline_pue": f"PUE={dc.baseline_pue:.2f}",
            "electricity_price": f"Price={dc.electricity_price:.2f}/kWh",
            "load_growth_rate": f"Growth={dc.load_growth_rate*100:.0f}%",
            "energy_reduction_pct": f"E.Red={vp.energy_reduction_pct*100:.0f}%",
            "water_reduction_pct": f"W.Red={vp.water_reduction_pct*100:.0f}%",
            "investment_cost": f"Invest={vp.investment_cost:,.0f}",
        }
        for k, v in all_params.items():
            if k != x_p and k != y_p:
                held.append(v)
        ax.text(0.02, 0.02, "Held constant: " + " | ".join(held),
                transform=ax.transAxes, fontsize=7.5, color="#666666",
                style="italic", va="bottom")

        plt.tight_layout()
        plt.savefig(os.path.join(OUTPUT_DIR, fname), dpi=150,
                    bbox_inches="tight", facecolor="white")
        plt.close()
        print(f"  [saved] {OUTPUT_DIR}/{fname}")


# ═══════════════════════════════════════════════════════════════════════════════
# 8.  INTERACTIVE INPUT
# ═══════════════════════════════════════════════════════════════════════════════

def prompt_float(label, default, suffix=""):
    """Prompt user for a float with a default."""
    raw = input(f"  {label} [{default}{suffix}]: ").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"    Invalid input, using default: {default}")
        return default


def interactive_input() -> tuple[DataCenterInputs, VigilentParams]:
    print("\n┌──────────────────────────────────────────────┐")
    print("│  VIGILENT OPTIMIZER — Enter Your Parameters  │")
    print("└──────────────────────────────────────────────┘\n")

    print("  DATA CENTER:")
    dc = DataCenterInputs()
    dc.dc_size_mw           = prompt_float("DC Size (MW)", 20)
    dc.baseline_pue         = prompt_float("Baseline PUE", 1.4)
    dc.electricity_price    = prompt_float("Elec Price ($/kWh)", 0.10)
    dc.load_growth_rate     = prompt_float("Load Growth Rate (%)", 8) / 100
    dc.annual_water_usage_l = prompt_float("Annual Water (liters)", 315_000_000)
    dc.water_cost_per_l     = prompt_float("Water Cost ($/L)", 0.003)
    dc.energy_pct_opex      = prompt_float("Energy % of OPEX (%)", 40) / 100

    print("\n  VIGILENT DEPLOYMENT:")
    vp = VigilentParams()
    vp.investment_cost      = prompt_float("Investment Cost ($)", 1_500_000)
    vp.energy_reduction_pct = prompt_float("Energy Reduction (%)", 7) / 100
    vp.water_reduction_pct  = prompt_float("Water Reduction (%)", 4) / 100
    vp.num_years            = int(prompt_float("Number of Years", 1))

    return dc, vp


# ═══════════════════════════════════════════════════════════════════════════════
# 9.  MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Vigilent DC Optimization Model")
    parser.add_argument("--defaults", action="store_true",
                        help="Run with spreadsheet default values")
    parser.add_argument("--demo", action="store_true",
                        help="Run demo with multiple pre-set scenarios")
    args = parser.parse_args()

    if args.defaults or args.demo:
        dc = DataCenterInputs()
        vp = VigilentParams()
    else:
        dc, vp = interactive_input()

    # Compute and display
    result = compute(dc, vp)
    print_results(dc, vp, result)

    # Generate all visualizations
    print("  Generating visualizations...")
    plot_dashboard(dc, vp, result)
    plot_heatmaps(dc, vp)
    plot_bubble_charts(dc, vp)
    plot_individual_heatmaps(dc, vp)
    plot_grid_bubble_heatmaps(dc, vp)

    if args.demo:
        # Also run a second scenario (the Sheet2 config)
        print("\n" + "=" * 64)
        print("  SCENARIO 2: High-PUE / High-Growth Data Center")
        print("=" * 64)
        dc2 = DataCenterInputs(
            dc_size_mw=20, baseline_pue=1.58,
            electricity_price=0.26, load_growth_rate=0.15,
            annual_water_usage_l=50, water_cost_per_l=0.001,
            energy_pct_opex=0.40,
        )
        vp2 = VigilentParams(
            num_years=1, investment_cost=10_000,
            energy_reduction_pct=0.075, water_reduction_pct=0.075,
        )
        r2 = compute(dc2, vp2)
        print_results(dc2, vp2, r2)

    print(f"\n  All outputs saved to: {OUTPUT_DIR}/")
    print("  Files: dashboard.png, heatmaps.png, bubble_charts.png,")
    print("         heatmap_savings_mw_vs_water.png,")
    print("         heatmap_price_vs_pue.png,")
    print("         heatmap_reduction_vs_growth.png,")
    print("         grid_dcsize_vs_price.png,")
    print("         grid_dcsize_vs_pue.png,")
    print("         grid_dcsize_vs_loadgrowth.png,")
    print("         grid_energyred_vs_price.png,")
    print("         grid_energyred_vs_waterred.png,")
    print("         grid_pue_vs_loadgrowth.png\n")


if __name__ == "__main__":
    main()
