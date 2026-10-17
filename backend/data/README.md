# Address base

`address_base.db` (246.6 MB) is **not stored in Git**: GitHub rejects files
larger than 100 MB.

The database is **baked into the published API image**:

```text
ghcr.io/hydr0x1de/posttech-hackathon-api:1.0.0 → /data/address_base.db
```

Runtime containers do not download the DB and do not mount a DB volume. The
healthcheck requires `db_loaded: true`.

## Local image builds

Place the production file here before building:

```text
backend/data/address_base.db
```

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

## CI builds

The GitHub Actions workflow downloads the DB while building the image:

- primary: https://github.com/Hydr0x1de/posttech-hackathon/releases/download/v1.0/address_base.db
- fallback: https://posttech_db.hydr0x1de.xyz/address_base.db

The release/mirror is only a **build input**; the final image is self-contained.

The database is built by `base-builder/build_osm_db.py` from OSM PBF + KATO,
or copied from the team's prepared artifact.
