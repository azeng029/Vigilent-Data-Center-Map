# Capacity Factor + OPEX Tier Update — Change-Impact Memo

**Author:** Adam Tang
**Date:** 2026-04-27
**Scope:** Vigilent scoring model & client-facing map

---

## 1. What changed

| Assumption | Before | After | Source |
|---|---|---|---|
| Capacity factor | implicit **1.00** (8760 hr/yr full uptime) | **0.70** (explicit input) | Uptime Institute / IDC average IT load utilization — accounts for downtime, scheduled maintenance, redundancy headroom, growth ramp, and underutilized rack space |
| Energy % of OPEX | flat **0.40** for all DCs | **operator-tier lookup** (3 tiers) | Uptime Institute / JLL data center cost surveys |

### Operator-tier breakdown for `energy_pct_opex`

| Tier | Value | Examples (in DB) | DC count |
|---|---|---|---|
| HYPERSCALE | **0.32** | AWS, Google, Microsoft, Meta | 20 |
| WHOLESALE_COLO | **0.40** (default) | Digital Realty, Equinix, NTT, CyrusOne, QTS, Iron Mountain, Vantage, Cologix, ST Telemedia, … | 356 |
| ENTERPRISE | **0.48** | C Spire, TRG, Flexential, Conapto, Datum, Sabey, … | 41 |

Unmatched operators fall back to WHOLESALE_COLO (industry midline). Lookup table: `operator_tiers.py`.

---

## 2. Why these numbers

**Capacity factor 0.70.** The pre-update model multiplied dc_size × PUE × 8760 to get annual energy consumption, which silently assumed every DC runs at full IT load every hour of every year. In practice, surveyed data centers run at an average ~65–75% IT load factor once you account for redundant capacity (N+1, 2N), scheduled maintenance windows, rack-level underutilization, and the multi-year ramp on newly built sites. 0.70 is the median in this range.

**OPEX share by operator tier.** No public source publishes per-DC OPEX figures — even the public REITs (Equinix, Digital Realty) only report at the corporate level in 10-Ks. But Uptime Institute and JLL publish energy-share-of-OPEX *by operator class*:

- **Hyperscale** operators run highly optimized facilities and amortize large capex; energy is a smaller slice of total OPEX (~30–35%).
- **Wholesale colocation** is the industry midline (~38–42%).
- **Enterprise / regional / legacy** facilities have lower capex amortization and smaller staff bases, so energy dominates a larger share (~45–52%).

The tiered approach gives the map three distinct OPEX-impact values across DCs (3.2% / 4.0% / 4.8%) instead of a uniform 4.0%, which was the team's critique.

---

## 3. Worked example — reference 20 MW DC

**Inputs:** 20 MW, PUE 1.55, $0.10/kWh, 10% load growth, Vigilent standard offering ($1.5M / 10% energy reduction).

```
annual_energy_kwh = 20 × 1.55 × 1000 × 8760 × CF × (1 + 0.10)
```

| Metric | Before (CF=1.0, OPEX=40%) | After (CF=0.70, OPEX=40%) |
|---|---|---|
| Annual energy | 298.7 GWh | **209.1 GWh** |
| Annual energy cost | $29.87 M | **$20.91 M** |
| Annual savings (10% energy reduction) | $2.99 M | **$2.09 M** |
| Savings/MW | $149,358 | **$104,551** |
| Payback | 0.50 yr | **0.72 yr** |
| Composite score | 60.8 | **54.5** |

Energy-related metrics scale by exactly 0.70. Payback grows proportionally because investment is unchanged.

---

## 4. Worked examples — three real DCs

| DC | Size | Operator → tier | Composite | Savings/MW | Payback | OPEX impact |
|---|---|---|---|---|---|---|
| **Microsoft DUB05 (Dublin)** | 20 MW | Microsoft → HYPERSCALE | 80.1 → **78.1** | $448,074 → **$313,652** | 0.17 → **0.24 yr** | 4.0% → **3.2%** |
| **Digital Realty IAD20 (Ashburn)** | 36 MW | Digital Realty → WHOLESALE_COLO | 61.4 → **55.7** | $145,325 → **$101,728** | 0.29 → **0.41 yr** | 4.0% → **4.0%** |
| **C Spire Starkville (MS)** | 5 MW | C Spire → ENTERPRISE | 60.1 → **51.6** | $189,237 → **$132,466** | 1.59 → **2.26 yr** | 4.0% → **4.8%** |

OPEX impact moves in opposite directions by tier: hyperscale ↓ (less of their cost is energy) and enterprise ↑ (more of their cost is energy). This is the intended behavior — Vigilent's pitch is more compelling at facilities where energy dominates the budget.

---

## 5. Aggregate impact across the full database (417 DCs)

| Metric | Before | After | Delta |
|---|---|---|---|
| Mean composite score | 68.00 | **61.53** | −6.47 |
| Median composite score | 67.83 | **60.10** | −7.73 |
| Mean payback | 1.23 yr | **1.76 yr** | +0.53 yr |
| Median payback | 0.43 yr | **0.61 yr** | +0.18 yr |
| Mean savings/MW | $271,179 | **$189,826** | −$81,353 |
| Total annual savings | $3.18 B | **$2.23 B** | −$954 M |

**Classification migration:**

| Class | Before | After |
|---|---|---|
| Excellent (≥75) | 181 | 88 |
| Good (50–74) | 204 | 256 |
| Moderate (25–49) | 32 | 71 |
| Low (<25) | 0 | 2 |

The map now shows fewer "Excellent" green pins and more "Good" blue ones — a more conservative and defensible distribution.

---

## 6. Files changed

| File | Change |
|---|---|
| `inputs_spec.json` | Added `capacity_factor` row; revised `energy_pct_opex` source; added `operator_tiers` block |
| `vigilent_engine.py` | `capacity_factor` parameter on `compute_score`, `compute_exhaustive_sweep`, `compute_ej_impact`; threaded into the 8760-hour energy calc in all three places |
| `operator_tiers.py` | **NEW** — single source of truth for the operator → tier mapping (~80 entries with name normalization) |
| `score_datacenters.py` | Per-DC OPEX from operator lookup; CF=0.70 default; sensitivity sweeps updated |
| `build_map.py` | Same tier lookup + CF; new GeoJSON properties `operator_tier`, `energy_pct_opex`, `capacity_factor` for popup transparency |
| `simulation.py` | Capacity Factor slider auto-renders in DC Finder & Optimizer (added to `DC_PARAMS`); heatmap callback wired |
| `Vigilent Calculator.xlsx` | `Inputs` sheet updated: new `capacity_factor` row, `energy_pct_opex` source rewritten, tier table appended |

---

## 7. How to reproduce / re-apply

```bash
# 1. Edit defaults if needed in inputs_spec.json or operator_tiers.py
# 2. Re-score all DCs:
python3 score_datacenters.py
# 3. Sync new scores into the map's GeoJSON layers:
python3 sync_map_from_csv.py
# 4. Reload the Netlify-hosted map (or the local index.html) — popups now reflect new values.
```

To override capacity factor for a what-if (e.g., for a specific client running a hyperscale-utilized site), override `DEFAULTS["capacity_factor"]` in `score_datacenters.py` before step 2, or use the new Capacity Factor slider in the Dash app at the DC Finder / Optimizer tabs.

---

## 8. Open questions for team review

1. **Hyperscale tier coverage.** Only AWS / Google / Microsoft / Meta are seeded. Should Oracle, Apple, Tencent, Alibaba, Baidu be added when those DCs enter the database?
2. **Edge cases for tier classification.** Ascenty, Switch, and Aligned were placed in WHOLESALE_COLO despite running hyperscale-class facilities. The argument for keeping them in wholesale is that they *lease* space to others rather than running their own SaaS workloads. Reasonable to revisit.
3. **Capacity factor as a per-DC field in the database.** If Caroline / Luis can collect actual utilization data per facility, we could move CF from a flat 0.70 default to a per-DC value (mirrors how electricity_price already works).
