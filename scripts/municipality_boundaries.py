
import json
from collections import defaultdict
from pathlib import Path

from shapely.geometry import shape, mapping
from shapely.ops import unary_union

SOURCE = Path("data/boundaries/bataan_barangays.geojson")
OUTPUT = Path("data/boundaries/bataan_municipalities.geojson")

data = json.loads(SOURCE.read_text(encoding="utf-8"))

groups = defaultdict(list)
names = {}

for feature in data["features"]:
    props = feature["properties"]
    code = props["municipality_psgc"]

    geometry = shape(feature["geometry"])

    if geometry.is_empty or not geometry.is_valid:
        raise ValueError(f"Invalid geometry: {props['barangay_psgc']}")

    groups[code].append(geometry)
    names[code] = props["municipality"]

features = []

for code, polygons in sorted(groups.items()):
    merged = unary_union(polygons)

    if merged.is_empty or not merged.is_valid:
        raise ValueError(f"Invalid municipality geometry: {code}")

    features.append({
        "type": "Feature",
        "properties": {
            "municipality_psgc": code,
            "municipality": names[code],
        },
        "geometry": mapping(merged),
    })

if len(features) != 12:
    raise ValueError(f"Expected 12 municipalities, found {len(features)}")

result = {
    "type": "FeatureCollection",
    "features": features,
}

OUTPUT.write_text(
    json.dumps(result, ensure_ascii=False),
    encoding="utf-8",
)

print(f"Generated {len(features)} municipalities")
print(f"Saved to: {OUTPUT}")
