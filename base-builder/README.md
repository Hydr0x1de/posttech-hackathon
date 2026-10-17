# Address base builder (ETL)

`build_osm_db.py` is the offline pipeline that produced
`backend/data/address_base.db` (SQLite + FTS5) from OpenStreetMap and KATO
data. It is **not** part of the runtime image: the API only reads the ready
database.

Large inputs and intermediate dumps are intentionally not committed:

- `kazakhstan-*.osm.pbf` — OSM extract (~215 MB)
- `kato.json` / `kato.csv` — KATO classifier export
- the generated `address_base.db` — the production copy lives in
  `backend/data/address_base.db`

Rebuild:

```bash
pip install osmium shapely
python3 build_osm_db.py \
  --pbf kazakhstan-latest.osm.pbf \
  --kato kato.json \
  --db ../backend/data/address_base.db
```

The script creates the same v2 schema as `backend/db/schema.sql` (including
`name_core_norm`) and populates `fts_address` for fast prefix search.
