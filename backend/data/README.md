# Address base

`address_base.db` (246.6 MB) is **not stored in Git**: GitHub rejects files
larger than 100 MB. Place the production database here before running
`docker compose up -d --build`:

```text
backend/data/address_base.db
```

The file is mounted read-only into the API container as
`/data/address_base.db`. If it is absent, the API starts in fallback mode
(`db_loaded: false`, regex parsing with `confidence = 0.5`), but the `web`
container will wait for the API healthcheck and will not start.

The database is built by `base-builder/build_osm_db.py` from OSM PBF + KATO,
or copied from the team's prepared artifact.
