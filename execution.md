# Soko Phase 2 — Execution & Testing Walkthrough

## Prerequisites

Three config changes are required before running the stack end-to-end:

- `data-ingestion-service` joined to `soko-ml-bridge` (so it can reach core services)
- `user_service`, `order_service`, `produce_service` joined to `soko-ml-bridge`
- `services/soko-ml/.env` updated with all Phase 2 variables

All three are already applied in the codebase.

---

## Step 1 — Unit tests (no Docker needed)

Pure function tests — crop normalisers, transport rates, sell signal logic. Always pass offline.

```bash
# Install Python deps (only needed once, or after adding a new service)
make install

# Transformer tests: crop normalisation, market mapping, UGX price field names
make test-ingest

# Location tests: haversine distance, transport cost bands, sell signal derivation
make test-location
```

Expected: all green. DB-dependent tests auto-skip with `pytest.skip("Postgres unreachable")` — that is correct behaviour offline.

---

## Step 2 — Tear down any existing ML stack

```bash
make ml-down
```

---

## Step 3 — Cold-start Phase 2

Starts the ML Postgres, applies the schema, builds and starts all seven ML services, then auto-triggers the initial data bootstrap from core services.

```bash
make cold-start
```

Takes ~90 seconds. Internal sequence:
1. Ensures `soko-ml-bridge` network exists
2. Starts `soko-ml-db` and runs `db/schema.sql`
3. Builds and starts all ML containers
4. Waits 20 s for healthchecks to pass
5. POSTs to `/bootstrap` on the ingest service

Follow progress:
```bash
make ml-logs
# Ctrl-C when all services show "Application startup complete"
```

---

## Step 4 — Restart core services on the new network

Core service containers were started before `soko-ml-bridge` was added to them. Recreate them so they join it.

```bash
make core-restart
```

After this, `data-ingestion-service` can reach `http://user_service:8002`, `http://order_service:8004`, and `http://produce_service:8003` directly over `soko-ml-bridge`.

---

## Step 5 — Health check every service

```bash
make health
```

| Service | Port |
|---|---|
| API gateway (nginx) | `:80` |
| ML gateway | `:8080` |
| Price prediction | `:8094` |
| Recommendation | `:8095` |
| Location service | `:8003` |
| Data ingestion | `:8096` |

All should return `{"status":"ok"}`. If any show `UNREACHABLE`, tail their logs:

```bash
make logs-location
make logs-ingest
make logs-gateway
```

---

## Step 6 — Check bootstrap status

```bash
make ingest-status
```

Returns a JSON breakdown of farmers, produce listings, and price observations pulled from core services. If `bootstrap_complete: false`, trigger it manually:

```bash
make ingest-bootstrap
```

---

## Step 7 — Smoke test Phase 1 features (regression check)

Verify price predictions and recommendations are unaffected:

```bash
make smoke-test
```

Hits `/price/predict` with `maize_grain` at `Kisenyi_Kampala` and both recommendation endpoints.

---

## Step 8 — Smoke test Phase 2 location endpoints

**Market routing** — ranked markets with sell signal and transport cost for a farmer:

```bash
make smoke-route
```

Response includes:
- `tier` — 1 (full ML), 2 (category band), or 3 (unknown crop)
- `ranked_markets` — each with `ugx_per_kg`, `mode` (e.g. `boda_cargo`, `pickup_truck`), and `sell_signal`

**Buyer-to-farmer discovery** — farmers near a buyer within 150 km under 2 000 UGX/kg:

```bash
make smoke-discover
```

**Tier 2 fallback** — niche crop with thin ML coverage falls back to category price band:

```bash
make smoke-fallback
```

**Tier 3** — completely unknown crop (`moringa`) returns a gap notification and publishes to `soko.gaps`:

```bash
make smoke-tier3
```

---

## Step 9 — Smoke test the ingest endpoint

Posts a synthetic `purchase_completed` event and writes a `price_observation` row:

```bash
make smoke-ingest
```

Confirm it landed in the DB:

```bash
make db-shell
```

Inside psql:

```sql
SELECT crop, market, price_per_kg, currency, source
FROM price_observations
ORDER BY observed_at DESC
LIMIT 5;
\q
```

Expected: `currency = UGX`, `price_per_kg = 1400` for event `TEST-001`.

---

## Step 10 — Gap report

After the Tier 3 smoke test, `moringa` will appear here:

```bash
make gaps-summary
```

Returns coverage counts per crop/market and a `gap_level` (`low` / `medium` / `high`) based on the thresholds in `services/soko-ml/.env`.

---

## Quick reference — troubleshooting

| Problem | Command |
|---|---|
| DB schema missing | `make db-reset` *(destructive)* |
| Kafka topics not created | `make kafka-topics` |
| Redis full / stale cache | `make redis-cli` → `FLUSHALL` |
| Re-run bootstrap | `make ingest-bootstrap` |
| Wipe everything and restart | `make clean-docker` then `make cold-start` |
| Rebuild one service only | `docker compose -f services/soko-ml/docker-compose.yml up --build -d <service-name>` |

---

## Port map

| Service | Host port | Container port |
|---|---|---|
| nginx (API gateway) | 80 | 80 |
| ML gateway | 8080 | 8000 |
| Price prediction | 8094 | 8001 |
| Recommendation | 8095 | 8002 |
| Location service | 8003 | 8003 |
| Data ingestion | 8096 | 8004 |
| Kafka (external) | 29092 | 29092 |

---

## Transport cost reference (FarasUG / SafeBoda benchmarks)

| Distance | Mode | UGX/kg |
|---|---|---|
| 0 – 25 km | boda_cargo | 290 |
| 25 – 80 km | taxi_van | 420 |
| 80 – 200 km | pickup_truck | 620 |
| 200 – 400 km | shared_lorry | 850 |
| 400+ km | cross_region | 1 100 |

Rates reflect partial-load pricing for 100–500 kg loads. All monetary values in UGX.
