# Address base

`address_base.db` (246.6 MB) is **not stored in Git**: GitHub rejects files
larger than 100 MB. The production copy is published as a GitHub Release asset:

https://github.com/Hydr0x1de/posttech-hackathon/releases/download/v1.0/address_base.db

On a clean clone `backend/entrypoint.sh` downloads it automatically into the
`address_data` Docker volume at `/data/address_base.db` before FastAPI starts.
No manual step is required.

Primary source: GitHub Release asset above.
Fallback mirror: https://posttech_db.hydr0x1de.xyz/address_base.db

The mirror is used automatically if GitHub is unavailable. Downloads use
`curl -L`, so an HTTP mirror URL that redirects to HTTPS works too.

If a local file exists at `backend/data/address_base.db`, it is mounted
read-only at `/seed/address_base.db` and used as a seed instead of downloading.
The API itself always reads the writable volume path (`/data/address_base.db`)
in read-only SQLite mode.

To force a fresh download:

```bash
docker compose down -v
docker compose up -d --build
```

The database is built by `base-builder/build_osm_db.py` from OSM PBF + KATO,
or copied from the team's prepared artifact.
