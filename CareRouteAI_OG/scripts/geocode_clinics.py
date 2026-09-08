"""Geocode clinic addresses and update clinics/clinics.json

Usage: python scripts/geocode_clinics.py
This uses the existing `utils.helpers.geocode_address` (Nominatim/OpenStreetMap).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.helpers import geocode_address

CLINICS_FILE = ROOT / "clinics" / "clinics.json"


def main():
    data = json.loads(CLINICS_FILE.read_text(encoding="utf-8"))
    backup = CLINICS_FILE.with_suffix('.json.bak')
    backup.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Backup written to {backup}")

    changed = 0
    for c in data:
        addr = c.get("address", "")
        if not addr:
            continue
        geocoded = geocode_address(addr)
        if not geocoded:
            print(f"  ⚠️  Could not geocode: {addr}")
            continue
        lat, lng = geocoded
        if round(lat, 4) != round(c.get("lat", 0), 4) or round(lng, 4) != round(c.get("lng", 0), 4):
            print(f"Updating {c.get('id')} {c.get('name')}: {c.get('lat')},{c.get('lng')} -> {lat},{lng}")
            c['lat'] = lat
            c['lng'] = lng
            changed += 1

    if changed:
        CLINICS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Updated {changed} clinics in {CLINICS_FILE}")
    else:
        print("No changes needed; clinic coordinates are up-to-date.")


if __name__ == '__main__':
    main()
