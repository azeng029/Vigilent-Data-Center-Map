"""
audit_coords.py
===============
Flags data centers whose (Latitude, Longitude) fall outside the bounding
box of their declared Country / State-Province. For each flagged row,
proposes a corrected coordinate using the CITY_COORDS lookup in
`import_full_database.py` (falling back to the region centroid).

Writes two artefacts:
  output/coord_audit_report.md   — markdown review table
  output/coord_audit_fixes.csv   — (Name, new_lat, new_lng) rows ready
                                   to apply with apply_coord_fixes.py

Nothing is applied to the master CSV here — this is read-only.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_full_database import CITY_COORDS

ROOT = Path(__file__).resolve().parent
CSV_PATH = ROOT / "Vigilent Data Center Database (US)(Sheet1).csv"
OUT_DIR = ROOT / "output"

# --- Country bounding boxes (lat_min, lat_max, lng_min, lng_max) ---
COUNTRY_BBOX = {
    "USA":             (24, 50, -125, -66),
    "Canada":          (41, 71, -141, -52),
    "Brazil":          (-34, 6, -74, -34),
    "United Kingdom":  (49, 61, -9, 2),
    "Germany":         (47, 55, 5, 15),
    "France":          (41, 51.5, -5, 10),
    "Ireland":         (51, 55.5, -11, -5),
    "Sweden":          (55, 70, 10, 25),
    "Norway":          (58, 72, 4, 31),
    "Finland":         (59, 71, 20, 32),
    "Netherlands":     (50, 54, 3, 8),
    "The Netherlands": (50, 54, 3, 8),
    "NEtherlands":     (50, 54, 3, 8),
    "Italy":           (35, 47.5, 6, 19),
    "Spain":           (35, 44, -10, 5),
    "Portugal":        (36, 43, -10, -6),
    "Japan":           (24, 46, 122, 150),
    "Australia":       (-45, -10, 112, 155),
    "India":           (6, 37, 67, 98),
    "Singapore":       (1.1, 1.5, 103.5, 104.1),
    "Thailand":        (5, 21, 97, 106),
    "Denmark":         (54, 58, 8, 16),
    "Belgium":         (49, 52, 2, 7),
    "Austria":         (46, 49, 9, 17),
    "Switzerland":     (45, 48, 5, 11),
    "Poland":          (49, 55, 14, 24),
}

# --- US state bounding boxes ---
US_STATE_BBOX = {
    "AL": (30.1, 35.1, -88.5, -84.9), "AK": (51, 71, -180, -129),
    "AZ": (31.3, 37.0, -114.9, -109.0), "AR": (33, 36.5, -94.6, -89.6),
    "CA": (32.5, 42.0, -124.5, -114.1), "CO": (36.9, 41.0, -109.1, -102.0),
    "CT": (40.9, 42.1, -73.8, -71.8), "DE": (38.4, 39.9, -75.8, -75.0),
    "FL": (24.4, 31.0, -87.7, -79.9), "GA": (30.3, 35.0, -85.6, -80.8),
    "HI": (18.9, 22.3, -160.3, -154.8), "ID": (41.9, 49.1, -117.3, -111.0),
    "IL": (36.9, 42.5, -91.6, -87.0), "IN": (37.8, 41.8, -88.1, -84.8),
    "IA": (40.4, 43.5, -96.7, -90.1), "KS": (36.9, 40.0, -102.1, -94.6),
    "KY": (36.5, 39.2, -89.6, -81.9), "LA": (28.9, 33.0, -94.1, -88.8),
    "ME": (43.0, 47.5, -71.1, -66.9), "MD": (37.9, 39.7, -79.5, -75.0),
    "MA": (41.2, 42.9, -73.5, -69.9), "MI": (41.7, 48.3, -90.4, -82.4),
    "MN": (43.5, 49.4, -97.3, -89.5), "MS": (30.1, 35.0, -91.7, -88.1),
    "MO": (35.9, 40.6, -95.8, -89.1), "MT": (44.3, 49.1, -116.1, -104.0),
    "NE": (40.0, 43.0, -104.1, -95.3), "NV": (35.0, 42.0, -120.0, -114.0),
    "NH": (42.6, 45.3, -72.6, -70.6), "NJ": (38.9, 41.4, -75.6, -73.9),
    "NM": (31.3, 37.0, -109.1, -103.0), "NY": (40.5, 45.1, -79.8, -71.8),
    "NC": (33.8, 36.6, -84.4, -75.4), "ND": (45.9, 49.1, -104.1, -96.5),
    "OH": (38.4, 42.0, -84.9, -80.5), "OK": (33.6, 37.1, -103.1, -94.4),
    "OR": (41.9, 46.3, -124.6, -116.5), "PA": (39.7, 42.3, -80.6, -74.7),
    "RI": (41.1, 42.1, -71.9, -71.1), "SC": (32.0, 35.3, -83.4, -78.5),
    "SD": (42.4, 46.0, -104.1, -96.4), "TN": (34.9, 36.7, -90.4, -81.6),
    "TX": (25.8, 36.6, -107.0, -93.5), "UT": (36.9, 42.1, -114.1, -108.9),
    "VT": (42.7, 45.1, -73.5, -71.4), "VA": (36.5, 39.5, -83.7, -75.1),
    "WA": (45.5, 49.1, -124.9, -116.9), "WV": (37.1, 40.7, -82.7, -77.6),
    "WI": (42.4, 47.1, -92.9, -86.2), "WY": (40.9, 45.1, -111.1, -104.0),
}

# --- Foreign state/region bounding boxes (key = (country, state)) ---
FOREIGN_STATE_BBOX = {
    ("Canada", "Ontario"):           (41.5, 57.0, -95.2, -74.3),
    ("Canada", "Quebec"):            (45.0, 63.0, -79.8, -57.1),
    ("Canada", "Alberta"):           (49.0, 60.0, -120.0, -110.0),
    ("Canada", "British Columbia"):  (48.3, 60.0, -139.1, -114.0),
    ("Canada", "Nova Scotia"):       (43.3, 47.0, -66.5, -59.7),
    ("Canada", "Saskatchewan"):      (49.0, 60.0, -110.0, -101.3),
    ("Germany", "Hesse"):            (49.4, 51.7, 7.8, 10.2),
    ("Japan", "Tokyo"):              (35.5, 36.0, 139.0, 140.0),
    ("Japan", "Chiba"):              (34.9, 36.3, 139.7, 141.0),
    ("Japan", "Saitama"):            (35.7, 36.3, 138.8, 140.0),
    ("Japan", "Osaka"):              (34.2, 35.1, 135.0, 136.0),
    ("Australia", "NSW"):            (-37.5, -28.2, 141.0, 154.0),
    ("India", "Maharashtra"):        (15.6, 22.0, 72.6, 80.9),
    ("India", "Tamil Nadu"):         (8.1, 13.6, 76.2, 80.3),
    ("India", "Telangana"):          (15.9, 19.9, 77.3, 81.8),
    ("India", "Karnataka"):          (11.5, 18.5, 74.1, 78.6),
    ("India", "Delhi"):              (28.4, 28.9, 76.8, 77.4),
    ("Brazil", "São Paulo"):         (-25.3, -19.8, -53.1, -44.2),
    ("Brazil", "Rio de Janeiro"):    (-23.4, -20.7, -44.9, -40.9),
    ("Brazil", "Tocantins"):         (-13.5, -5.2, -50.8, -45.7),
}


# --- Helpers ---

def parse_float(v):
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return None


def inside(lat, lng, bbox, pad=0.5):
    lo_lat, hi_lat, lo_lng, hi_lng = bbox
    return (lo_lat - pad) <= lat <= (hi_lat + pad) and \
           (lo_lng - pad) <= lng <= (hi_lng + pad)


def bbox_center(bbox):
    return ((bbox[0] + bbox[1]) / 2, (bbox[2] + bbox[3]) / 2)


def city_coord(city, state, country):
    """Try to resolve (city, state/country) via CITY_COORDS."""
    keys = [(city, state), (city, country), (city, None), (city, "N/A"), (city, "")]
    for k in keys:
        if k in CITY_COORDS:
            return CITY_COORDS[k]
    return None


def expected_bbox(row):
    country = (row.get("Country") or "").strip()
    state = (row.get("State/Province") or "").strip()
    if country == "USA" and state in US_STATE_BBOX:
        return ("state", state, US_STATE_BBOX[state])
    if (country, state) in FOREIGN_STATE_BBOX:
        return ("state", f"{country}/{state}", FOREIGN_STATE_BBOX[(country, state)])
    if country in COUNTRY_BBOX:
        return ("country", country, COUNTRY_BBOX[country])
    return (None, None, None)


def main():
    rows = list(csv.DictReader(CSV_PATH.open(newline="", encoding="utf-8")))

    flagged = []
    no_check = 0
    for row in rows:
        name = (row.get("Name") or "").strip()
        if not name:
            continue
        lat = parse_float(row.get("Latitude"))
        lng = parse_float(row.get("Longitude"))
        if lat is None or lng is None:
            flagged.append((row, "missing", None, None, None))
            continue
        kind, label, bbox = expected_bbox(row)
        if bbox is None:
            no_check += 1
            continue
        if not inside(lat, lng, bbox):
            flagged.append((row, kind, label, lat, lng))
            continue
        # Secondary check: inside region but far from the claimed city.
        # Flag if the great-circle distance to the known city centroid > 250 km.
        city = (row.get("City") or "").strip()
        state = (row.get("State/Province") or "").strip()
        country = (row.get("Country") or "").strip()
        cc = city_coord(city, state, country)
        if cc:
            from math import radians, sin, cos, asin, sqrt
            dlat = radians(cc[0] - lat); dlng = radians(cc[1] - lng)
            a = sin(dlat/2)**2 + cos(radians(lat))*cos(radians(cc[0]))*sin(dlng/2)**2
            km = 2 * 6371 * asin(sqrt(a))
            if km > 250:
                flagged.append((row, "city-drift", f"{city} ({km:.0f} km off)", lat, lng))

    # Propose fixes
    OUT_DIR.mkdir(exist_ok=True)
    fixes_path = OUT_DIR / "coord_audit_fixes.csv"
    report_path = OUT_DIR / "coord_audit_report.md"

    fix_rows = []
    with report_path.open("w") as rf:
        rf.write(f"# Coordinate audit — {len(flagged)} flagged DCs\n\n")
        rf.write(f"Checked {len(rows)} rows; {no_check} skipped (unknown region).\n\n")
        rf.write("| Name | Country | State | City | Current | Suggested | Source |\n")
        rf.write("|---|---|---|---|---|---|---|\n")
        for row, kind, label, cur_lat, cur_lng in flagged:
            name = row.get("Name", "").strip()
            country = row.get("Country", "").strip()
            state = row.get("State/Province", "").strip()
            city = row.get("City", "").strip()

            suggested = city_coord(city, state, country)
            source = "CITY_COORDS"
            if suggested is None:
                _, _, bbox = expected_bbox(row)
                if bbox:
                    suggested = bbox_center(bbox)
                    source = f"{label} centroid"
                else:
                    source = "UNRESOLVED"

            cur_str = f"({cur_lat}, {cur_lng})" if cur_lat is not None else "(none)"
            sug_str = f"({suggested[0]:.4f}, {suggested[1]:.4f})" if suggested else "—"
            rf.write(f"| {name} | {country} | {state} | {city} | {cur_str} | {sug_str} | {source} |\n")

            if suggested:
                fix_rows.append({
                    "Name": name, "Current_Lat": cur_lat, "Current_Lng": cur_lng,
                    "New_Lat": f"{suggested[0]:.4f}", "New_Lng": f"{suggested[1]:.4f}",
                    "Source": source, "City": city, "State": state, "Country": country,
                })

    with fixes_path.open("w", newline="") as ff:
        writer = csv.DictWriter(ff, fieldnames=[
            "Name", "Country", "State", "City",
            "Current_Lat", "Current_Lng", "New_Lat", "New_Lng", "Source",
        ])
        writer.writeheader()
        writer.writerows(fix_rows)

    print(f"Flagged {len(flagged)} rows.")
    print(f"  Auto-fix suggestions: {len(fix_rows)}")
    print(f"  Report: {report_path}")
    print(f"  Fixes:  {fixes_path}")


if __name__ == "__main__":
    main()
