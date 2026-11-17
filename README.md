# truckexpoai-osm-data

Public OpenStreetMap truck-restriction and camera extracts. No application code,
API credentials, driver histories or personal data belong in this repository.

## First Run

Setup status: the extraction tools and tests are published. The workflow file
is prepared locally but has not been uploaded: the current Git credential does
not have permission to create workflows. An owner must add the prepared
`.github/workflows/signs.yml` using GitHub's web editor or an appropriately
authorized credential. Do not enable paid runners or change billing settings.
Until that step is complete, the Actions entry below will not appear and no
real-country build has been run.

Run **Actions > Build OSM signs data > Run workflow**, scope `pilot`.
This extracts Andorra and Liechtenstein, records extraction time, observed disk
usage and output size, verifies checksums, and combines both countries.
The result is a **prerelease**, never the production/latest dataset.

After checking the pilot, scope `europe` processes the catalog in
`tools/build_signs.py`, with at most two countries downloading/building at once.
Each worker removes its raw country map when finished. Europe includes the
listed Geofabrik extracts, not an assurance of complete or current road coverage.

No recurring schedule is enabled until measurements and coverage are reviewed.
No Railway setting or phone installation is changed by this workflow.

## Integrity and Resource Controls

- Standard public `ubuntu-latest` runners only; no paid/larger machines.
- Release assets store data; no Actions cache or artifact storage is requested.
- Every requested country must succeed before a release is published.
- Source snapshots older than seven days or with invalid timestamps are rejected.
- Checksums and embedded region metadata must agree before merging.
- OSM identities deduplicate border objects; the newer snapshot wins.
- Limits: 512 MiB compressed / 2 GiB expanded combined database, matching the
  current consumer limits. Exceeding either stops publication, not upgrades billing.
- Failed runs may leave a draft release for diagnosis; the last good release is
  not changed. Remove obsolete drafts manually after investigating.

## Output

`manifest.json`, `signs.sqlite.gz`, `build-report.json`, and individual country
assets. The manifest preserves source timestamps and coverage polygons.
R-tree indexes retain full restricted-way geometry, not just midpoint markers.
The extract includes mapped OSM restrictions and speed-camera nodes; it does not
claim every real sign/camera or every enforcement relation is represented.

Camera publication does not authorize camera alerts in every jurisdiction.
The consuming application must apply applicable local rules and show coverage,
freshness and attribution. Missing data is never evidence that a road is safe.

## License and Attribution

Database: **OpenStreetMap contributors**, Open Database License 1.0:
https://www.openstreetmap.org/copyright
https://opendatacommons.org/licenses/odbl/1-0/

Source extracts: https://download.geofabrik.de/
Retain attribution and comply with ODbL when redistributing derived databases.
Extraction utilities in `tools/` are MIT licensed; see `LICENSE`.

## Local Tests

Python 3.12, `pip install -r tools/requirements.txt pytest`, then
`python -m pytest -q tools`. Tests prohibit real country downloads.
Real extraction additionally requires the `osmium-tool` command-line package.
OpenStreetMap extracts for truck restrictions and cameras. Data-only publication; no application code or personal data.
