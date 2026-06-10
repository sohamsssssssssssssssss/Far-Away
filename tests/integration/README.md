# DisasterMind — Integration Tests

docker-compose-backed tests that exercise the **real** backing services against
the production storage/bus code paths (PRD Step 9 storage, Step 10 bus).

| File | Service | Exercises |
|------|---------|-----------|
| `test_kafka_roundtrip.py` | Kafka (`:9092`) | `confluent_kafka` Producer → Consumer round-trip of a `Message.to_dict()` |
| `test_postgis_spatial.py` | PostGIS (`:5432`) | `PostgisResourceRepo` upsert + nearest/within spatial queries |
| `test_timescale_range.py` | TimescaleDB (`:5433`) | `TimescaleTelemetryRepo` append + time-range query |
| `test_elasticsearch_search.py` | Elasticsearch (`:9200`) | `ElasticsearchAuditRepo` index + search (NRT refresh) |
| `test_minio_putget.py` | MinIO (`:9000`) | `MinioArtifactStore` put / get / exists / list / delete |

## Two layers of guarding

1. **Collection gate** — `conftest.py` sets `collect_ignore_glob = ["test_*.py"]`
   **unless** `DM_INTEGRATION=1`. So the default `python -m pytest -q` never
   collects these tests and its count is unchanged.
2. **Per-service skip** — even with `DM_INTEGRATION=1`, each test
   `importorskip`s its client library and TCP-probes its service, skipping
   cleanly (never erroring/failing) when either is absent.

## Run

```bash
# from the repo root
docker compose up -d                       # bring up kafka/postgis/timescaledb/elasticsearch/minio
pip install -e '.[bus,storage]'            # confluent-kafka, psycopg, elasticsearch, minio

DM_INTEGRATION=1 python -m pytest tests/integration -q     # run just the integration suite
# (give the services ~20s to become ready; tests self-skip if any are still down)

docker compose down                        # tear down (add -v to drop volumes)
```

The default suite is unaffected:

```bash
python -m pytest -q          # integration tests are NOT collected (gate off)
```
