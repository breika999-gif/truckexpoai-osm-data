import importlib.util
from pathlib import Path

import pytest
import json
import shutil

osmium = pytest.importorskip("osmium")
spec = importlib.util.spec_from_file_location("build_signs", Path(__file__).with_name("build_signs.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture(autouse=True)
def forbid_real_downloads(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('Tests must not download country extracts')
    monkeypatch.setattr(builder, 'download', deny)


def test_import_keeps_way_shape_and_filters_unrelated_nodes(tmp_path):
    source = tmp_path / "small.osm"
    source.write_text('''<osm version="0.6">
      <node id="1" lat="42" lon="0"/>
      <node id="2" lat="42.005" lon="0.005"/>
      <node id="3" lat="42" lon="0.01"/>
      <node id="4" lat="42.005" lon="0.005"><tag k="highway" v="speed_camera"/></node>
      <way id="5"><nd ref="1"/><nd ref="2"/><nd ref="3"/><tag k="maxheight" v="3.8"/></way>
    </osm>''')
    path = tmp_path / "signs.sqlite"
    with builder.create_signs_db(path, []) as db:
        builder.import_pbf(source, db)
        rows = db.execute("SELECT kind, payload FROM elements ORDER BY id").fetchall()
    assert len(rows) == 2
    assert '"lat":42.005' in rows[1][1]
    assert rows[1][0] == "restriction"


def test_poly_holes_are_not_declared_covered():
    from shapely.geometry import Point, shape
    coverage = shape(builder.poly_geometry('''extract
1
0 0
2 0
2 2
0 2
0 0
END
!hole
0.5 0.5
1 0.5
1 1
0.5 1
0.5 0.5
END
END
'''))
    assert coverage.covers(Point(0.1, 0.1))
    assert not coverage.covers(Point(0.75, 0.75))


def fake_build_tools(monkeypatch, output, fail=None):
    poly = 'extract\n1\n-1 41\n1 41\n1 43\n-1 43\n-1 41\nEND\nEND\n'
    def download(url, target):
        if url.endswith('.osm.pbf'):
            assert not list(output.rglob('*-signs.pbf')), 'previous country still occupies disk'
        if fail and fail in url:
            raise RuntimeError('upstream failed')
        if url.endswith('.poly'):
            target.write_text(poly)
        else:
            with osmium.SimpleWriter(str(target)) as writer:
                writer.add_node(osmium.osm.mutable.Node(id=1, location=(0, 42), tags={'maxheight': '4'}))
    def run(args, **kwargs):
        shutil.copyfile(args[2], args[args.index('-o') + 1])
    monkeypatch.setattr(builder, 'download', download)
    monkeypatch.setattr(builder.subprocess, 'check_output', lambda *args, **kwargs: '2026-11-16T00:00:00Z')
    monkeypatch.setattr(builder.subprocess, 'run', run)


def test_country_files_are_removed_before_next_country_and_manifest_is_complete(tmp_path, monkeypatch):
    output = tmp_path / 'output'
    fake_build_tools(monkeypatch, output)
    builder.build(output, 'https://example.com/version', ['spain', 'france'])
    manifest = json.loads((output / 'manifest.json').read_text())
    assert [row['name'] for row in manifest['regions']] == ['spain', 'france']
    assert (output / 'build-report.json').exists()
    assert not list(output.rglob('*.pbf'))


def test_country_failure_does_not_leave_publishable_files(tmp_path, monkeypatch):
    output = tmp_path / 'output'
    fake_build_tools(monkeypatch, output, 'france')
    with pytest.raises(RuntimeError):
        builder.build(output, 'https://example.com/version', ['spain', 'france'])
    assert not (output / 'manifest.json').exists()
    assert not (output / 'signs.sqlite.gz').exists()
    assert not list(output.iterdir())


def test_existing_release_is_never_overwritten(tmp_path):
    old = tmp_path / 'manifest.json'
    old.write_text('last-good')
    with pytest.raises(ValueError):
        builder.build(tmp_path, 'https://example.com/version', ['spain'])
    assert old.read_text() == 'last-good'


def test_verified_catalog_covers_the_requested_trucking_countries():
    assert {'bulgaria', 'romania', 'hungary', 'austria', 'germany', 'netherlands', 'belgium',
            'italy', 'switzerland', 'serbia', 'macedonia', 'turkey', 'united-kingdom', 'ukraine'} <= builder.REGIONS.keys()
    assert builder.REGIONS['canary-islands'] == 'https://download.geofabrik.de/africa/canary-islands'
    assert builder.REGIONS['russia'] == 'https://download.geofabrik.de/russia'
