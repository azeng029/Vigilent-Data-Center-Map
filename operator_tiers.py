"""
Operator → tier lookup for the OPEX assumption.

The Vigilent database has no per-DC OPEX figure (no public source publishes it
at facility level). Instead we classify the *operator* into one of three
industry-standard classes and assign the energy share of OPEX from
operator-class benchmarks (Uptime Institute / JLL data center cost surveys).

Tiers and their energy_pct_opex defaults:
    HYPERSCALE     0.32  — cloud / SaaS operators running their own DCs
    WHOLESALE_COLO 0.40  — public REIT-style and large regional wholesalers
    ENTERPRISE     0.48  — single-site, retail colo, regional ISPs

Unmatched operators fall back to WHOLESALE_COLO (industry midline).
"""

from typing import Optional

TIER_OPEX_PCT = {
    "HYPERSCALE":     0.32,
    "WHOLESALE_COLO": 0.40,
    "ENTERPRISE":     0.48,
}

DEFAULT_TIER = "WHOLESALE_COLO"

_OPERATOR_TIERS = {
    # ── Hyperscale ────────────────────────────────────────────────────────
    "microsoft":               "HYPERSCALE",
    "amazon web services":     "HYPERSCALE",
    "aws":                     "HYPERSCALE",
    "google":                  "HYPERSCALE",
    "meta":                    "HYPERSCALE",
    "facebook":                "HYPERSCALE",
    "oracle":                  "HYPERSCALE",
    "apple":                   "HYPERSCALE",

    # ── Wholesale / large colocation ──────────────────────────────────────
    "digital realty":          "WHOLESALE_COLO",
    "equinix":                 "WHOLESALE_COLO",
    "iron mountain":           "WHOLESALE_COLO",
    "ntt data":                "WHOLESALE_COLO",
    "ntt":                     "WHOLESALE_COLO",
    "st telemedia global":     "WHOLESALE_COLO",
    "st telemedia":            "WHOLESALE_COLO",
    "cologix":                 "WHOLESALE_COLO",
    "ascenty":                 "WHOLESALE_COLO",
    "cyrusone":                "WHOLESALE_COLO",
    "databank":                "WHOLESALE_COLO",
    "vantage":                 "WHOLESALE_COLO",
    "princeton digital group": "WHOLESALE_COLO",
    "qts":                     "WHOLESALE_COLO",
    "stack infrastructure":    "WHOLESALE_COLO",
    "switch":                  "WHOLESALE_COLO",
    "aligned":                 "WHOLESALE_COLO",
    "compass":                 "WHOLESALE_COLO",
    "edgeconnex":              "WHOLESALE_COLO",
    "t5":                      "WHOLESALE_COLO",
    "telehouse":               "WHOLESALE_COLO",
    "kddi":                    "WHOLESALE_COLO",
    "keppel":                  "WHOLESALE_COLO",
    "itenos":                  "WHOLESALE_COLO",
    "virtus":                  "WHOLESALE_COLO",
    "ark":                     "WHOLESALE_COLO",
    "yotta":                   "WHOLESALE_COLO",
    "sify":                    "WHOLESALE_COLO",
    "nxtra":                   "WHOLESALE_COLO",
    "ctrls bangalore dc1":     "WHOLESALE_COLO",
    "ctrls":                   "WHOLESALE_COLO",
    "l&t vyoma":               "WHOLESALE_COLO",
    "scala":                   "WHOLESALE_COLO",
    "ada infrastructure":      "WHOLESALE_COLO",
    "damac digital":           "WHOLESALE_COLO",
    "atnorth":                 "WHOLESALE_COLO",
    "qscale":                  "WHOLESALE_COLO",
    "nextstream":              "WHOLESALE_COLO",
    "templus":                 "WHOLESALE_COLO",

    # ── Enterprise / regional / legacy ────────────────────────────────────
    "conapto":                 "ENTERPRISE",
    "datum":                   "ENTERPRISE",
    "nt data center":          "ENTERPRISE",
    "baltic broadband limited":"ENTERPRISE",
    "epsilon":                 "ENTERPRISE",
    "nxera":                   "ENTERPRISE",
    "nlighten":                "ENTERPRISE",
    "ans":                     "ENTERPRISE",
    "apto":                    "ENTERPRISE",
    "bce":                     "ENTERPRISE",
    "bell aliant":             "ENTERPRISE",
    "c spire":                 "ENTERPRISE",
    "capital land":            "ENTERPRISE",
    "cyfuture":                "ENTERPRISE",
    "echelon":                 "ENTERPRISE",
    "evolution data centers":  "ENTERPRISE",
    "flexential":              "ENTERPRISE",
    "fortress":                "ENTERPRISE",
    "gotspace":                "ENTERPRISE",
    "hostdime":                "ENTERPRISE",
    "idx":                     "ENTERPRISE",
    "ldex":                    "ENTERPRISE",
    "proen":                   "ENTERPRISE",
    "purecolo":                "ENTERPRISE",
    "qu data centres":         "ENTERPRISE",
    "sabey":                   "ENTERPRISE",
    "servecentric":            "ENTERPRISE",
    "trg":                     "ENTERPRISE",
    "us signal":               "ENTERPRISE",
    "urbacon":                 "ENTERPRISE",
    "thésée":                  "ENTERPRISE",
    "thesee":                  "ENTERPRISE",
    "vaultica":                "ENTERPRISE",
}


def _normalize(name: str) -> str:
    """Lowercase, strip, drop trailing 'data centers/centres/datacenters' etc."""
    if not name:
        return ""
    n = str(name).strip().lower()
    for suffix in (" data centers", " data centres", " datacenters",
                   " datacentres", " data center", " data centre",
                   " data centers & it services", " corp"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
            break
    return n


def tier_for_operator(operator: Optional[str]) -> str:
    """Return tier name for an operator. Falls back to WHOLESALE_COLO."""
    n = _normalize(operator or "")
    if not n:
        return DEFAULT_TIER
    if n in _OPERATOR_TIERS:
        return _OPERATOR_TIERS[n]
    # Substring match (handles "NTT Data Center Singapore", "Digital Realty Inc", etc.)
    for key, tier in _OPERATOR_TIERS.items():
        if key in n:
            return tier
    return DEFAULT_TIER


def opex_pct_for_operator(operator: Optional[str]) -> float:
    """Return energy_pct_opex (fraction) for an operator."""
    return TIER_OPEX_PCT[tier_for_operator(operator)]


if __name__ == "__main__":
    samples = [
        "Digital Realty", "Equinix", "Microsoft", "Iron Mountain",
        "NTT Data", "ST Telemedia Global Data Centres", "ST Telemedia",
        "Sabey Data Centers", "Sabey", "nLighten", "nlighten",
        "C Spire", "TRG Datacenters", None, "", "Some New Op",
    ]
    for s in samples:
        t = tier_for_operator(s)
        o = opex_pct_for_operator(s)
        print(f"  {str(s):<40} -> {t:<15} ({o:.0%})")
