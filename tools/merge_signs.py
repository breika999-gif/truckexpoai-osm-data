"""Validate country extracts and combine them without publishing partial coverage."""
import argparse
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from signs_schema import ATTRIBUTION, create_signs_db, insert_element

MAX_COMPRESSED_BYTES = 512 * 1024 * 1024
MAX_DATABASE_BYTES = 2 * 1024 * 1024 * 1024


def merge(parts, output, names, asset_base_url, now=None):
    now = now or datetime.now(timezone.utc)
    names = list(dict.fromkeys(names))
    if not names or any('/' in name or '\\' in name or name in {'.', '..'} for name in names):
        raise ValueError('Invalid region selection')
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError('Use an empty output directory')
    manifests = []
    for name in names:
        manifest = json.loads((parts / f'{name}.manifest.json').read_text(encoding='utf-8'))
        regions = manifest.get('regions', [])
        if manifest.get('schema') != 1 or len(regions) != 1 or regions[0]['name'] != name:
            raise ValueError(f'Unexpected coverage/schema: {name}')
        stamp = datetime.fromisoformat(regions[0]['data_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or not now - timedelta(days=7) <= stamp <= now + timedelta(hours=1):
            raise ValueError(f'Stale or invalid source timestamp: {name}')
        archive = parts / f'{name}.sqlite.gz'
        if archive.stat().st_size > MAX_COMPRESSED_BYTES:
            raise ValueError(f'Country archive too large: {name}')
        with archive.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != manifest['sha256']:
                raise ValueError(f'Checksum mismatch: {name}')
        manifests.append((stamp, name, manifest))
    regions = [manifest['regions'][0] for _, _, manifest in manifests]
    with tempfile.TemporaryDirectory(prefix='.merge-', dir=output.resolve()) as temporary:
        stage = Path(temporary)
        database = stage / 'signs.sqlite'
        with create_signs_db(database, regions) as target:
            # Newer border objects win, regardless of matrix completion order.
            for _, name, manifest in sorted(manifests, reverse=True):
                country = stage / 'country.sqlite'
                with gzip.open(parts / f'{name}.sqlite.gz', 'rb') as source, country.open('wb') as dest:
                    written = 0
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > MAX_DATABASE_BYTES:
                            raise ValueError('Expanded country database too large')
                        dest.write(chunk)
                source_db = sqlite3.connect(country.as_uri() + '?mode=ro', uri=True)
                try:
                    if source_db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise ValueError(f'Invalid SQLite database: {name}')
                    metadata = dict(source_db.execute('SELECT key, value FROM metadata'))
                    if json.loads(metadata['schema']) != 1 or json.loads(metadata['regions']) != manifest['regions']:
                        raise ValueError(f'Database metadata mismatch: {name}')
                    count = 0
                    for payload, in source_db.execute('SELECT payload FROM elements ORDER BY id'):
                        insert_element(target, json.loads(payload))
                        count += 1
                        if count % 10000 == 0:
                            target.commit()
                            if database.stat().st_size > MAX_DATABASE_BYTES:
                                raise ValueError('Combined database exceeds server limit')
                    if not count:
                        raise ValueError(f'Empty country extraction: {name}')
                finally:
                    source_db.close()
                country.unlink()
                target.commit()
            counts = dict(target.execute('SELECT kind, COUNT(*) FROM elements GROUP BY kind'))
        if database.stat().st_size > MAX_DATABASE_BYTES:
            raise ValueError('Combined database exceeds server limit')
        compressed = stage / 'signs.sqlite.gz'
        with database.open('rb') as source, gzip.open(compressed, 'wb', compresslevel=6) as dest:
            shutil.copyfileobj(source, dest)
        if compressed.stat().st_size > MAX_COMPRESSED_BYTES:
            raise ValueError('Combined archive exceeds server limit')
        with compressed.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        report = {'regions': names, 'counts': counts, 'compressed_bytes': compressed.stat().st_size,
                  'database_bytes': database.stat().st_size}
        (stage / 'build-report.json').write_text(json.dumps(report), encoding='utf-8')
        (stage / 'manifest.json').write_text(json.dumps({'schema': 1, 'regions': regions,
            'url': asset_base_url.rstrip('/') + '/signs.sqlite.gz', 'sha256': digest,
            'attribution': ATTRIBUTION}), encoding='utf-8')
        for name in ('build-report.json', 'signs.sqlite.gz', 'manifest.json'):
            os.replace(stage / name, output / name)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--regions', nargs='+', required=True)
    parser.add_argument('--asset-base-url', required=True)
    args = parser.parse_args()
    merge(args.parts, args.output, args.regions, args.asset_base_url)
