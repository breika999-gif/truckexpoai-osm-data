from datetime import datetime, timezone
import gzip
import hashlib
import json
import sqlite3

import pytest

from merge_signs import merge
from signs_schema import create_signs_db, insert_element

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def part(path, name, stamp='2026-11-16T00:00:00Z', height='4'):
    region = {'name': name, 'data_at': stamp, 'coverage': {'type': 'Polygon',
              'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
    database = path / f'{name}.sqlite'
    with create_signs_db(database, [region]) as db:
        insert_element(db, {'type': 'node', 'id': 1, 'lat': 0, 'lon': 0, 'tags': {'maxheight': height}})
    archive = path / f'{name}.sqlite.gz'
    archive.write_bytes(gzip.compress(database.read_bytes()))
    (path / f'{name}.manifest.json').write_text(json.dumps({'schema': 1, 'regions': [region],
        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}))


def test_merge_deduplicates_borders_and_retains_newest_payload(tmp_path):
    part(tmp_path, 'andorra', height='3')
    part(tmp_path, 'france', '2026-11-16T01:00:00Z', height='4')
    output = tmp_path / 'out'
    merge(tmp_path, output, ['andorra', 'france'], 'https://example.com/release', NOW)
    manifest = json.loads((output / 'manifest.json').read_text())
    assert len(manifest['regions']) == 2
    assert hashlib.sha256((output / 'signs.sqlite.gz').read_bytes()).hexdigest() == manifest['sha256']
    db_path = tmp_path / 'result.sqlite'
    db_path.write_bytes(gzip.decompress((output / 'signs.sqlite.gz').read_bytes()))
    with sqlite3.connect(db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM bounds').fetchone()[0] == 1
        assert json.loads(db.execute('SELECT payload FROM elements').fetchone()[0])['tags']['maxheight'] == '4'


@pytest.mark.parametrize('failure', ['missing', 'checksum', 'stale', 'metadata'])
def test_failed_input_cannot_publish_manifest(tmp_path, failure):
    part(tmp_path, 'andorra', '2026-11-01T00:00:00Z' if failure == 'stale' else '2026-11-16T00:00:00Z')
    if failure == 'checksum':
        (tmp_path / 'andorra.sqlite.gz').write_bytes(b'corrupt')
    if failure == 'metadata':
        path = tmp_path / 'andorra.manifest.json'
        data = json.loads(path.read_text())
        data['regions'][0]['coverage'] = None
        path.write_text(json.dumps(data))
    names = ['andorra', 'france'] if failure == 'missing' else ['andorra']
    output = tmp_path / 'out'
    with pytest.raises((ValueError, FileNotFoundError)):
        merge(tmp_path, output, names, 'https://example.com/release', NOW)
    assert not (output / 'manifest.json').exists()


def test_never_overwrites_existing_release(tmp_path):
    output = tmp_path / 'out'
    output.mkdir()
    manifest = output / 'manifest.json'
    manifest.write_text('old')
    with pytest.raises(ValueError):
        merge(tmp_path, output, ['andorra'], 'https://example.com', NOW)
    assert manifest.read_text() == 'old'
