# Soko ML Phase 2 — Complete System Analysis

**Date of Analysis:** 2026-05-14  
**Scope:** Phase 2 extension of the Soko ML service layer — transforming a static CSV-driven price prediction system into a live, data-ingesting, location-aware farmer decision support system.

---

## 1. What Phase 2 Actually Builds

Phase 1 delivered Prophet-based price prediction and collaborative-filtering recommendations, both trained on synthetic CSV data. Phase 2 replaces every static data source with live backend data and adds two new services. The result is a system that:

- Learns from real transactions as they happen
- Recommends the best market to sell at (not just the price)
- Gives the farmer a GO/WAIT sell signal based on price trajectory
- Falls back gracefully when ML coverage is insufficient
- Tracks its own blind spots and escalates them for remediation

---

## 2. Discovery Findings (What the Code Actually Contains)

### Auth Service (`services/auth`)
- Stores only credentials (email, hashed password, role). No profile data, no GPS.
- All profile operations delegated to user-service at registration time.
- **Impact:** Bootstrap clients must call user-service, not auth-service.

### User Service (`services/user`)
- `UserProfile` has `district` and `village` as plain strings. No lat/lng.
- `specialties` is a comma-separated string (e.g., `"maize,beans,coffee"`).
- `GET /users/farmers` already existed (public). `GET /users/buyers` did not — added.
- **Impact:** All GPS must come from district-centroid lookup tables, not user records.

### Order Service (`services/order`)
- `OrderStatus` enum: `pending → confirmed → processing → dispatched → delivered → cancelled`. No "completed".
- `checkout()` publishes to `soko.transactions` with `crop = product.category` (e.g., `"Grains"`) — not the specific crop name.
- **Impact:** (1) Bootstrap must filter by `delivered` status. (2) `product_name` must be added to the Kafka payload to enable specific crop identification.

### Produce Service (`services/produce`)
- Service is named `produce-service` in Docker. Prompt called it `listing-service` — all client code uses the actual name.
- No Kafka publisher. Listings are only accessible via HTTP.
- **Impact:** Periodic re-bootstrap every 15 minutes for profile sync (no push-based update path).

---

## 3. Architectural Decisions and Their Implications

### Decision 1: Add `product_name` to the Kafka Transaction Payload

**What changed:** `services/order/app/kafka_publisher.py` and `routers/orders.py` now include `product_name` (the listing title, e.g., `"Maize (Dry)"`) alongside the existing `crop` (the category, e.g., `"Grains"`).

**Why it was needed:** The ML price model is trained on specific crops (`maize_grain`, `yellow_beans`). The category field alone is ambiguous — `"Grains"` maps to 5+ crops. Without `product_name`, every streaming transaction event would need to guess the crop, producing noise in training data.

**How it works:** `normalise_crop_from_order()` in `price_transformer.py` tries `product_name` first via the `CROP_NAME_NORMALISER` dict, then falls back to category-level mapping if the product name is unrecognisable. This means a transition period where older orders (no `product_name`) still produce usable (if slightly noisier) observations.

**Risk:** Requires a coordinated deploy — data-ingestion-service must be updated after order-service, or it will receive events without `product_name` and fall back gracefully.

---

### Decision 2: No Kafka Publishers in User/Produce Services → Periodic Re-Bootstrap Instead

**What was considered:** Adding Kafka `profile_updated` events to user-service and produce-service, so the ML layer would get push-based profile updates.

**Why rejected:** These services have no Kafka infrastructure. Adding it would be a significant cross-service change with no benefit to the core product, only to the ML layer.

**What was built instead:** `recommendation-service` calls `GET /users/farmers` and `GET /users/buyers` every `PROFILE_REFRESH_INTERVAL_SECONDS` (default: 15 min) via `asyncio.create_task` background loop. Data is loaded directly into the in-memory `ProfileStore`.

**Implication:** A farmer who updates their specialties will not be reflected in recommendations for up to 15 minutes. This is acceptable for a smallholder market with low-frequency profile changes.

**Implication for bootstrap:** `data-ingestion-service` runs a full HTTP bootstrap of all farmers, buyers, and delivered orders at startup, then listens to `soko.transactions` for incremental updates. The Postgres feature store (`farmer_features`, `buyer_features`) is the ground truth; the recommendation service's in-memory store is a read cache of it.

---

### Decision 3: Uganda District Centroids as GPS Approximation

**What was built:** `DISTRICT_COORDINATES` in `farmer_transformer.py` maps 25 Uganda districts to approximate lat/lng centroid coordinates. `DISTRICT_TO_MARKET` maps districts to ML market node IDs (used by `price_transformer.py` and `geo_recommender.py`).

**Why it was needed:** User profiles store `district` (e.g., `"Gulu"`) as a string. No GPS field exists. The location-service and recommendation-service both need coordinates for distance calculations.

**Known limitation:** District centroids can be 20-50 km from where the farmer actually is. The `GEO_FILTER_RELAX_FACTOR` (default: 1.5) compensates by expanding the Haversine pre-filter radius by 50%. All API responses include a `location_precision: "district_centroid"` flag so callers know the coordinates are approximate.

**Upgrade path:** If the user-service adds a GPS field to `UserProfile` in the future, `farmer_transformer.py` can use it directly — the `district_to_coords()` function is only called when no explicit coordinates are present.

---

## 4. New Services

### `data-ingestion-service` (port 8096)

**Purpose:** Single entry point for all data flowing into the ML Feature Store. Owns the Postgres `soko_ml_db` database exclusively.

**Startup sequence:**
1. Connect to Postgres
2. Apply schema if needed (`db-init` container runs schema.sql separately)
3. If `BOOTSTRAP_ON_STARTUP=true` AND all three core tables are empty: trigger full HTTP bootstrap from user/order/produce services
4. Start `TransactionStream` background thread consuming `soko.transactions`

**Key endpoints:**
- `POST /bootstrap` — Trigger or re-trigger full sync
- `GET /bootstrap/status` — Check if bootstrap is in progress / complete
- `POST /ingest/order-event` — Accept a single transaction event (called by kafka-agent's `TransactionPriceCollector`)
- `GET /gaps/summary` — Show which crop/market pairs need more data
- `GET /coverage` — Full coverage map

**Data quality mechanisms:**
- Outlier rejection: 3σ rolling window (last 30 observations per crop/market pair)
- Deduplication: Postgres `UNIQUE` constraint on `order_id` in `price_observations`
- Both streaming path (`TransactionStream`) and HTTP path (`/ingest/order-event`) exist simultaneously; deduplication handles double-writes

---

### `location-service` (port 8003)

**Purpose:** Given a farmer + crop, recommend which markets to sell at and when.

**`POST /route` — The core endpoint:**
1. Load market registry from `soko_ml_db` (Redis-cached for 6h)
2. Get distances to all markets (Google Maps batch call, or Haversine fallback; Redis-cached per farmer×market pair for 30 days)
3. Fetch Prophet price predictions from `ml-gateway-service` for each market
4. Compute net value = `predicted_price × quantity - transport_cost`
5. Derive GO/WAIT sell signal from price trend, perishability, harvest month
6. Return ranked market list with signal

**Three-tier fallback:**
- **Tier 1:** Full ML — Prophet predictions available for this crop/market pair (`observation_count >= 30`)
- **Tier 2:** Category price band — crop is known but specific market has insufficient data. Returns historical price range for the crop category
- **Tier 3:** Unknown crop — crop not in the ML catalogue at all. Returns generic advice, records a coverage gap event, publishes to `soko.gaps`

**`POST /discover` — Buyer-side discovery:**
Finds farmers near a buyer who grow a requested crop. Uses `farmer_features` table (district centroids + Haversine filter).

---

## 5. Modified Existing Services

### `price-prediction-service`
- Added `train_all_models_from_feature_store()`: reads real price observations from Postgres instead of CSV; triggered when `retrain_requested` event arrives on `soko.ml.events`
- Original `train_all_models()` (CSV-based) retained as cold-start fallback

### `recommendation-service`
- `ProfileStore` rewritten: `async reload()` fetches from Postgres feature store instead of reading CSV files
- Startup fails fast (SystemExit) if DB is unreachable — no silent degradation with empty profiles
- Background reload task runs every 15 min

### `ml-gateway-service`
- Added proxying for `/location/*`, `/gaps/*`, `/coverage`, `/ingest/*` routes
- Added circuit breakers for `location` and `ingestion` services
- Health check now aggregates status from all 4 downstream services (price, rec, location, ingest); only price + rec are required for `overall: ok`

### `kafka-agent`
- Added `CoverageGapConsumer`: consumes `soko.gaps` for monitoring/logging
- Added `TransactionPriceCollector`: consumes `soko.transactions`, forwards `purchase_completed` events to `data-ingestion-service /ingest/order-event` via HTTP (alternative path to the internal `TransactionStream`)

---

## 6. Data Flow Diagrams

### Transaction → Price Observation Flow

```
Order Service
  └─ checkout() → publish to soko.transactions
         │
         ├─► TransactionStream (data-ingestion-service internal thread)
         │     └─ insert_price_observation() → price_observations (Postgres)
         │           └─ trigger: trg_update_coverage → updates coverage_map
         │                 └─ if observation_count >= 30 → publish retrain_requested
         │                       └─ price-prediction-service retrains Prophet model
         │
         └─► TransactionPriceCollector (kafka-agent)
               └─ POST /ingest/order-event → data-ingestion-service
                     └─ insert_price_observation() (deduplicated by order_id)
```

### Farmer Market Routing Flow

```
Client → POST /ml/location/route
  └─ ml-gateway-service → POST /route → location-service
       │
       ├─ Load market registry (soko_ml_db → Redis cache)
       ├─ Get farmer GPS (district centroid if no lat/lng)
       ├─ Fetch road distances (Google Maps API → Redis cache 30 days)
       ├─ Fetch Prophet predictions (ml-gateway → price-prediction-service)
       │     └─ Tier check: coverage_map.is_model_ready?
       │           ├─ Tier 1: full ML prediction → compute net value
       │           ├─ Tier 2: category price band (insufficient data)
       │           └─ Tier 3: unknown crop → gap_notifier → soko.gaps
       ├─ Estimate transport cost (rate band lookup)
       ├─ Derive sell signal (perishability → harvest month → trend)
       └─ Return ranked_markets + signal
```

### Coverage Gap → Retraining Flow

```
location-service (Tier 3)
  └─ gap_notifier.record_and_notify_gap()
       ├─ INSERT / UPDATE coverage_gaps (Postgres)
       └─ publish CoverageGapEvent → soko.gaps
             ├─► CoverageGapConsumer (kafka-agent) — logs for ops monitoring
             └─► (future) admin notification service

data-ingestion-service (TransactionStream)
  └─ When coverage_map.observation_count reaches MIN_OBSERVATIONS_FOR_MODEL (30)
       └─ publish RetrainRequestedEvent → soko.ml.events
             └─► price-prediction-service
                   └─ train_all_models_from_feature_store(crop, market)
```

---

## 7. Kafka Topics Reference

| Topic | Partitions | Retention | Produced by | Consumed by |
|---|---|---|---|---|
| `soko.transactions` | 6 | 7 days | order-service | TransactionStream, TransactionPriceCollector, TransactionConsumer |
| `soko.interactions` | 6 | 3 days | (future frontend) | InteractionConsumer |
| `soko.price.requests` | 3 | 1 day | kafka-agent | PriceRequestConsumer |
| `soko.price.results` | 3 | 1 day | PriceRequestConsumer | kafka-agent |
| `soko.ml.events` | 2 | 14 days | data-ingestion-service | price-prediction-service |
| `soko.gaps` | 2 | 30 days | location-service | CoverageGapConsumer |
| `soko.dlq` | 2 | 30 days | all consumers on failure | (manual remediation) |

---

## 8. Postgres Schema Summary (`soko_ml_db`)

| Table | Purpose | Key Columns |
|---|---|---|
| `farmer_features` | ML-ready farmer profiles | `farmer_id`, `crops_offered TEXT[]`, `lat`, `lng`, `avg_rating` |
| `buyer_features` | ML-ready buyer profiles | `buyer_id`, `crop_interests TEXT[]`, `total_purchases` |
| `price_observations` | Real transaction prices | `crop`, `market`, `price_ugx_per_kg`, `order_id UNIQUE` |
| `coverage_map` | Model readiness per crop/market | `is_model_ready`, `observation_count`, `last_trained_at` |
| `market_registry` | All known markets with GPS | `market_id`, `lat`, `lng`, `active` |
| `coverage_gaps` | Crops/markets with no ML data | `crop`, `priority LOW/MEDIUM/HIGH`, `gap_count` |

**Postgres trigger:** `trg_update_coverage` fires on every `price_observations` INSERT, incrementing `coverage_map.observation_count` and flipping `is_model_ready = TRUE` when count reaches `min_observations_needed` (default 30). This is the mechanism by which the system self-heals from Tier 2/3 to Tier 1 over time.

---

## 9. Redis Key Patterns and TTLs

| Key Pattern | TTL | Purpose |
|---|---|---|
| `dist:{farmer_id}:{market_id}` | 30 days | Road distance (km) from farmer to market |
| `route:{farmer_id}:{crop}` | 6 hours | Full ranked market list |
| `discover:{buyer_id}:{crop}:{radius}` | 1 hour | Nearby farmers for buyer |
| `market_registry` | 6 hours | Market list from Postgres |

---

## 10. Environment Variables Reference

See `services/soko-ml/.env.example` for the complete list with defaults and comments.

**Critical variables that must be set before production:**

| Variable | Default | Why it matters |
|---|---|---|
| `POSTGRES_PASSWORD` | `changeme` | Feature store security |
| `INTERNAL_API_KEY` | `internal-secret` | Auth between ML layer and core services |
| `USER_SERVICE_URL` | `http://user-service:8002` | Bootstrap farmer/buyer data |
| `ORDER_SERVICE_URL` | `http://order-service:8003` | Bootstrap historical price observations |
| `PRODUCE_SERVICE_URL` | `http://produce-service:8004` | Coverage map seeding |
| `GOOGLE_MAPS_API_KEY` | (empty) | Road distances; Haversine fallback used if absent |

---

## 11. Port Map

| Service | Container Port | Default Host Port |
|---|---|---|
| ml-gateway-service | 8000 | 8080 |
| price-prediction-service | 8001 | 8094 |
| recommendation-service | 8002 | 8095 |
| location-service | 8003 | 8003 |
| data-ingestion-service | 8004 | 8096 |
| Kafka (external) | 9092 | 29092 |
| Postgres (soko_ml_db) | 5432 | (not exposed) |
| Redis | 6379 | (not exposed) |

---

## 12. Startup Order (Docker Compose `depends_on`)

```
zookeeper
  └─ kafka
       └─ kafka-init (creates topics, exits)
       └─ price-prediction-service
       └─ recommendation-service
       └─ kafka-agent (waits for price + rec + data-ingestion)

soko-ml-db
  └─ db-init (applies schema.sql, exits)
  └─ data-ingestion-service
       └─ (bootstrap runs at startup if tables empty)

redis
  └─ price-prediction-service
  └─ recommendation-service
  └─ location-service

data-ingestion-service + location-service
  └─ ml-gateway-service (waits for all 4 downstream services)
       └─ kafka-agent
```

---

## 13. Known Limitations and Future Work

| Limitation | Impact | Recommended Fix |
|---|---|---|
| District centroid GPS (~20-50 km error) | Distance-ranked markets may be slightly wrong | Add `lat/lng` to `UserProfile` in user-service |
| No real-time profile push | Up to 15 min lag for updated farmer specialties | Add Kafka publisher to user-service `PUT /me` |
| Produce-service listing prices not in feature store | Farmer asking price not used in market routing | Periodic sync of active listing prices |
| Google Maps quota | Distance calls are batched and cached; if quota exhausted, Haversine is used | Monitor API quota; upgrade plan if needed |
| Prophet cold-start for new crop/market pairs | Tier 2 until 30 observations accumulate | Lower `MIN_OBSERVATIONS_FOR_MODEL` to 10 during initial rollout |
| `kafka-agent` `TransactionPriceCollector` doubles write load | Acceptable due to Postgres deduplication | Can be disabled if data-ingestion-service `TransactionStream` is confirmed reliable |

---

## 14. File Manifest (Phase 2 New / Modified Files)

### New Services
```
services/soko-ml/data-ingestion-service/
  src/
    main.py                         FastAPI app, lifespan bootstrap
    schemas.py                      Pydantic request/response models
    feature_store.py                All asyncpg DB operations
    health.py                       Health checks (DB + backend services)
    transformers/
      farmer_transformer.py         Profile → farmer_features row
      buyer_transformer.py          Profile → buyer_features row
      price_transformer.py          Transaction event → price_observations row
    clients/
      user_client.py                GET /users/farmers, /users/buyers
      order_client.py               GET /orders?status=delivered
      listing_client.py             GET /listings
    bootstrap/
      auth_bootstrap.py             Bulk farmer + buyer sync
      order_bootstrap.py            Bulk price observation sync
      listing_bootstrap.py          Coverage map seeding
      market_bootstrap.py           No-op (markets seeded in schema.sql)
    streams/
      transaction_stream.py         Kafka consumer thread
  tests/
    test_transformers.py            Pure unit tests (no DB)
    test_feature_store.py           Integration tests (skipped if DB absent)
  requirements.txt
  Dockerfile

services/soko-ml/location-service/
  src/
    main.py                         FastAPI app
    schemas.py                      Pydantic models
    market_router.py                Core routing logic
    geo_recommender.py              Buyer→farmer discovery
    google_maps_client.py           Distance Matrix API + Haversine fallback
    transport_cost.py               Rate-band cost estimation
    sell_signal.py                  GO/WAIT signal derivation
    fallback.py                     Tier 1/2/3 fallback logic
    gap_notifier.py                 Gap recording + Kafka publish
    cache.py                        Redis key patterns + helpers
  tests/
    test_market_router.py           Unit + integration tests
  requirements.txt
  Dockerfile
```

### New Infrastructure
```
services/soko-ml/db/
  schema.sql                        Postgres schema + seed data + trigger

services/soko-ml/.env.example       Updated with all Phase 2 variables
```

### Modified Existing Files
```
services/order/app/kafka_publisher.py       Added product_name field
services/order/app/routers/orders.py        Passes product_name on checkout + cancel

services/user/app/routers/profile.py        Added GET /users/buyers endpoint

services/soko-ml/shared/events.py           Added SokoTransactionEvent, CoverageGapEvent,
                                            RetrainRequestedEvent

services/soko-ml/price-prediction-service/src/predictor.py
  Added train_all_models_from_feature_store()

services/soko-ml/recommendation-service/
  src/recommender.py              ProfileStore rewritten (async reload from Postgres)
  src/main.py                     Removed CSV paths; added periodic reload
  src/feature_store_client.py     New — asyncpg reads from farmer_features/buyer_features
  src/geo_filter.py               New — Haversine pre-filter helper
  requirements.txt                Added asyncpg

services/soko-ml/ml-gateway-service/
  src/proxy.py                    Added location + ingestion circuit breakers
  src/main.py                     Added /location/*, /gaps/*, /coverage, /ingest/* routes

services/soko-ml/kafka-agent/
  src/agent.py                    Added CoverageGapConsumer + TransactionPriceCollector
  src/consumers/coverage_gap_consumer.py    New
  src/consumers/transaction_price_collector.py  New

services/soko-ml/docker-compose.yml
  Added: soko-ml-db, db-init, data-ingestion-service, location-service
  Added: soko.gaps Kafka topic
  Updated: ml-gateway depends_on, kafka-agent depends_on + DATA_INGESTION_SERVICE_URL
  Updated: volumes (soko_ml_db_data)

Makefile (root)
  Added: db-up, db-shell, db-reset, cold-start
  Added: ingest-bootstrap, ingest-status, gaps-summary, gaps-reset
  Added: dev-location, dev-ingest, logs-location, logs-ingest
  Added: test-location, test-ingest
  Added: smoke-route, smoke-discover, smoke-fallback, smoke-tier3, smoke-ingest
  Added: INGEST_VENV, LOC_VENV
  Updated: install, infra-up, infra-down, kafka-topics, health, clean
```
