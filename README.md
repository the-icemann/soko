# Soko — Digital Agricultural Marketplace

A production-grade microservices platform connecting Ugandan farmers and buyers. Farmers list produce, buyers place orders, and the system handles payments, messaging, notifications, and price intelligence — all through a single Nginx API gateway.

The platform is split into two independent but integrated stacks:

- **Core stack** — transactional services (auth, users, produce, orders, payments, messaging, blog, USSD)
- **ML stack** — price prediction and farmer/buyer matching (`services/soko-ml/`)

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Core Services](#core-services)
3. [ML Layer](#ml-layer)
4. [How the Two Stacks Interact](#how-the-two-stacks-interact)
5. [API Reference](#api-reference)
6. [User Flows](#user-flows)
7. [Event System](#event-system)
8. [Getting the ML Stack Running](#getting-the-ml-stack-running)
9. [Running Tests](#running-tests)
10. [Environment Variables](#environment-variables)
11. [Project Structure](#project-structure)

---

## Architecture Overview

```
 ┌────────────────────────────────────────────────────────────────┐
 │                      CLIENT LAYER                              │
 │          Web App · Mobile App · USSD Handsets                  │
 └───────────────────────────┬────────────────────────────────────┘
                             │ HTTP / WebSocket
                             ▼
 ┌────────────────────────────────────────────────────────────────┐
 │               NGINX API GATEWAY  :80                           │
 │   Rate limiting (30 req/min) · CORS · JWT subrequest auth      │
 │   Routes: /auth/ /users/ /listings/ /orders/ /payments/        │
 │           /message/ /notifications/ /posts/ /ussd/             │
 │           /recommendations/                                     │
 └──┬────┬────┬────┬────┬────┬────┬────┬────┬────────────────────┘
    │    │    │    │    │    │    │    │    │
    ▼    ▼    ▼    ▼    ▼    ▼    ▼    ▼    ▼
  Auth User Prod Ord  Pay  Msg  Notif Blog USSD  Rec
 :8001:8002:8003:8004:8005:8006:8007 :8008:8009 :8010
    │    │    │    │    │    │    │    │    │     │
    └────┴────┴────┴────┴────┴────┴────┴────┴─────┘
                         │
              ┌──────────┴──────────┐
              │                     │
              ▼                     ▼
        PostgreSQL              RabbitMQ :5672
     (one DB per service)     (async events between
                               core services)

 ┌────────────────────────────────────────────────────────────────┐
 │                      ML STACK  (services/soko-ml/)             │
 │                                                                │
 │   ml-gateway-service :8000  ←  called by core services        │
 │        │                   ←  single entry point              │
 │        ├──► price-prediction-service :8001                     │
 │        │         Prophet .pkl models · Redis cache 24h         │
 │        └──► recommendation-service :8002                       │
 │                  Content scoring · Redis cache 1h              │
 │                                                                │
 │   kafka-agent  (no HTTP port)                                  │
 │        ├── consumes: soko.transactions                         │
 │        ├── consumes: soko.interactions                         │
 │        ├── consumes: soko.price.requests                       │
 │        └── produces: soko.price.results · soko.dlq             │
 │                                                                │
 │   Infrastructure: Kafka · Zookeeper · Redis                    │
 └────────────────────────────────────────────────────────────────┘
```

### Key design rules

- Every external request enters through **Nginx only** — services are never exposed directly.
- Every call to the ML layer enters through **ml-gateway-service only** — price and recommendation services are never called directly by core services.
- Auth is enforced at the gateway via an internal `/verify-token` subrequest to the Auth service before the request reaches any protected service.
- Core services communicate asynchronously via **RabbitMQ**. The ML layer uses **Kafka** for its own event backbone.

---

## Core Services

### Auth Service — `:8001`

**Responsibility:** Identity and access. Issues JWTs on login, exposes `/verify-token` which Nginx calls internally on every protected route to validate tokens and inject `X-User-Id`, `X-User-Role`, `X-User-Email` headers downstream.

**Nginx route:** `/auth/` and `/oauth/` (public — no auth guard)

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Register with role `farmer` or `buyer` |
| POST | `/auth/login` | Login → JWT access token |
| GET | `/auth/me` | Current user info (JWT required) |
| POST | `/auth/refresh` | Refresh an expiring token |
| GET | `/verify-token` | Internal — called by Nginx, not clients |

---

### User Service — `:8002`

**Responsibility:** User profiles and account management. Receives the authenticated user context (`X-User-Id`, `X-User-Role`) from Nginx — it never validates tokens itself.

**Nginx route:** `/users/` (JWT required)

---

### Produce Service — `:8003`

**Responsibility:** Produce listings — creation, search, stock management. Farmers create listings; buyers browse them. Publishes `produce.listed` events to RabbitMQ so the recommendation service can index new listings.

**Nginx route:** `/listings/` (JWT required, 20 MB upload limit for images)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/listings/` | farmer | Create a listing |
| GET | `/listings/` | JWT | Browse / search (filter by category, district, price) |
| GET | `/listings/{id}` | JWT | Single listing |
| PATCH | `/listings/{id}` | farmer | Update own listing |
| DELETE | `/listings/{id}` | farmer | Remove listing |
| PATCH | `/listings/{id}/reduce-stock` | internal | Called by Order service on order placement |

---

### Order Service — `:8004`

**Responsibility:** Order lifecycle from placement to completion. Buyers place orders against listings; farmers accept or reject; status advances through a defined state machine.

**Nginx route:** `/orders/` (JWT required)

**Order state machine:**
```
placed → pending
          ├─► confirmed  (farmer accepts)
          │       └─► completed  (farmer marks done → review unlocked)
          ├─► rejected   (farmer declines)
          └─► cancelled  (buyer withdraws)
```

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/orders/` | buyer | Place order |
| GET | `/orders/` | buyer | List own orders |
| PATCH | `/orders/{id}/cancel` | buyer | Cancel a pending order |
| POST | `/orders/{id}/review` | buyer | Review a completed order |
| GET | `/orders/incoming/` | farmer | Orders for farmer's produce |
| PATCH | `/orders/{id}/status` | farmer | Advance order status |

---

### Payment Service — `:8005`

**Responsibility:** Payment initiation and reconciliation via PesaPal (MTN Mobile Money / Airtel Money). The `/webhook/` endpoint is public so PesaPal can POST payment confirmations without authentication.

**Nginx routes:** `/payments/` (JWT required) · `/webhook/` (public)

---

### Message Service — `:8006`

**Responsibility:** Real-time direct messaging between farmers and buyers over WebSocket. The WebSocket upgrade (`/message/ws/`) bypasses the JWT subrequest — token is validated by the service itself on connection.

**Nginx routes:** `/message/` (JWT required) · `/message/ws/` (WebSocket, service-auth)

---

### Notification Service — `:8007`

**Responsibility:** Push notifications delivered in real-time over WebSocket. Consumes events from RabbitMQ (order updates, payment confirmations) and pushes them to connected clients.

**Nginx routes:** `/notifications/` (JWT required) · `/notifications/ws/` (WebSocket, service-auth)

---

### Blog Service — `:8008`

**Responsibility:** Agri-knowledge articles and market commentary. Supports image uploads up to 10 MB.

**Nginx route:** `/posts/` (JWT required, 10 MB upload limit)

---

### USSD Service — `:8009`

**Responsibility:** USSD session handler for feature-phone users. Completely public — USSD networks don't carry HTTP auth headers. Allows farmers with basic handsets to check prices and receive order notifications.

**Nginx route:** `/ussd/` (public — no auth guard)

---

### Recommendation Service — `:8010`

**Responsibility:** Personalised produce feed for buyers based on order history, category preferences, and produce quality scores. Consumes `produce.listed`, `order.placed`, and `quality.scored` events from RabbitMQ to keep its index fresh. This is the **existing** rule-based recommendation service, separate from the ML farmer/buyer matching layer.

**Nginx route:** `/recommendations/` (public)

| Method | Path | Description |
|---|---|---|
| GET | `/recommendations/` | Personalised feed for authenticated buyer |
| GET | `/recommendations/produce/{id}/score` | Quality score for a listing |

---

## ML Layer

The ML layer lives in `services/soko-ml/` and runs as a **separate Docker Compose stack**. It has four services of its own, plus Kafka and a dedicated Redis instance.

### ml-gateway-service — `:8000`

The single entry point for all ML capabilities. No core service should ever call the price or recommendation ML services directly — they call this gateway, which adds:

- **Request logging** — service name, endpoint, latency, cache hit/miss
- **Circuit breaking** — if a downstream ML service is unreachable after 3 retries, returns a graceful fallback response instead of propagating a 500
- **Health aggregation** — `GET /health` polls all downstream services and returns a combined status

| Gateway endpoint | Proxied to |
|---|---|
| `POST /price/predict` | price-prediction-service `/predict` |
| `GET /price/markets` | price-prediction-service `/markets` |
| `GET /price/crops` | price-prediction-service `/crops` |
| `GET /recommend/farmers-for-buyer/{buyer_id}` | recommendation-service |
| `GET /recommend/buyers-for-farmer/{farmer_id}` | recommendation-service |
| `GET /health` | aggregated from all downstream |

---

### price-prediction-service — `:8001` (ML stack internal)

Serves 4-week price forecasts per market–crop pair in UGX using pre-trained **Prophet** models.

- Loads `.pkl` model files from `models/` at startup (one model per market–crop pair, 48 total)
- Checks **Redis** on every request (`price:v1:{market}:{crop}:{weeks}`, TTL 24 h)
- Falls back to Uganda seasonal heuristics if no model file is present (always responds)
- Publishes a `price.predicted` event to `soko.price.results` after every inference
- Consumes `soko.price.requests` for async batch prediction jobs

**Supported markets:** Kisenyi_Kampala · Gulu · Mbarara · Mbale · Lira · Masaka

**Supported crops:** maize_grain · yellow_beans · irish_potatoes · tomatoes · matoke · cassava_chips · sorghum · millet

---

### recommendation-service — `:8002` (ML stack internal)

Recommends high-performing farmers to buyers and vice versa using a **weighted content-based scoring model** enriched in real-time from Kafka interaction events.

- Loads `farmers.csv` (200 profiles) and `buyers.csv` (300 profiles) at startup
- Scores farmer–buyer compatibility on crop overlap, market overlap, rating, fulfillment rate
- Boosts scores dynamically from Kafka events: view +0.02, inquiry +0.05, purchase +0.10, rating +0.04
- Caches results in Redis (`rec:farmers:{buyer_id}:{top_n}`, TTL 1 h)
- Invalidates cache when a relevant interaction event arrives

**Scoring weights — farmers for buyer:**

| Signal | Weight |
|---|---|
| Crop overlap (buyer wants ∩ farmer offers) | 0.35 |
| Market overlap | 0.20 |
| Farmer average rating (normalised / 5.0) | 0.20 |
| Fulfillment rate | 0.15 |
| Interaction boost (Kafka, additive, capped +0.20) | additive |

**Scoring weights — buyers for farmer:**

| Signal | Weight |
|---|---|
| Crop overlap (farmer offers ∩ buyer wants) | 0.35 |
| Market overlap | 0.20 |
| Payment reliability | 0.25 |
| Purchase volume (normalised by dataset max) | 0.20 |

---

### kafka-agent

Long-running Python process — no HTTP port. Bridges the core Soko event stream with the ML layer.

| Consumes | Event types | Action |
|---|---|---|
| `soko.transactions` | `purchase_completed`, `order_cancelled` | Publishes enriched event to `soko.interactions` |
| `soko.interactions` | `farmer_viewed`, `buyer_inquiry`, `rating_submitted` | Logged and forwarded; recommendation-service has its own consumer |
| `soko.price.requests` | `price_prediction_requested` | Calls price-prediction-service, publishes result to `soko.price.results` |
| `soko.ml.events` | `retrain_requested`, `model_deployed` | Logged, triggers downstream refresh |

Failed messages go to `soko.dlq` with full error context.

---

### ML Infrastructure

| Component | Image | Config |
|---|---|---|
| Kafka | `confluentinc/cp-kafka:7.5.0` | 1 broker, auto-topic creation off |
| Zookeeper | `confluentinc/cp-zookeeper:7.5.0` | — |
| Redis | `redis:7-alpine` | 256 MB max, `allkeys-lru` eviction |

**Kafka topics:**

| Topic | Partitions | Retention | Purpose |
|---|---|---|---|
| `soko.transactions` | 6 | 7 days | Purchase and order events |
| `soko.interactions` | 6 | 3 days | Views, inquiries, ratings |
| `soko.price.requests` | 3 | 1 day | Async prediction requests |
| `soko.price.results` | 3 | 1 day | Async prediction results |
| `soko.ml.events` | 2 | 14 days | Model lifecycle events |
| `soko.dlq` | 2 | 30 days | Dead-letter queue |

**Redis cache keys:**

| Key pattern | TTL | Stores |
|---|---|---|
| `price:v1:{market}:{crop}:{weeks}` | 24 h | Full prediction response |
| `rec:farmers:{buyer_id}:{top_n}` | 1 h | Recommended farmers list |
| `rec:buyers:{farmer_id}:{top_n}` | 1 h | Recommended buyers list |
| `model:meta:{market}:{crop}` | 7 days | Model training date, MAPE |

---

## How the Two Stacks Interact

```
Core Soko stack                         ML stack
─────────────────                       ─────────────────────────────────────
                                        ml-gateway-service :8000
recommendation_service :8010 ──HTTP──►  GET /recommend/farmers-for-buyer/
                                        GET /recommend/buyers-for-farmer/

produce_service :8003        ──HTTP──►  POST /price/predict
                                        (surface price context on listing pages)

order_service :8004          ──Kafka──► soko.transactions
                                        (kafka-agent listens, enriches to
                                         soko.interactions for rec boosts)

ussd_service :8009           ──HTTP──►  POST /price/predict
                                        (price checks on feature phones)
```

The ML stack is intentionally decoupled — the core stack calls `ml-gateway-service` over HTTP and publishes to Kafka topics. The ML layer never calls back into the core stack.

---

## API Reference

All requests enter via `http://localhost:80` through Nginx. Protected routes require an `Authorization: Bearer <token>` header.

### Auth

```http
POST /auth/register         { "email": "...", "password": "...", "role": "farmer|buyer" }
POST /auth/login            { "email": "...", "password": "..." }  →  { "access_token": "..." }
GET  /auth/me               Authorization: Bearer <token>
POST /auth/refresh          Authorization: Bearer <token>
```

### Produce

```http
GET  /listings/             ?category=grains&district=Kampala&min_price=500&max_price=2000
POST /listings/             { "title", "category", "price_per_kg", "quantity_kg", "district" }
GET  /listings/{id}
PATCH /listings/{id}
DELETE /listings/{id}
```

### Orders

```http
POST  /orders/              { "listing_id": "...", "quantity_kg": 100 }
GET   /orders/
PATCH /orders/{id}/cancel
POST  /orders/{id}/review   { "rating": 5, "comment": "..." }
GET   /orders/incoming/                                (farmer)
PATCH /orders/{id}/status   { "new_status": "confirmed|completed|rejected" }  (farmer)
```

### Payments

```http
POST /payments/initiate     { "order_id": "...", "phone": "256700000000" }
GET  /payments/{id}/status
POST /webhook/pesapal       (PesaPal callback — public)
```

### Messaging & Notifications

```http
GET  /message/              List conversations
POST /message/              { "recipient_id": "...", "body": "..." }
WS   /message/ws/{token}    Real-time message stream

GET  /notifications/        List notifications
WS   /notifications/ws/{token}  Real-time push stream
```

### ML (via ml-gateway-service — not through Nginx)

```http
POST http://localhost:8000/price/predict
     { "market": "Kisenyi_Kampala", "crop": "maize_grain", "weeks_ahead": 4 }

GET  http://localhost:8000/recommend/farmers-for-buyer/B0001?top_n=5
GET  http://localhost:8000/recommend/buyers-for-farmer/F0001?top_n=5
GET  http://localhost:8000/health
GET  http://localhost:8000/price/markets
GET  http://localhost:8000/price/crops
```

---

## User Flows

### Farmer

```
1. POST /auth/register  { role: "farmer" }
2. POST /auth/login     → JWT
3. POST /listings/      List produce with price and quantity
4. GET  /orders/incoming/    See buyer orders
5. PATCH /orders/{id}/status  { "new_status": "confirmed" }
6. PATCH /orders/{id}/status  { "new_status": "completed" }
```

### Buyer

```
1. POST /auth/register  { role: "buyer" }
2. POST /auth/login     → JWT
3. GET  /listings/      Browse produce (filter by district, crop, price)
4. POST /orders/        Place order
5. POST /payments/initiate   Pay via Mobile Money
6. POST /orders/{id}/review  Rate after completion
7. GET  /recommendations/    See personalised feed
```

### Price check (USSD — no smartphone needed)

```
1. Farmer dials USSD short code
2. ussd_service calls ml-gateway-service POST /price/predict
3. 4-week maize price forecast returned as plain text to handset
```

---

## Event System

### RabbitMQ — Core stack events

| Event | Publisher | Consumers | Effect |
|---|---|---|---|
| `produce.listed` | Produce | Recommendation | Index new listing |
| `order.placed` | Order | Recommendation, Notification | Update feed; notify farmer |
| `order.completed` | Order | Notification | Notify buyer |
| `quality.scored` | Order | Produce, Recommendation | Update avg_rating; re-rank |
| `payment.confirmed` | Payment | Notification, Order | Unlock fulfillment |

All queues are durable. Payload schemas are in [CONTRACTS.md](CONTRACTS.md).

### Kafka — ML layer events

| Event | Flow |
|---|---|
| `purchase_completed` | order_service → `soko.transactions` → kafka-agent → `soko.interactions` → recommendation-service (boost +0.10) |
| `price_prediction_requested` | Any service → `soko.price.requests` → kafka-agent → price-prediction-service → `soko.price.results` |
| `farmer_viewed` | recommendation_service → `soko.interactions` → recommendation-service (boost +0.02) |

---

## Getting the ML Stack Running

All commands run from the **project root**. Prerequisites: Docker 20+, Python 3.11, Make.

### Step 1 — Install Python dependencies

```bash
make install
```

Creates a `.venv` inside each ML service folder and installs its `requirements.txt`. Prophet pulls in `pystan` — expect 3–5 minutes on first run.

### Step 2 — Generate synthetic training data

```bash
make generate-data
```

Writes three files to `services/soko-ml/recommendation-service/data/raw/`:

- `crop_prices_raw.csv` — 4 years of weekly UGX prices, 6 markets × 8 crops (~12,000 rows)
- `farmers.csv` — 200 synthetic farmer profiles with crop/market coverage, rating, fulfillment rate
- `buyers.csv` — 300 synthetic buyer profiles with preferred crops, markets, payment reliability

Verify the output:
```bash
wc -l services/soko-ml/recommendation-service/data/raw/*.csv
# expect: 12289 crop_prices_raw.csv  |  201 farmers.csv  |  301 buyers.csv
```

### Step 3 — Train the Prophet models

```bash
make train
```

Trains 48 Prophet models (6 markets × 8 crops) with Uganda bimodal seasonality and saves `.pkl` files to `services/soko-ml/price-prediction-service/models/`. Takes 5–15 minutes depending on CPU.

> **You can skip this step.** The price-prediction-service has a built-in seasonal fallback that always returns a valid forecast. Skip `make train` for a faster first boot and run it later in the background.

Verify:
```bash
ls services/soko-ml/price-prediction-service/models/ | wc -l
# expect: 48
```

### Step 4 — Start the ML stack

```bash
make up
```

Builds and starts 8 containers: Zookeeper, Kafka, kafka-init (topic creation), Redis, price-prediction-service, recommendation-service, ml-gateway-service, kafka-agent.

Watch the startup log until you see these three lines, then proceed:
```bash
make logs
# Look for:
# soko-ml-kafka-init  | All Kafka topics created.
# soko-ml-rec         | {"event": "recommendation_service_started", "farmers": 200, "buyers": 300}
# soko-ml-gateway     | {"event": "gateway_started"}
```
Kafka takes ~30 seconds to elect a leader — the ML services will retry automatically.

### Step 5 — Health check

```bash
make health
```

All three services must return `"ok"` before proceeding:

```json
=== ML Gateway ===
{
    "gateway": "ok",
    "services": { "price-prediction": "ok", "recommendation": "ok" },
    "circuit_breakers": { "price-prediction": "closed", "recommendation": "closed" }
}
=== Price Service ===
{ "status": "ok", "service": "price-prediction-service", "models_loaded": 48 }
=== Recommendation Service ===
{ "status": "ok", "service": "recommendation-service", "farmers_loaded": 200, "buyers_loaded": 300 }
```

### Step 6 — Run unit tests (no Docker required)

```bash
make test
```

Runs pytest across all three FastAPI services — pure logic tests, no Redis or Kafka needed:

```bash
make test-price    # 11 tests — fallback predict, base UGX prices, model registry
make test-rec      # 14 tests — scoring, ranking, interaction boosts, cache invalidation
make test-gateway  # 11 tests — proxy, circuit breaker, health aggregation
```

### Step 7 — Smoke test (full round trip)

```bash
make smoke-test
```

Fires three live HTTP calls through the gateway and prints the JSON. On the second run, the price response returns `"cached": true` — confirming Redis is working.

### What to look for if something fails

| Symptom | Cause | Fix |
|---|---|---|
| `"recommendation": "unreachable"` in health | CSVs not generated | Run `make generate-data` |
| `"models_loaded": 0` in price health | No `.pkl` files | Run `make train`, or rely on fallback |
| Gateway returns `503` | Service startup race | Wait 30 s; check `make logs` |
| `kafka-init` exits immediately | Kafka not ready | It restarts automatically; wait |
| `"cached": true` on first call | Stale Redis from prior run | `make redis-cli` → `FLUSHDB` |

---

## Makefile Reference

All targets run from the project root.

### Setup

| Command | What it does |
|---|---|
| `make install` | Create `.venv` in each ML service and install deps |
| `make generate-data` | Write synthetic CSVs to `services/soko-ml/recommendation-service/data/raw/` |
| `make train` | Train 48 Prophet models → `services/soko-ml/price-prediction-service/models/` |

### Development (local, no Docker)

| Command | What it does |
|---|---|
| `make dev` | Full ML stack with hot reload (docker-compose.dev.yml) |
| `make dev-price` | Run price-prediction-service locally, port 8001 |
| `make dev-rec` | Run recommendation-service locally, port 8002 |
| `make dev-gateway` | Run ml-gateway-service locally, port 8000 |

### Infrastructure

| Command | What it does |
|---|---|
| `make infra-up` | Start Redis + Kafka + Zookeeper only (no ML services) |
| `make infra-down` | Stop infrastructure containers |
| `make kafka-topics` | Re-create all Kafka topics (idempotent) |
| `make kafka-ui` | List all Kafka topics in terminal |
| `make redis-cli` | Open Redis CLI in the running container |

### Production

| Command | What it does |
|---|---|
| `make up` | `docker-compose up --build -d` — full ML stack |
| `make down` | Stop all ML containers |
| `make restart` | `down` then `up` |
| `make logs` | Follow logs for all ML services |
| `make logs-price` | Follow price-prediction-service logs only |
| `make logs-rec` | Follow recommendation-service logs only |
| `make logs-gateway` | Follow ml-gateway-service logs only |
| `make logs-agent` | Follow kafka-agent logs only |

### Testing

| Command | What it does |
|---|---|
| `make test` | Run all pytest suites |
| `make test-price` | price-prediction-service tests only |
| `make test-rec` | recommendation-service tests only |
| `make test-gateway` | ml-gateway-service tests only |

### Health & Smoke

| Command | What it does |
|---|---|
| `make health` | `curl` all `/health` endpoints and print results |
| `make smoke-test` | End-to-end: price prediction + farmer recs + buyer recs |

### Cleanup

| Command | What it does |
|---|---|
| `make clean` | Remove `__pycache__`, `.pyc`, venvs, generated CSVs |
| `make clean-models` | Remove trained `.pkl` model files |
| `make clean-docker` | `docker-compose down -v --rmi all` — full wipe |

---

## Running Tests

### Core stack integration tests

Start the core stack first:
```bash
docker compose up --build -d
```

Then run the integration suite (hits real services, no mocks):
```bash
pip install pytest httpx
pytest tests/integration/ -v
```

Covers: health checks, auth, user profiles, produce listings, order placement, stock reduction, reviews, recommendation event propagation.

### ML stack unit tests

No Docker required — runs against local code:
```bash
make install   # only needed once
make test
```

---

## Environment Variables

### Core stack (set in `docker-compose.yml`)

| Variable | Services | Description |
|---|---|---|
| `DATABASE_URL` | all | PostgreSQL connection string |
| `RABBITMQ_URL` | all except auth | `amqp://guest:guest@rabbitmq:5672/` |
| `SECRET_KEY` | auth + JWT-validating services | JWT signing key |
| `ALGORITHM` | same | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | auth | Token lifetime (default 30) |
| `PRODUCE_SERVICE_URL` | order | `http://produce_service:8003` |

### ML stack (template in `services/soko-ml/.env.example`)

| Variable | Default | Description |
|---|---|---|
| `REDIS_HOST` | `redis` | ML Redis hostname |
| `REDIS_PORT` | `6379` | |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | |
| `MODEL_DIR` | `/app/models` | Path to `.pkl` files inside container |
| `FARMERS_DATA_PATH` | `/app/data/raw/farmers.csv` | |
| `BUYERS_DATA_PATH` | `/app/data/raw/buyers.csv` | |
| `PRICE_CACHE_TTL_SECONDS` | `86400` | 24 hours |
| `REC_CACHE_TTL_SECONDS` | `3600` | 1 hour |
| `DEFAULT_TOP_N` | `5` | Default recommendation count |
| `LOG_LEVEL` | `INFO` | `DEBUG` for development |

---

## Project Structure

```
soko/
├── Makefile                          ← All ML commands (run from here)
├── docker-compose.yml                ← Core Soko stack
├── nginx/
│   └── nginx.conf                    ← API gateway routing + auth subrequests
├── services/
│   ├── auth/                         ← JWT auth, /verify-token
│   ├── user/                         ← User profiles          :8002
│   ├── produce/                      ← Listings, stock        :8003
│   ├── order/                        ← Orders, reviews        :8004
│   ├── payment/                      ← PesaPal integration    :8005
│   ├── message/                      ← WebSocket messaging    :8006
│   ├── notification/                 ← WebSocket push         :8007
│   ├── blog/                         ← Articles               :8008
│   ├── ussd/                         ← Feature-phone access   :8009
│   ├── recommendation/               ← RabbitMQ-driven feed   :8010
│   └── soko-ml/                      ← ML stack (own compose)
│       ├── docker-compose.yml
│       ├── docker-compose.dev.yml
│       ├── .env.example
│       ├── shared/
│       │   └── events.py             ← Kafka event dataclasses
│       ├── price-prediction-service/ ← Prophet + Redis + Kafka :8001
│       │   ├── src/
│       │   │   ├── main.py
│       │   │   ├── predictor.py
│       │   │   ├── cache.py
│       │   │   ├── kafka_producer.py
│       │   │   └── schemas.py
│       │   ├── models/               ← .pkl files (gitignored)
│       │   └── tests/
│       ├── recommendation-service/   ← Content scoring + Redis :8002
│       │   ├── src/
│       │   │   ├── main.py
│       │   │   ├── recommender.py
│       │   │   ├── interaction_store.py
│       │   │   ├── cache.py
│       │   │   ├── kafka_consumer.py
│       │   │   └── schemas.py
│       │   ├── data/raw/             ← farmers.csv, buyers.csv
│       │   └── tests/
│       ├── ml-gateway-service/       ← Proxy + circuit breaker :8000
│       │   ├── src/
│       │   │   ├── main.py
│       │   │   ├── proxy.py
│       │   │   └── logger.py
│       │   └── tests/
│       ├── kafka-agent/              ← Event backbone (no HTTP)
│       │   ├── src/
│       │   │   ├── agent.py
│       │   │   ├── consumers/
│       │   │   ├── producers/
│       │   │   └── dlq.py
│       │   └── tests/
│       └── data-generator/           ← One-shot CSV + model data
│           ├── generate_prices.py
│           └── generate_profiles.py
└── tests/
    └── integration/                  ← Core stack integration tests
```

Each core service follows the same internal layout:
```
service/
├── Dockerfile
├── requirements.txt
└── app/
    ├── main.py          ← FastAPI app + lifespan
    ├── config.py        ← pydantic-settings
    ├── database.py      ← SQLAlchemy engine
    ├── dependencies.py  ← JWT auth
    ├── messaging.py     ← RabbitMQ publisher / consumer
    ├── schemas.py       ← Pydantic models
    ├── models/          ← SQLAlchemy ORM
    └── routers/         ← Route handlers
```

---

## Known Limitations

- **Alembic not wired** — schema changes require dropping the affected DB volume
- **Shared JWT secret** — all services share one key; use a secrets manager in production
- **`/listings/{id}/reduce-stock` is unauthenticated** — secure with an internal API key in production
- **No password reset** — requires an email provider
- **ML stack is a separate compose** — it does not share the core stack's network or Redis; the two stacks communicate over localhost ports in development and would use a shared Docker network or service mesh in production
