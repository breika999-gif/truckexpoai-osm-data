"""Build a portable sign-only database outside Railway; never fetch PBFs at request time."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import osmium
import requests
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from signs_schema import ATTRIBUTION, RESTRICTION_TAGS, create_signs_db, insert_element

# Verified against Geofabrik index-v1.json on 2026-11-17. Exclude overlapping regional bundles.
EUROPE_EXTRACTS = (
    'albania', 'andorra', 'austria', 'azores', 'belarus', 'belgium', 'bosnia-herzegovina',
    'bulgaria', 'croatia', 'cyprus', 'czech-republic', 'denmark', 'estonia', 'faroe-islands',
    'finland', 'france', 'georgia', 'germany', 'greece', 'guernsey-jersey', 'hungary', 'iceland',
    'ireland-and-northern-ireland', 'isle-of-man', 'italy', 'kosovo', 'latvia', 'liechtenstein',
    'lithuania', 'luxembourg', 'macedonia', 'malta', 'moldova', 'monaco', 'montenegro',
    'netherlands', 'norway', 'poland', 'portugal', 'romania', 'serbia', 'slovakia', 'slovenia',
    'spain', 'sweden', 'switzerland', 'turkey', 'ukraine', 'united-kingdom',
)
REGIONS = {name: f'https://download.geofabrik.de/europe/{name}' for name in EUROPE_EXTRACTS}
REGIONS.update({'russia': 'https://download.geofabrik.de/russia',
                'canary-islands': 'https://download.geofabrik.de/africa/canary-islands'})
RUSSIA_EUROPE_EXTRACTS = ('central-fed-district', 'kaliningrad', 'north-caucasus-fed-district',
    'northwestern-fed-district', 'south-fed-district', 'ural-fed-district', 'volga-fed-district')
REGIONS.update({name: f'https://download.geofabrik.de/russia/{name}' for name in RUSSIA_EUROPE_EXTRACTS})
EUROPE_DEFAULT_REGIONS = (*EUROPE_EXTRACTS, 'canary-islands', *RUSSIA_EUROPE_EXTRACTS)


def poly_geometry(text):
    """Read Geofabrik .poly rings, including islands and excluded holes."""
    lines = iter(text.splitlines()[1:])
    positive, negative = [], []
    for line in lines:
        name = line.strip()
        if not name:
            continue
        if name == "END":
            break
        points = []
        for line in lines:
            if line.strip() == "END":
                break
            points.append(tuple(map(float, line.split())))
        (negative if name.startswith("!") else positive).append(Polygon(points))
    coverage = unary_union(positive)
    if negative:
        coverage = coverage.difference(unary_union(negative))
    if coverage.is_empty or not coverage.is_valid or coverage.geom_type not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Invalid Geofabrik coverage")
    return mapping(coverage)


def import_pbf(path, db):
    counts = {'restrictions': 0, 'cameras': 0}
    class Signs(osmium.SimpleHandler):
        def node(self, node):
            tags = dict(node.tags)
            if any(key in tags for key in RESTRICTION_TAGS) or tags.get("highway") == "speed_camera" or tags.get("enforcement") in {"speed", "maxspeed"}:
                counts['restrictions'] += int(any(key in tags for key in RESTRICTION_TAGS))
                counts['cameras'] += int(tags.get('highway') == 'speed_camera' or tags.get('enforcement') in {'speed', 'maxspeed'})
                insert_element(db, {"type": "node", "id": node.id, "lat": node.location.lat,
                                    "lon": node.location.lon, "tags": tags})

        def way(self, way):
            tags = dict(way.tags)
            if not any(key in tags for key in RESTRICTION_TAGS):
                return
            counts['restrictions'] += 1
            # Missing referenced nodes must fail the build, not create a falsely complete extract.
            geometry = [{"lon": node.lon, "lat": node.lat} for node in way.nodes]
            insert_element(db, {"type": "way", "id": way.id, "tags": tags, "geometry": geometry})
    Signs().apply_file(str(path), locations=True, idx="flex_mem")
    return counts


def download(url, path):
    with requests.get(url, stream=True, timeout=(15, 180)) as response:
        response.raise_for_status()
        with path.open("wb") as output:
            for chunk in response.iter_content(1024 * 1024):
                output.write(chunk)


def build(output, asset_base_url, names):
    started = time.monotonic()
    names = list(dict.fromkeys(name for choice in names for name in (EUROPE_DEFAULT_REGIONS if choice == 'europe' else [choice])))
    if not names or any(name not in REGIONS for name in names):
        raise ValueError('Unknown or empty extract selection')
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ('signs.sqlite', 'signs.sqlite.gz', 'manifest.json', 'build-report.json')):
        raise ValueError("Use an empty output directory")
    regions = []
    peak_bytes = 0
    def measure():
        nonlocal peak_bytes
        peak_bytes = max(peak_bytes, sum(path.stat().st_size for path in output.rglob('*') if path.is_file()))
    # Nothing publishable is written until every requested extract imported successfully.
    with tempfile.TemporaryDirectory(prefix='.signs-build-', dir=output.resolve()) as temporary:
        stage = Path(temporary)
        database = stage / 'signs.sqlite'
        with create_signs_db(database, []) as db:
            for name in names:
                country_started = time.monotonic()
                base = REGIONS[name]
                with tempfile.TemporaryDirectory(prefix=name + '-', dir=stage) as country:
                    work = Path(country)
                    pbf, filtered, poly = work / f'{name}.pbf', work / f'{name}-signs.pbf', work / f'{name}.poly'
                    download(base + '-latest.osm.pbf', pbf)
                    download(base + '.poly', poly)
                    measure()
                    stamp = subprocess.check_output(['osmium', 'fileinfo', str(pbf), '-g',
                        'header.option.osmosis_replication_timestamp'], text=True).strip()
                    if not stamp:
                        raise ValueError(f'No source snapshot timestamp for {name}')
                    subprocess.run(['osmium', 'tags-filter', str(pbf), *[f'nw/{tag}' for tag in RESTRICTION_TAGS],
                        'n/highway=speed_camera', 'n/enforcement=speed,maxspeed', '-o', str(filtered), '--overwrite'], check=True)
                    counts = import_pbf(filtered, db)
                    measure()
                    db.commit()
                    regions.append({'name': name, 'data_at': stamp, 'coverage': poly_geometry(poly.read_text()),
                        'source': base, 'counts': counts, 'elapsed_s': round(time.monotonic() - country_started, 2)})
                # Both raw and filtered files are removed before requesting the next country.
            db.execute('UPDATE metadata SET value=? WHERE key=?', (json.dumps(regions), 'regions'))
            if db.execute('SELECT COUNT(*) FROM elements').fetchone()[0] == 0:
                raise ValueError('Empty extraction')
        compressed = stage / 'signs.sqlite.gz'
        with database.open('rb') as source, gzip.open(compressed, 'wb', compresslevel=6) as target:
            shutil.copyfileobj(source, target)
        measure()
        with compressed.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        (stage / 'build-report.json').write_text(json.dumps({'elapsed_s': round(time.monotonic() - started, 2),
            'peak_disk_bytes_observed': peak_bytes, 'compressed_bytes': compressed.stat().st_size,
            'regions': [{key: value for key, value in row.items() if key != 'coverage'} for row in regions]}, ensure_ascii=False), encoding='utf-8')
        (stage / 'manifest.json').write_text(json.dumps({'schema': 1,
            'url': asset_base_url.rstrip('/') + '/signs.sqlite.gz', 'sha256': digest,
            'regions': regions, 'attribution': ATTRIBUTION}, ensure_ascii=False), encoding='utf-8')
        for name in ('build-report.json', 'signs.sqlite.gz', 'manifest.json'):
            os.replace(stage / name, output / name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--asset-base-url", required=True)
    parser.add_argument("--regions", nargs="+", choices=['europe', *REGIONS], default=['europe'])
    args = parser.parse_args()
    build(args.output, args.asset_base_url, args.regions)
