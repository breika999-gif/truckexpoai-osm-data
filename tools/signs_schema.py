"""Portable OSM sign extract; separate from the application/user database."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

RESTRICTION_TAGS = (
    "maxheight", "maxweight", "maxwidth", "maxlength", "maxaxleload",
    "maxweight:hgv", "maxweightrating", "maxgcweight", "hgv", "goods", "hazmat",
)
SCHEMA_VERSION = 1
ATTRIBUTION = "© OpenStreetMap contributors (ODbL 1.0); extracts by Geofabrik"


@contextmanager
def create_signs_db(path, regions):
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE elements (id INTEGER PRIMARY KEY, osm_key TEXT UNIQUE NOT NULL,
            kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE VIRTUAL TABLE bounds USING rtree(id, west, east, south, north);
    """)
    for key, value in {"schema": SCHEMA_VERSION, "regions": regions,
                       "built_at": datetime.now(timezone.utc).isoformat(), "attribution": ATTRIBUTION}.items():
        db.execute("INSERT INTO metadata VALUES (?, ?)", (key, json.dumps(value)))
    try:
        yield db
        db.commit()
    finally:
        db.close()


def insert_element(db, element):
    tags = element.get("tags", {})
    camera = tags.get("highway") == "speed_camera" or tags.get("enforcement") in {"speed", "maxspeed"}
    if not camera and not any(tag in tags for tag in RESTRICTION_TAGS):
        return
    points = element.get("geometry") or [{"lon": element.get("lon"), "lat": element.get("lat")}]
    if not points or any(p.get("lat") is None or p.get("lon") is None for p in points):
        raise ValueError("OSM element has incomplete geometry")
    key = f'{element["type"]}:{element["id"]}'
    # Border objects can occur in both extracts; retain one stable OSM identity.
    kind = "both" if camera and any(tag in tags for tag in RESTRICTION_TAGS) else "camera" if camera else "restriction"
    db.execute("INSERT OR IGNORE INTO elements(osm_key, kind, payload) VALUES (?, ?, ?)",
               (key, kind, json.dumps(element, separators=(",", ":"))))
    row_id = db.execute("SELECT id FROM elements WHERE osm_key=?", (key,)).fetchone()[0]
    db.execute("INSERT OR IGNORE INTO bounds VALUES (?, ?, ?, ?, ?)",
               (row_id, min(p["lon"] for p in points), max(p["lon"] for p in points),
                min(p["lat"] for p in points), max(p["lat"] for p in points)))
