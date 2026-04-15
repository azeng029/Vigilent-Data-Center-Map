"""
Optimize QGIS2Web Map Data
============================
Simplifies GeoJSON geometry in the qgis2web data/*.js files using
Douglas-Peucker algorithm.  Targets the massive Canada boundary files
(5 × 133 MB) and the WorldMap file (8.6 MB).

Backs up originals to data/originals/ before overwriting.

Usage:
    python3 optimize_map.py

Result:
    ~694 MB → ~25-35 MB total map size
"""

import json
import os
import re
import shutil
from shapely.geometry import shape, mapping
from shapely.ops import transform

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

MAP_DIR = "qgis2web_2026_04_01-14_53_08_869925"
DATA_DIR = os.path.join(MAP_DIR, "data")
BACKUP_DIR = os.path.join(DATA_DIR, "originals")

# Files to simplify and their tolerance (degrees).
# Higher tolerance = more simplification.  0.01° ≈ 1 km at mid-latitudes.
FILES_TO_SIMPLIFY = {
    # Canada files — extremely detailed, need aggressive simplification
    "CanadaCompositeScore_6.js": 0.02,
    "CanadaRegulationsByQuantity_7.js": 0.02,
    "CanadaCommercialElectricityRatekWh_8.js": 0.02,
    "CanadaCommercialWaterRate1000gallons_9.js": 0.02,
    "CanadaOutlineMap_10.js": 0.02,
    # World map — moderately detailed
    "WorldMap_0.js": 0.02,
    # Brazil — small but can still trim
    "BrazilCompositeScore_1.js": 0.01,
    "BrazilRegulationsByQuantity_2.js": 0.01,
    "BrazilCommercialElectricityRateskWh_3.js": 0.01,
    "BrazilCommercialWaterRates1000gallons_4.js": 0.01,
    "BrazilOutlineMap_5.js": 0.01,
    # Europe
    "EuropeCompositeScore_11.js": 0.01,
    "EuropeRegulationsByQuantity_12.js": 0.01,
    "EuropeCommercialElectricityRateskWh_13.js": 0.01,
    "EuropeCommercialWaterRates1000gallons_14.js": 0.01,
    # US
    "USCompositeScore_15.js": 0.01,
    "USRegulationsByQuantity_16.js": 0.01,
    "USCommercialElectricityRateskWh_17.js": 0.01,
    "USCommercialWaterRates1000gallons_18.js": 0.01,
}


# ═══════════════════════════════════════════════════════════════════════════════
# GEOMETRY SIMPLIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def round_coords(geom, precision=4):
    """Round all coordinates to N decimal places to reduce file size."""
    def _round(x, y, z=None):
        if z is not None:
            return round(x, precision), round(y, precision), round(z, precision)
        return round(x, precision), round(y, precision)
    return transform(_round, geom)


def simplify_feature(feature, tolerance):
    """Simplify a single GeoJSON feature's geometry."""
    geom = shape(feature["geometry"])
    if geom.is_empty:
        return feature

    simplified = geom.simplify(tolerance, preserve_topology=True)
    simplified = round_coords(simplified)

    if simplified.is_empty:
        return feature

    feature["geometry"] = mapping(simplified)
    return feature


def simplify_geojson(geojson, tolerance):
    """Simplify all features in a GeoJSON FeatureCollection."""
    original_count = 0
    simplified_count = 0

    for feature in geojson.get("features", []):
        geom = feature.get("geometry")
        if not geom:
            continue

        # Count original coords
        original_count += count_coords(geom)

        feature = simplify_feature(feature, tolerance)

        simplified_count += count_coords(feature["geometry"])

    return geojson, original_count, simplified_count


def count_coords(geom):
    """Recursively count coordinate pairs in a GeoJSON geometry."""
    gtype = geom.get("type", "")
    coords = geom.get("coordinates", [])

    if gtype == "Point":
        return 1
    elif gtype in ("LineString", "MultiPoint"):
        return len(coords)
    elif gtype in ("Polygon", "MultiLineString"):
        return sum(len(ring) for ring in coords)
    elif gtype == "MultiPolygon":
        return sum(len(ring) for poly in coords for ring in poly)
    elif gtype == "GeometryCollection":
        return sum(count_coords(g) for g in geom.get("geometries", []))
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# FILE I/O
# ═══════════════════════════════════════════════════════════════════════════════

def parse_js_file(filepath):
    """Parse a qgis2web .js data file → (variable_name, geojson_dict)."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Format: var json_LayerName_N = { ... GeoJSON ... }
    match = re.match(r'var\s+(json_\w+)\s*=\s*', content)
    if not match:
        raise ValueError(f"Could not parse variable name from {filepath}")

    var_name = match.group(1)
    json_str = content[match.end():].rstrip().rstrip(';')
    geojson = json.loads(json_str)
    return var_name, geojson


def write_js_file(filepath, var_name, geojson):
    """Write GeoJSON back as a qgis2web .js data file."""
    # Use separators to minimize whitespace (single-line, compact)
    json_str = json.dumps(geojson, separators=(',', ':'))
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"var {var_name} = {json_str};")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  QGIS2WEB MAP OPTIMIZER")
    print("=" * 60)

    # Backup originals
    os.makedirs(BACKUP_DIR, exist_ok=True)
    print(f"\nBacking up originals to {BACKUP_DIR}/")

    total_original = 0
    total_simplified = 0

    for filename, tolerance in FILES_TO_SIMPLIFY.items():
        filepath = os.path.join(DATA_DIR, filename)
        if not os.path.exists(filepath):
            print(f"  SKIP (not found): {filename}")
            continue

        orig_size = os.path.getsize(filepath)
        total_original += orig_size

        # Backup if not already backed up
        backup_path = os.path.join(BACKUP_DIR, filename)
        if not os.path.exists(backup_path):
            shutil.copy2(filepath, backup_path)

        print(f"\n  Processing: {filename}")
        print(f"    Original: {orig_size / 1024 / 1024:.1f} MB")

        # Parse, simplify, write
        var_name, geojson = parse_js_file(filepath)
        geojson, orig_coords, simp_coords = simplify_geojson(geojson, tolerance)
        write_js_file(filepath, var_name, geojson)

        new_size = os.path.getsize(filepath)
        total_simplified += new_size
        reduction = (1 - new_size / orig_size) * 100 if orig_size > 0 else 0

        print(f"    Simplified: {new_size / 1024 / 1024:.1f} MB  ({reduction:.0f}% reduction)")
        print(f"    Coords: {orig_coords:,} → {simp_coords:,}")

    # DC point files (no simplification needed, just report size)
    dc_files = [f for f in os.listdir(DATA_DIR)
                if f.startswith("VigilentDataCenter") and f.endswith(".js")]
    dc_total = sum(os.path.getsize(os.path.join(DATA_DIR, f)) for f in dc_files)

    print("\n" + "=" * 60)
    print("  RESULTS")
    print("=" * 60)
    print(f"  Boundary layers: {total_original / 1024 / 1024:.1f} MB → {total_simplified / 1024 / 1024:.1f} MB")
    print(f"  DC point layers: {dc_total / 1024:.0f} KB (unchanged)")
    print(f"  Total map data:  {(total_simplified + dc_total) / 1024 / 1024:.1f} MB")
    print(f"  Overall reduction: {(1 - (total_simplified + dc_total) / (total_original + dc_total)) * 100:.0f}%")
    print(f"\n  Originals backed up to: {BACKUP_DIR}/")
    print("  Done!\n")


if __name__ == "__main__":
    main()
