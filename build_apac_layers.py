"""
Build Australia + Southeast Asia choropleth GeoJSON layers
==========================================================

Same recipe as build_india_layers.py, applied to:
    Australia, Singapore, Thailand, Vietnam, Malaysia, Indonesia, Philippines

Per-country power & water tariffs are encoded as lookup tables below.
Sources are cited per row. Local-currency values are converted to
¢/kWh (electricity) and $/1000 gal (water) at FX rates locked
2026-04-27 so client-facing numbers don't drift.
"""

import json
import re
from pathlib import Path

ROOT = Path("/Users/adamtang/Desktop/Vigilent")
DATA_DIR = ROOT / "data"
NE_PATH = DATA_DIR / "raw" / "ne_10m_admin_1_states_provinces.geojson"

GAL_PER_M3 = 264.172  # 1 m³ = 264.172 US gallons

# FX snapshot 2026-04-27 (rounded to clean numbers)
FX = {
    "AUD": 0.66,    # 1 AUD = 0.66 USD
    "SGD": 0.74,
    "THB": 0.029,
    "VND": 0.0000395,
    "MYR": 0.21,
    "IDR": 0.000063,
    "PHP": 0.0175,
}

def kwh_to_cents(local_per_kwh, currency):
    """Convert local-currency per-kWh price to US cents per kWh."""
    return round(local_per_kwh * FX[currency] * 100, 2)

def m3_to_per_1000gal(local_per_m3, currency):
    """Convert local-currency per-m³ price to USD per 1000 US gallons."""
    return round(local_per_m3 * FX[currency] * 1000 / GAL_PER_M3, 2)


# ─────────────────────────────────────────────────────────────────────────────
# COUNTRY DATA TABLES
# Structure per country:
#   ne_admin: name in Natural Earth `admin` field
#   name_keys: list of NE property keys to try for the polygon name
#   regulations: national regulations text (applied to every polygon)
#   power: dict of zone → (local_per_kwh, currency, source)
#   water: dict of zone → (local_per_m3, currency, source) [None means N/A]
#   province_zone: dict mapping NE polygon name → zone key for power & water
#                  (any unmapped polygon falls back to "default")
# ─────────────────────────────────────────────────────────────────────────────

COUNTRIES = {
    # ───────── AUSTRALIA ─────────
    # AEMC State of the Energy Market 2024 small-medium commercial tariffs;
    # state water authorities published 2024-25 commercial rates.
    "Australia": {
        "ne_admin": "Australia",
        "name_keys": ["name", "name_en"],
        "regulations": "\n".join([
            "NABERS Energy & Water rating",
            "NCC Section J (energy efficiency)",
            "Climate Active certification",
            "National Greenhouse and Energy Reporting Act",
            "Renewable Energy Target",
        ]),
        "power": {
            "New South Wales":       (0.32, "AUD", "AEMC SME 2024 (NSW DNSP avg)"),
            "Victoria":              (0.28, "AUD", "AEMC SME 2024 (VIC)"),
            "Queensland":            (0.28, "AUD", "AEMC SME 2024 (QLD Energex avg)"),
            "South Australia":       (0.38, "AUD", "AEMC SME 2024 (SA)"),
            "Western Australia":     (0.30, "AUD", "Synergy A2 SME 2024"),
            "Tasmania":              (0.26, "AUD", "Aurora Tariff 31 SME 2024"),
            "Northern Territory":    (0.36, "AUD", "Jacana Energy SME 2024"),
            "Australian Capital Territory": (0.25, "AUD", "Evoenergy SME 2024"),
        },
        "water": {
            "New South Wales":       (4.50, "AUD", "Sydney Water non-residential 2024"),
            "Victoria":              (3.80, "AUD", "Yarra Valley Water non-res 2024"),
            "Queensland":            (4.20, "AUD", "Urban Utilities (Brisbane) non-res 2024"),
            "South Australia":       (3.85, "AUD", "SA Water non-res 2024"),
            "Western Australia":     (3.50, "AUD", "Water Corp non-res 2024"),
            "Tasmania":              (3.20, "AUD", "TasWater non-res 2024"),
            "Northern Territory":    (1.80, "AUD", "Power and Water non-res 2024"),
            "Australian Capital Territory": (5.20, "AUD", "Icon Water non-res 2024"),
        },
        "province_zone": {
            # NE name → state name (1:1 for major states)
            "New South Wales":       "New South Wales",
            "Victoria":              "Victoria",
            "Queensland":            "Queensland",
            "South Australia":       "South Australia",
            "Western Australia":     "Western Australia",
            "Tasmania":              "Tasmania",
            "Northern Territory":    "Northern Territory",
            "Australian Capital Territory": "Australian Capital Territory",
            # Small territories — treat as nearest-state
            "Jervis Bay Territory":  "New South Wales",
            "Macquarie Island":      "Tasmania",
            "Lord Howe Island":      "New South Wales",
        },
    },

    # ───────── SINGAPORE ─────────
    # Singapore is one country with one tariff zone — applied to all 5
    # community-development-council polygons in NE.
    "Singapore": {
        "ne_admin": "Singapore",
        "name_keys": ["name", "name_en"],
        "regulations": "\n".join([
            "BCA Green Mark for Data Centres",
            "EMA Energy Conservation Act",
            "PUB Water Efficiency requirements",
            "Singapore Standard SS 564 (Green DC)",
            "MAS Technology Risk Management (DC ops)",
        ]),
        "power": {
            "default": (0.30, "SGD", "EMA non-residential tariff Q1 2025"),
        },
        "water": {
            "default": (2.74, "SGD", "PUB Tier-1 non-domestic 2024"),
        },
        "province_zone": {},  # all polygons fall through to "default"
    },

    # ───────── THAILAND ─────────
    # MEA serves Bangkok, Nonthaburi, Samut Prakan; PEA serves the rest.
    # Both publish nearly identical commercial tariffs but water differs significantly.
    "Thailand": {
        "ne_admin": "Thailand",
        "name_keys": ["name", "name_en"],
        "regulations": "\n".join([
            "ENCON Act energy efficiency requirements",
            "BOI green DC investment incentives",
            "TIEB Power Development Plan",
            "DEDE energy management standards",
        ]),
        # Power zones unchanged (MEA = Bangkok metro / PEA = rest); commercial
        # tariffs near-identical between authorities so 2-zone is sufficient.
        "power": {
            "MEA": (3.97, "THB", "MEA medium-business TOU 2024 avg"),
            "PEA": (3.99, "THB", "PEA medium-business TOU 2024 avg"),
        },
        # Water zones expanded 2026-04-27 after pwa.co.th tariff schedule audit:
        # PWA publishes THREE separate Type-2 (large-business) tables, not one.
        #   MWA          — Bangkok metro (BKK/Nonthaburi/Samut Prakan)
        #   PWA Table 1  — commercialised major branches (Pathum Thani, Samut
        #                  Sakhon, Nakhon Pathom, Rayong concession area)
        #   PWA Table 2  — high-cost tourist islands (Phuket; Surat Thani's
        #                  Ko Samui / Ko Phangan districts)
        #   PWA Table 3  — default (majority of 74 provinces)
        # Source: pwa.co.th/contents/service/table-price ; mwa.co.th tariff page.
        "water": {
            "MWA":         (14.84, "THB", "MWA Type-2 commercial 101-120 m³ (Bangkok metro)"),
            "PWA_TABLE1":  (27.70, "THB", "PWA Table-1 large-business 101+ m³ (commercialised branches)"),
            "PWA_TABLE2":  (30.25, "THB", "PWA Table-2 large-business 101+ m³ (Phuket / Samui / Phangan islands)"),
            "PWA_TABLE3":  (21.70, "THB", "PWA Table-3 large-business 101-300 m³ (default — most provinces)"),
        },
        # Province → zone mapping. Power uses old MEA/PEA keys; water uses the
        # expanded MWA / PWA_TABLE1-3 keys. We need separate maps per metric.
        "province_zone": {
            "Bangkok Metropolis": "MEA",
            "Nonthaburi":         "MEA",
            "Samut Prakan":       "MEA",
        },
        "default_zone": "PEA",
        # Per-metric zone override (water-only, since power is the simple split)
        "water_zone": {
            "Bangkok Metropolis": "MWA",
            "Nonthaburi":         "MWA",
            "Samut Prakan":       "MWA",
            "Pathum Thani":       "PWA_TABLE1",
            "Samut Sakhon":       "PWA_TABLE1",
            "Nakhon Pathom":      "PWA_TABLE1",
            "Rayong":             "PWA_TABLE1",
            "Phuket":             "PWA_TABLE2",
            "Surat Thani":        "PWA_TABLE2",  # Samui/Phangan districts
        },
        "water_default_zone":     "PWA_TABLE3",
    },

    # ───────── VIETNAM ─────────
    # EVN sets a single national commercial/industrial tariff (Decision 2606/QĐ-BCT 2024).
    # Water tariffs vary by city; we use a Hanoi/HCMC blended midpoint as the proxy
    # since province-level commercial water data is largely unpublished.
    "Vietnam": {
        "ne_admin": "Vietnam",
        "name_keys": ["name", "name_en"],
        "regulations": "\n".join([
            "Law on Energy Saving and Efficiency (50/2010/QH12)",
            "Vietnam Building Energy Efficiency Code",
            "MOIT Renewable Energy Mechanism",
            "MONRE Water Resources Law",
        ]),
        "power": {
            "default": (2236.0, "VND", "EVN Decision 2606/QĐ-BCT 2024 commercial avg"),
        },
        "water": {
            "default": (25000.0, "VND", "HAWACOM/SAWACO blended commercial 2024 (proxy)"),
        },
        "province_zone": {},
    },

    # ───────── MALAYSIA ─────────
    # Three utility regions: TNB (Peninsula), SESB (Sabah), SESCO (Sarawak).
    "Malaysia": {
        "ne_admin": "Malaysia",
        "name_keys": ["name", "name_en"],
        "regulations": "\n".join([
            "Malaysia Standard MS 1525 (Energy efficiency)",
            "Energy Commission DC efficiency guidelines",
            "MyHIJAU green DC procurement",
            "Sustainable Energy Development Authority RE incentives",
        ]),
        "power": {
            "TNB":   (0.50, "MYR", "TNB Tariff B (commercial) 2024"),
            "SESB":  (0.45, "MYR", "SESB commercial 2024 avg"),
            "SESCO": (0.35, "MYR", "Sarawak Energy commercial 2024"),
        },
        "water": {
            "TNB":   (1.85, "MYR", "Air Selangor / state water companies 2024 avg"),
            "SESB":  (1.30, "MYR", "Jabatan Air Negeri Sabah commercial 2024"),
            "SESCO": (1.25, "MYR", "Kuching Water Board commercial 2024"),
        },
        "province_zone": {
            "Sabah":        "SESB",
            "Sarawak":      "SESCO",
            "Labuan":       "SESB",
            # All Peninsula states default to TNB
        },
        "default_zone": "TNB",
    },

    # ───────── INDONESIA ─────────
    # PLN sets a single national B-3 commercial tariff. Water tariffs are
    # municipal — too granular for province-level — so we apply Jakarta as
    # a national proxy (standard practice in Indonesia DC market reports).
    "Indonesia": {
        "ne_admin": "Indonesia",
        "name_keys": ["name", "name_en"],
        "regulations": "\n".join([
            "Government Regulation 70/2009 (Energy conservation)",
            "PerMen ESDM 14/2012 (energy management)",
            "Green Building Council Indonesia Greenship",
            "MEMR DC efficiency guideline",
        ]),
        "power": {
            "default": (1444.0, "IDR", "PLN B-3 commercial 2024 effective tariff"),
        },
        "water": {
            "default": (7500.0, "IDR", "PAM Jaya Jakarta commercial 2024 (national proxy)"),
        },
        "province_zone": {},
    },

    # ───────── PHILIPPINES ─────────
    # 4 utility zones: Meralco (NCR + nearby), Luzon ECs, Visayas, Mindanao.
    # Water: Maynilad/Manila Water for NCR, LWUA Class A for elsewhere.
    "Philippines": {
        "ne_admin": "Philippines",
        "name_keys": ["name", "name_en"],
        "regulations": "\n".join([
            "Energy Efficiency and Conservation Act (RA 11285)",
            "DOE Philippine Green Building Code",
            "ERC DC tariff transparency rules",
            "DICT National Broadband / DC strategy",
        ]),
        "power": {
            "Meralco":  (11.50, "PHP", "Meralco GP commercial Q1 2025"),
            "Luzon":    (9.50,  "PHP", "Other Luzon EC 2024 avg"),
            "Visayas":  (12.00, "PHP", "VECO/various ECs 2024 avg"),
            "Mindanao": (8.50,  "PHP", "Davao Light/various ECs 2024 avg"),
        },
        "water": {
            "Meralco":  (38.0, "PHP", "Maynilad/Manila Water commercial 2024"),
            "Luzon":    (30.0, "PHP", "LWUA Class A commercial avg"),
            "Visayas":  (32.0, "PHP", "MCWD/various LWUA 2024 avg"),
            "Mindanao": (28.0, "PHP", "DCWD/various LWUA 2024 avg"),
        },
        "default_zone": "Luzon",
        # Province-zone classification by island-region. (NE polygons in PH are
        # provinces + chartered cities; classification by location.)
        "province_zone": {
            # Meralco franchise area
            "Metropolitan Manila":  "Meralco", "Manila": "Meralco",
            "Quezon City": "Meralco", "Caloocan": "Meralco", "Pasig": "Meralco",
            "Makati": "Meralco", "Taguig": "Meralco", "Rizal": "Meralco",
            "Bulacan": "Meralco", "Cavite": "Meralco", "Laguna": "Meralco",
            "Batangas": "Meralco", "Pampanga": "Meralco",
            # Luzon (everything else north of Visayas)
            "Ilocos Norte": "Luzon", "Ilocos Sur": "Luzon", "La Union": "Luzon",
            "Pangasinan": "Luzon", "Cagayan": "Luzon", "Isabela": "Luzon",
            "Nueva Vizcaya": "Luzon", "Quirino": "Luzon", "Apayao": "Luzon",
            "Abra": "Luzon", "Benguet": "Luzon", "Ifugao": "Luzon",
            "Kalinga": "Luzon", "Mountain Province": "Luzon", "Aurora": "Luzon",
            "Zambales": "Luzon", "Bataan": "Luzon", "Tarlac": "Luzon",
            "Nueva Ecija": "Luzon", "Quezon": "Luzon", "Marinduque": "Luzon",
            "Mindoro Occidental": "Luzon", "Mindoro Oriental": "Luzon",
            "Romblon": "Luzon", "Palawan": "Luzon", "Camarines Norte": "Luzon",
            "Camarines Sur": "Luzon", "Catanduanes": "Luzon", "Albay": "Luzon",
            "Sorsogon": "Luzon", "Masbate": "Luzon",
            # Visayas
            "Aklan": "Visayas", "Antique": "Visayas", "Capiz": "Visayas",
            "Iloilo": "Visayas", "Guimaras": "Visayas",
            "Negros Occidental": "Visayas", "Negros Oriental": "Visayas",
            "Siquijor": "Visayas", "Cebu": "Visayas", "Bohol": "Visayas",
            "Leyte": "Visayas", "Southern Leyte": "Visayas",
            "Biliran": "Visayas", "Samar": "Visayas",
            "Eastern Samar": "Visayas", "Northern Samar": "Visayas",
            # Mindanao
            "Zamboanga del Norte": "Mindanao", "Zamboanga del Sur": "Mindanao",
            "Zamboanga Sibugay":   "Mindanao", "Misamis Occidental": "Mindanao",
            "Misamis Oriental":    "Mindanao", "Bukidnon": "Mindanao",
            "Camiguin": "Mindanao", "Lanao del Norte": "Mindanao",
            "Lanao del Sur": "Mindanao", "Davao del Norte": "Mindanao",
            "Davao del Sur": "Mindanao", "Davao Oriental": "Mindanao",
            "Davao Occidental": "Mindanao", "Davao de Oro": "Mindanao",
            "Compostela Valley": "Mindanao", "South Cotabato": "Mindanao",
            "North Cotabato": "Mindanao", "Cotabato": "Mindanao",
            "Sultan Kudarat": "Mindanao", "Sarangani": "Mindanao",
            "Maguindanao": "Mindanao", "Basilan": "Mindanao",
            "Sulu": "Mindanao", "Tawi-Tawi": "Mindanao",
            "Surigao del Norte": "Mindanao", "Surigao del Sur": "Mindanao",
            "Agusan del Norte": "Mindanao", "Agusan del Sur": "Mindanao",
            "Dinagat Islands":   "Mindanao",
        },
    },
}

# Per-country shortcut for naming layer files & property keys.
COUNTRY_FILE_INDEX = {
    "Australia":   30,
    "Singapore":   31,
    "Thailand":    32,
    "Vietnam":     33,
    "Malaysia":    34,
    "Indonesia":   35,
    "Philippines": 36,
}

COMPOSITE_WEIGHTS = {
    "electricity":  0.40,
    "water":        0.20,
    "dc_density":   0.20,
    "regulatory":   0.20,
}


# ─────────────────────────────────────────────────────────────────────────────
# PER-POLYGON WATER OVERRIDES
# Per-province / per-state commercial water tariffs that take precedence over
# the country-level zone defaults in COUNTRIES[].water. Researched 2026-04-27
# from utility websites & SPAN/MWSS/MoF regulator filings (2024-2025 schedules).
# Names match Natural Earth `name` field (some require aliases — see below).
# ─────────────────────────────────────────────────────────────────────────────

# NE-name aliases used by water override lookup (agent-supplied name → NE name)
NE_NAME_ALIASES = {
    "Hồ Chí Minh":          "Hồ Chí Minh city",
    "Metropolitan Manila":  "Manila",  # NE has name='Manila', name_en='Metro Manila'
}

WATER_OVERRIDES = {
    # ─── Malaysia (16 / 16) — SPAN-regulated, post-Feb-2024 nationwide revision ───
    "Malaysia": {
        "Johor":           {"local_per_m3": 3.30, "currency": "MYR", "source": "SAJ Ranhill 2024 commercial >35 m³ slab"},
        "Kedah":           {"local_per_m3": 2.60, "currency": "MYR", "source": "SADA 2024 commercial >20 m³ slab"},
        "Kelantan":        {"local_per_m3": 1.65, "currency": "MYR", "source": "AKSB 2024 trade tariff"},
        "Melaka":          {"local_per_m3": 1.78, "currency": "MYR", "source": "SAMB Air Melaka 2024 commercial >35 m³"},
        "Negeri Sembilan": {"local_per_m3": 2.42, "currency": "MYR", "source": "SAINS 2024 commercial slab"},
        "Pahang":          {"local_per_m3": 2.07, "currency": "MYR", "source": "PAIP 2024 commercial >35 m³"},
        "Pulau Pinang":    {"local_per_m3": 1.65, "currency": "MYR", "source": "PBA Holdings 2024 trade tariff (NE name: Pulau Pinang)"},
        "Perak":           {"local_per_m3": 2.30, "currency": "MYR", "source": "LAP 2024 commercial slab"},
        "Perlis":          {"local_per_m3": 1.30, "currency": "MYR", "source": "Perlis Water Dept 2024 trade rate"},
        "Sabah":           {"local_per_m3": 1.40, "currency": "MYR", "source": "JANS Sabah Water Dept 2024 commercial"},
        "Sarawak":         {"local_per_m3": 1.92, "currency": "MYR", "source": "Kuching Water Board 2024 commercial >50 m³"},
        "Selangor":        {"local_per_m3": 3.51, "currency": "MYR", "source": "Air Selangor SPAN-approved post-Feb-2024 trade >35 m³ (+RM0.57)"},
        "Terengganu":      {"local_per_m3": 1.42, "currency": "MYR", "source": "SATU 2024 commercial slab"},
        "Kuala Lumpur":    {"local_per_m3": 3.51, "currency": "MYR", "source": "Air Selangor (KL/FT) post-Feb-2024 trade rate"},
        "Putrajaya":       {"local_per_m3": 3.51, "currency": "MYR", "source": "Air Selangor (Putrajaya) post-Feb-2024 trade rate"},
        "Labuan":          {"local_per_m3": 1.45, "currency": "MYR", "source": "Labuan Water Dept 2024 commercial"},
    },
    # ─── Indonesia (31 / 33) — Provincial PDAM commercial K3 slab via capital city ───
    "Indonesia": {
        "Aceh":               {"local_per_m3":  8500, "currency": "IDR", "source": "PDAM Tirta Daroy Banda Aceh 2024 niaga K3"},
        "Sumatera Utara":     {"local_per_m3": 11000, "currency": "IDR", "source": "PDAM Tirtanadi Medan 2024 niaga besar"},
        "Sumatera Barat":     {"local_per_m3":  9200, "currency": "IDR", "source": "Perumda AM Padang 2024 niaga >20 m³"},
        "Riau":               {"local_per_m3": 12500, "currency": "IDR", "source": "PDAM Tirta Siak Pekanbaru 2024 niaga"},
        "Kepulauan Riau":     {"local_per_m3": 13800, "currency": "IDR", "source": "ATB Batam 2024 commercial slab"},
        "Jambi":              {"local_per_m3":  9500, "currency": "IDR", "source": "PDAM Tirta Mayang Jambi 2024 niaga"},
        "Sumatera Selatan":   {"local_per_m3": 10200, "currency": "IDR", "source": "PDAM Tirta Musi Palembang 2024 niaga"},
        "Lampung":            {"local_per_m3":  9800, "currency": "IDR", "source": "PDAM Way Rilau Bandar Lampung 2024"},
        "Bangka-Belitung":    {"local_per_m3": 11500, "currency": "IDR", "source": "PDAM Tirta Bangka 2024 niaga"},
        "Bengkulu":           {"local_per_m3":  8700, "currency": "IDR", "source": "PDAM Tirta Dharma Bengkulu 2024"},
        "Banten":             {"local_per_m3": 11800, "currency": "IDR", "source": "PDAM Tirta Albantani Serang 2024 K3"},
        "Jakarta Raya":       {"local_per_m3": 12550, "currency": "IDR", "source": "PAM Jaya 2024 K3/K4 commercial"},
        "Jawa Barat":         {"local_per_m3": 10400, "currency": "IDR", "source": "PDAM Tirtawening Bandung 2024 niaga"},
        "Jawa Tengah":        {"local_per_m3":  9400, "currency": "IDR", "source": "PDAM Tirta Moedal Semarang 2024 niaga"},
        "Yogyakarta":         {"local_per_m3":  8900, "currency": "IDR", "source": "PDAM Tirtamarta Yogyakarta 2024 niaga"},
        "Jawa Timur":         {"local_per_m3": 11200, "currency": "IDR", "source": "PDAM Surya Sembada Surabaya 2024 K3"},
        "Bali":               {"local_per_m3": 13500, "currency": "IDR", "source": "Perumda AM Tirta Sewakadarma Denpasar 2024"},
        "Nusa Tenggara Barat":{"local_per_m3":  9600, "currency": "IDR", "source": "PDAM Giri Menang Mataram 2024 niaga"},
        "Nusa Tenggara Timur":{"local_per_m3":  8800, "currency": "IDR", "source": "PDAM Kupang 2024 niaga slab"},
        "Kalimantan Barat":   {"local_per_m3": 10800, "currency": "IDR", "source": "PDAM Tirta Khatulistiwa Pontianak 2024"},
        "Kalimantan Tengah":  {"local_per_m3": 10500, "currency": "IDR", "source": "PDAM Palangkaraya 2024 niaga"},
        "Kalimantan Selatan": {"local_per_m3": 10100, "currency": "IDR", "source": "PDAM Bandarmasih Banjarmasin 2024"},
        "Kalimantan Timur":   {"local_per_m3": 12200, "currency": "IDR", "source": "PDAM Samarinda/Balikpapan 2024 niaga"},
        "Sulawesi Utara":     {"local_per_m3": 10700, "currency": "IDR", "source": "PT Air Manado 2024 niaga"},
        "Sulawesi Tengah":    {"local_per_m3":  9800, "currency": "IDR", "source": "PDAM Palu 2024 niaga slab"},
        "Sulawesi Selatan":   {"local_per_m3": 10900, "currency": "IDR", "source": "PDAM Makassar 2024 niaga K3"},
        "Sulawesi Tenggara":  {"local_per_m3":  9700, "currency": "IDR", "source": "PDAM Tirta Anoa Kendari 2024"},
        "Gorontalo":          {"local_per_m3":  9300, "currency": "IDR", "source": "PDAM Gorontalo 2024 niaga"},
        "Maluku":             {"local_per_m3": 10400, "currency": "IDR", "source": "PDAM Ambon 2024 niaga"},
        "Papua":              {"local_per_m3": 14500, "currency": "IDR", "source": "PDAM Jayapura 2024 niaga (high-cost)"},
        # Maluku Utara, Papua Barat → no override; fall back to country default
    },
    # ─── Vietnam (12 provinces with verified commercial schedules) ───
    "Vietnam": {
        "Ha Noi":            {"local_per_m3": 29000, "currency": "VND", "source": "HAWACOM 2024 commercial (Decision 3541/QD-UBND eff. 2024)"},
        "Hồ Chí Minh city":  {"local_per_m3": 22300, "currency": "VND", "source": "SAWACO 2024 sản xuất dịch vụ"},
        "Đà Nẵng":           {"local_per_m3": 23500, "currency": "VND", "source": "DAWACO Da Nang 2024 commercial"},
        "Hải Phòng":         {"local_per_m3": 18900, "currency": "VND", "source": "Hai Phong Water 2024 commercial slab"},
        "Can Tho":           {"local_per_m3": 17200, "currency": "VND", "source": "Can Tho Water Supply 2024 commercial"},
        "Bắc Ninh":          {"local_per_m3": 19500, "currency": "VND", "source": "Bac Ninh Water 2024 industrial-commercial"},
        "Bình Dương":        {"local_per_m3": 21800, "currency": "VND", "source": "BIWASE Binh Duong 2024 commercial"},
        # "Đồng Nai" not a standalone NE polygon (rolled into Đông Nam Bộ region)
        "Đông Nam Bộ":       {"local_per_m3": 20400, "currency": "VND", "source": "DOWACO Dong Nai 2024 (proxy for Đông Nam Bộ region)"},
        "Vĩnh Phúc":         {"local_per_m3": 18500, "currency": "VND", "source": "Vinh Phuc Water 2024 commercial"},
        "Quảng Ninh":        {"local_per_m3": 19800, "currency": "VND", "source": "QUAWACO Quang Ninh 2024 commercial"},
        "Bà Rịa - Vũng Tàu": {"local_per_m3": 21200, "currency": "VND", "source": "BWACO 2024 commercial"},
        "Long An":           {"local_per_m3": 17800, "currency": "VND", "source": "Long An Water 2024 commercial"},
        # Added 2026-04-27 — published provincial PC decisions / utility schedules
        "Hải Dương":         {"local_per_m3": 21300, "currency": "VND", "source": "Hai Duong Clean Water JSC 2024 'kinh doanh dịch vụ' (Decision 542/QĐ-UBND framework)"},
        "Bắc Giang":         {"local_per_m3": 25095, "currency": "VND", "source": "Bac Giang Clean Water JSC 2024 rural commercial (incl. 5% VAT)"},
        "Lâm Đồng":          {"local_per_m3": 20984, "currency": "VND", "source": "Decision 04/2024/QĐ-UBND Lâm Đồng — kinh doanh dịch vụ"},
        "Đắk Lắk":           {"local_per_m3": 17140, "currency": "VND", "source": "Buon Ma Thuot 2024 'kinh doanh dịch vụ' (baodaklak.vn schedule)"},
        "Quàng Nam":         {"local_per_m3": 18959, "currency": "VND", "source": "Decision 2473/QĐ-UBND Quảng Nam phase-3 (Oct-2024) service-business rate"},
        "Quảng Ngãi":        {"local_per_m3": 20431, "currency": "VND", "source": "Quang Ngai Water Supply & Construction JSC 2024 service-business (excl. VAT)"},
        "Ninh Thuận":        {"local_per_m3":  9114, "currency": "VND", "source": "Decision 26/2023/QĐ-UBND Ninh Thuận — average tariff (commercial slab applied 2024)"},
        "Cà Mau":            {"local_per_m3":  8200, "currency": "VND", "source": "Decision 13/2023/QĐ-UBND Cà Mau — Ca Mau Water JSC 2024 commercial slab"},
    },
    # ─── Philippines (12 zones with verified commercial schedules) ───
    "Philippines": {
        "Manila":             {"local_per_m3": 52.0, "currency": "PHP", "source": "Maynilad/Manila Water 2024 commercial avg (Jan-2024 rebasing)"},
        "Rizal":              {"local_per_m3": 50.5, "currency": "PHP", "source": "Manila Water East Zone 2024 commercial"},
        "Cavite":             {"local_per_m3": 51.0, "currency": "PHP", "source": "Maynilad West Zone 2024 commercial"},
        "Cebu":               {"local_per_m3": 38.0, "currency": "PHP", "source": "Metro Cebu Water District 2024 commercial >50 m³"},
        "Davao del Sur":      {"local_per_m3": 32.5, "currency": "PHP", "source": "Davao City Water District 2024 commercial >50 m³"},
        "Iloilo":             {"local_per_m3": 36.0, "currency": "PHP", "source": "Iloilo Water District 2024 commercial"},
        "Benguet":            {"local_per_m3": 34.0, "currency": "PHP", "source": "Baguio Water District 2024 commercial"},
        "Pampanga":           {"local_per_m3": 33.5, "currency": "PHP", "source": "Clark Water/PrimeWater Pampanga 2024"},
        "Batangas":           {"local_per_m3": 35.0, "currency": "PHP", "source": "PrimeWater Batangas 2024 commercial"},
        "Negros Occidental":  {"local_per_m3": 33.0, "currency": "PHP", "source": "Bacolod City Water District 2024 commercial"},
        "Misamis Oriental":   {"local_per_m3": 31.5, "currency": "PHP", "source": "Cagayan de Oro Water District 2024"},
        "Zamboanga del Sur":  {"local_per_m3": 30.0, "currency": "PHP", "source": "Zamboanga City Water District 2024"},
        # Added 2026-04-27
        "Camarines Sur":      {"local_per_m3": 33.20, "currency": "PHP", "source": "Metropolitan Naga Water District (MNWD) Commercial 31-40 m³ (LWUA Bd Res 107 s.2005, in effect 2024)"},
        "Palawan":            {"local_per_m3": 35.00, "currency": "PHP", "source": "Puerto Princesa City Water District (PPCWD) Jan-2024 reinstated commercial mid-tier"},
    },
    # Thailand: existing MEA/PEA zone split is already province-aware via
    # COUNTRIES["Thailand"].province_zone, no per-polygon overrides needed.
}


# ─────────────────────────────────────────────────────────────────────────────

def load_ne_polygons():
    print(f"  Loading Natural Earth admin-1 cache ({NE_PATH.stat().st_size // 1024 // 1024} MB)...")
    with open(NE_PATH) as f:
        return json.load(f)["features"]


def filter_country(features, admin_name):
    out = []
    for feat in features:
        p = feat["properties"]
        if p.get("admin") == admin_name:
            out.append(feat)
    return out


def get_name(feat, name_keys):
    for k in name_keys:
        v = feat["properties"].get(k)
        if v:
            return str(v).strip()
    return None


def resolve_zone(name, country_cfg, kind):
    """Return (zone_key, value_dict) for a polygon.

    Lookup precedence:
      1. metric-specific province map (e.g. country_cfg['water_zone'])
      2. shared province_zone map
      3. table['default'] entry
      4. metric-specific default (e.g. 'water_default_zone')
      5. shared default_zone
    """
    table = country_cfg[kind]
    metric_map_key = kind + "_zone"          # 'water_zone' / 'power_zone'
    metric_default_key = kind + "_default_zone"

    if name in country_cfg.get(metric_map_key, {}):
        zone = country_cfg[metric_map_key][name]
        if zone in table:
            return zone, table[zone]
    if name in country_cfg.get("province_zone", {}):
        zone = country_cfg["province_zone"][name]
        if zone in table:
            return zone, table[zone]
    if "default" in table:
        return "default", table["default"]
    metric_default = country_cfg.get(metric_default_key)
    if metric_default and metric_default in table:
        return metric_default, table[metric_default]
    default_zone = country_cfg.get("default_zone")
    if default_zone and default_zone in table:
        return default_zone, table[default_zone]
    return None, None


def build_country(country, cfg, ne_features):
    print(f"\n[{country}]")
    polys = filter_country(ne_features, cfg["ne_admin"])
    print(f"  Polygons: {len(polys)}")
    if not polys:
        print(f"  WARNING: no polygons matched ne_admin={cfg['ne_admin']!r}")
        return None

    rows = []
    for feat in polys:
        name = get_name(feat, cfg["name_keys"])
        if not name:
            continue

        # Power
        p_zone, p_val = resolve_zone(name, cfg, "power")
        if p_val:
            cents = kwh_to_cents(p_val[0], p_val[1])
            p_source = p_val[2]
        else:
            cents = None
            p_source = "no zone match"

        # Water — per-polygon override takes precedence over zone default
        override = WATER_OVERRIDES.get(country, {}).get(name)
        if override:
            usd_per_1000gal = m3_to_per_1000gal(override["local_per_m3"], override["currency"])
            w_zone = "OVERRIDE"
            w_source = override["source"]
        else:
            w_zone, w_val = resolve_zone(name, cfg, "water")
            if w_val:
                usd_per_1000gal = m3_to_per_1000gal(w_val[0], w_val[1])
                w_source = w_val[2]
            else:
                usd_per_1000gal = None
                w_source = "no zone match"

        rows.append({
            "name": name,
            "geometry": feat["geometry"],
            "power_cents_per_kwh": cents,
            "power_zone": p_zone,
            "power_source": p_source,
            "water_usd_per_1000gal": usd_per_1000gal,
            "water_zone": w_zone,
            "water_source": w_source,
        })
    return rows


def write_layer(rows, country, idx, kind, value_field, prop_key, var_suffix):
    features = []
    for r in rows:
        v = r.get(value_field)
        props = {
            "NAME": r["name"],
            prop_key: v,  # may be None → popup shows N/A; gradient layer substitutes 0
        }
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": r["geometry"],
        })
    fname = f"{country}{var_suffix}_{idx}.js"
    var = f"json_{country}{var_suffix}_{idx}"
    body = json.dumps({"type": "FeatureCollection", "features": features},
                      separators=(",", ":"), ensure_ascii=False)
    (DATA_DIR / fname).write_text(f"var {var} = {body};", encoding="utf-8")
    print(f"    {fname:<50} {len(features):>3} features  ({kind})")


def write_regs_layer(rows, country, idx, regs_text, prop_key):
    features = []
    for r in rows:
        features.append({
            "type": "Feature",
            "properties": {
                "NAME": r["name"],
                prop_key: regs_text,
            },
            "geometry": r["geometry"],
        })
    fname = f"{country}RegulationsByQuantity_{idx}.js"
    var = f"json_{country}RegulationsByQuantity_{idx}"
    body = json.dumps({"type": "FeatureCollection", "features": features},
                      separators=(",", ":"), ensure_ascii=False)
    (DATA_DIR / fname).write_text(f"var {var} = {body};", encoding="utf-8")
    print(f"    {fname:<50} {len(features):>3} features  (regulations)")


def main():
    print("=" * 70)
    print("  AUSTRALIA + SOUTHEAST ASIA CHOROPLETH BUILDER")
    print("=" * 70)

    ne_features = load_ne_polygons()

    summary = []
    for country, cfg in COUNTRIES.items():
        idx = COUNTRY_FILE_INDEX[country]
        rows = build_country(country, cfg, ne_features)
        if rows is None:
            continue

        print(f"  Writing layers...")
        # Electricity layer
        write_layer(rows, country, idx, "electricity",
                    "power_cents_per_kwh",
                    f"{country} Statistics_Commercial Electricity Rate (¢/kWh)",
                    "CommercialElectricityRateskWh")
        # Water layer
        write_layer(rows, country, idx + 1, "water",
                    "water_usd_per_1000gal",
                    f"{country} Statistics_Commercial Water Rate ($/1000 gallons)",
                    "CommercialWaterRates1000gallons")
        # Regulations layer
        write_regs_layer(rows, country, idx + 2, cfg["regulations"],
                         f"{country} Statistics_Regulations")

        summary.append((country, len(rows),
                        sum(1 for r in rows if r['power_cents_per_kwh'] is not None),
                        sum(1 for r in rows if r['water_usd_per_1000gal'] is not None)))

    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  {'Country':<14} {'Polygons':>10} {'PowerData':>10} {'WaterData':>10}")
    for c, n, p, w in summary:
        print(f"  {c:<14} {n:>10} {p:>10} {w:>10}")
    print("\nNext: re-run build_global_layers.py to merge into global layers.")


if __name__ == "__main__":
    main()
