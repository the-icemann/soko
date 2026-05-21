# Soko — Digital Agricultural Marketplace

A production-grade microservices platform connecting Ugandan farmers and buyers. Farmers list produce, buyers place orders, the system handles payments, messaging, and notifications — and a dedicated ML layer delivers personalised recommendations and market price forecasts to every authenticated user.

The platform runs as two independent but integrated Docker Compose stacks:

- **Core stack** — transactional services: auth, users, produce, orders, payments, messaging, notifications, blog, USSD
- **ML stack** — intelligence layer: price prediction, personalised recommendations, market routing, data ingestion, Kafka event backbone

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Core Services](#core-services)
3. [ML Layer](#ml-layer)
4. [Auth → ML: The Authenticated Recommendation Flow](#auth--ml-the-authenticated-recommendation-flow)
5. [How the Two Stacks Integrate](#how-the-two-stacks-integrate)
6. [API Reference](#api-reference)
7. [User Flows](#user-flows)
8. [Event System (Kafka)](#event-system-kafka)
9. [Getting Started](#getting-started)
10. [Makefile Reference](#makefile-reference)
11. [Environment Variables](#environment-variables)
12. [Project Structure](#project-structure)
13. [Production Bug Report](#production-bug-report)
14. [Known Limitations](#known-limitations)
15. [Port Reference & Network Isolation](#port-reference--network-isolation)

---

## Architecture Overview

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                          CLIENT LAYER                                │
 │              Web App · Mobile App · USSD Handsets                    │
 └───────────────────────────────┬──────────────────────────────────────┘
                                 │ HTTP / WebSocket
                                 ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │                    NGINX API GATEWAY  :80                            │
 │   Rate limiting (30 req/min) · CORS · JWT subrequest auth            │
 │                                                                      │
 │  /auth/ /oauth/           → auth_service        (public)             │
 │  /users/                  → user_service        (JWT required)       │
 │  /listings/               → produce_service     (JWT optional)       │
 │  /orders/                 → order_service       (JWT required)       │
 │  /payments/ /webhook/     → payment_service     (JWT / public)       │
 │  /message/ /message/ws/   → message_service     (JWT / WS)           │
 │  /notifications/ /ws/     → notification_service(JWT / WS)           │
 │  /posts/                  → blog_service        (JWT optional)       │
 │  /ussd/                   → ussd_service        (public)             │
 │  /ml/price/               → ml-gateway          (public)             │
 │  /ml/recommend/           → ml-gateway          (JWT required) ◄─┐   │
 │  /recommendations/        → ml-gateway          (JWT required) ──┘   │
 └──┬────┬────┬────┬────┬────┬────┬────┬──────────────┬────────────────┘
    │    │    │    │    │    │    │    │              │
    ▼    ▼    ▼    ▼    ▼    ▼    ▼    ▼              ▼
  ── CORE STACK (internal container ports) ──────────────────────────
  :8001 :8002 :8003 :8004 :8005 :8006 :8007 :8008         ML stack
  Auth  User  Prod   Ord   Pay   Msg   Not   Blog  USSD   (see below)
                                                   :8009

    Each service owns its own PostgreSQL database.
    Core services share one Redis instance for caching.
    Order service publishes to Kafka → ML layer consumes.

 ┌──────────────────────────────────────────────────────────────────────┐
 │                 ML STACK  (services/soko-ml/)                        │
 │                                                                      │
 │  nginx ──► ml-gateway-service (container port :8000 → host port :8080) │
 │               │  circuit breakers · request logging · fallbacks      │
 │               ├──► price-prediction-service  (:8001)                 │
 │               │         Prophet .pkl models · Redis 24h cache        │
 │               ├──► recommendation-service    (:8002)                 │
 │               │         Content scoring · Postgres profiles          │
 │               │         Redis 1h cache · Kafka interaction boosts    │
 │               ├──► location-service           (:8003)                │
 │               │         Market routing · Haversine distance          │
 │               └──► data-ingestion-service     (:8004)                │
 │                         Bootstrap profiles from user-service         │
 │                         Kafka transaction → price observations       │
 │                                                                      │
 │  kafka-agent  (no HTTP port)                                         │
 │       ├── soko.transactions  → soko.interactions  (boost pipeline)   │
 │       ├── soko.price.requests → price-prediction → soko.price.results│
 │       └── soko.gaps (coverage gap monitoring)                        │
 │                                                                      │
 │  Kafka · Zookeeper · Redis · PostgreSQL (soko_ml_db)                 │
 └──────────────────────────────────────────────────────────────────────┘
```

### Key design rules

- Every external request enters through **Nginx only** — core services are never exposed directly on the public network.
- Every call to the ML intelligence layer goes through **ml-gateway-service only** — downstream ML services are internal.
- JWT authentication is enforced at the Nginx gateway via an internal `/_verify_token` subrequest to the auth service. Validated user identity (`X-User-Id`, `X-User-Role`) is injected as headers into every downstream service.
- The recommendation service enforces that a user can only request recommendations for their own account ID — the JWT-derived `X-User-Id` is compared against the path parameter on every request.
- The two stacks communicate over the `soko-ml-bridge` Docker network and the `soko.transactions` Kafka topic.

---

## Port Reference & Network Isolation

### Docker Network Topology

Soko runs across three distinct Docker networks to enforce hard isolation boundaries:

| Network | Belongs To | Purpose |
|---|---|---|
| `soko_net` | Core stack | Internal mesh for all core services + Nginx |
| `soko-ml-network` | ML stack | Internal mesh for all ML services |
| `soko-ml-bridge` | Both stacks | Shared bridge linking Nginx ↔ ml-gateway-service |

```
 ┌─────────────────────────────────────────────────────────────────────┐
 │  soko_net (core stack)                                              │
 │   nginx · auth · user · produce · order · payment                  │
 │   message · notification · blog · ussd · redis · postgres×9        │
 └────────────────────────┬────────────────────────────────────────────┘
                          │ soko-ml-bridge
 ┌────────────────────────▼────────────────────────────────────────────┐
 │  soko-ml-network (ML stack)                                         │
 │   ml-gateway · price-prediction · recommendation · location         │
 │   data-ingestion · kafka-agent · kafka · zookeeper · redis-ml       │
 │   soko-ml-db (PostgreSQL feature store)                             │
 └─────────────────────────────────────────────────────────────────────┘
```

Core services on `soko_net` **cannot** directly address ML services on `soko-ml-network`. The only cross-stack paths are:

1. `Nginx → ml-gateway-service` over `soko-ml-bridge` (HTTP)
2. `order-service → Kafka → kafka-agent` over `soko-ml-bridge` (events)

### Complete Port Mapping Table

| Service | Container Port | Host Port | Network | Purpose |
|---|---|---|---|---|
| **CORE STACK** | | | | |
| nginx (API gateway) | 80 | 80 | `soko_net` + `soko-ml-bridge` | All public traffic entry point |
| auth-service | 8001 | — | `soko_net` | JWT issue & validation |
| user-service | 8002 | — | `soko_net` | User profiles |
| produce-service | 8003 | — | `soko_net` | Listings |
| order-service | 8004 | — | `soko_net` | Order lifecycle + Kafka pub |
| payment-service | 8005 | — | `soko_net` | PesaPal integration |
| message-service | 8006 | — | `soko_net` | WebSocket messaging |
| notification-service | 8007 | — | `soko_net` | WebSocket push |
| blog-service | 8008 | — | `soko_net` | Blog posts |
| ussd-service | 8009 | — | `soko_net` | Africa's Talking USSD |
| core PostgreSQL×9 | 5432 | — | `soko_net` | Per-service databases |
| core Redis | 6379 | — | `soko_net` | Shared caching |
| **ML STACK** | | | | |
| ml-gateway-service | 8000 | **8080** | `soko-ml-network` + `soko-ml-bridge` | ML traffic router, circuit breakers |
| price-prediction-service | 8001 | 8094 (dev only) | `soko-ml-network` | Prophet forecast models |
| recommendation-service | 8002 | 8095 (dev only) | `soko-ml-network` | Content scoring + Kafka boosts |
| location-service | 8003 | 8003 | `soko-ml-network` | Market routing, Haversine |
| data-ingestion-service | 8004 | 8096 (dev only) | `soko-ml-network` | Feature store bootstrap |
| **INFRASTRUCTURE (ML stack)** | | | | |
| Kafka | 9092 | — | `soko-ml-network` | Event broker (internal) |
| Zookeeper | 2181 | — | `soko-ml-network` | Kafka coordination |
| ML Redis | 6379 | — | `soko-ml-network` | ML service caching |
| soko-ml-db (PostgreSQL) | 5432 | — | `soko-ml-network` | ML feature store |

> **Host port vs. container port:** A container port is the port the process listens on *inside* Docker. A host port is what is mapped to your machine. Only explicitly mapped ports are reachable from your host — all others are container-internal only.

### Port Binding Rules

1. **Production** — only Nginx (`:80`) and ml-gateway-service (container `:8000` → host `:8080`) are bound to the host. Every other container port is internal-only.
2. **Development** — `make dev-price`, `make dev-rec`, `make dev-ingest` bind additional host ports (`:8094`, `:8095`, `:8096`) for local hot-reload. These mappings do not exist in the production Compose file.
3. **No direct service access** — clients must never call `auth_service:8001` directly; all traffic routes through Nginx or ml-gateway. The port numbers in the architecture diagram are container-internal addresses, not public endpoints.

### Service-to-Service Communication Examples

```bash
# Nginx → auth-service (internal subrequest for JWT validation)
nginx → http://auth_service:8001/verify-token

# Nginx → ml-gateway (cross-network via soko-ml-bridge)
nginx → http://ml-gateway-service:8000/price/predict

# ml-gateway → price-prediction (ML-internal only)
ml-gateway-service → http://price-prediction-service:8001/predict

# ml-gateway → recommendation (ML-internal only)
ml-gateway-service → http://recommendation-service:8002/recommend/{user_id}

# data-ingestion → user-service (cross-network via soko-ml-bridge)
data-ingestion-service → http://user_service:8002/users/farmers

# order-service → Kafka → kafka-agent (event-driven, cross-network)
order-service publishes to soko.transactions → kafka-agent consumes and boosts
```

---

## Core Services

### Auth Service — `:8001`

Issues JWTs on login and validates them on every protected route. Nginx calls `/verify-token` internally — it never reaches the client. On success it injects `X-User-Id`, `X-User-Role`, `X-User-Email` into downstream headers.

**Nginx route:** `/auth/` and `/oauth/` (public)

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Register with `role: farmer\|buyer\|both` |
| POST | `/auth/login` | Login → `{ access_token, refresh_token }` |
| GET  | `/auth/me` | Current user info (JWT required) |
| POST | `/auth/refresh` | Refresh an expiring token |
| GET  | `/verify-token` | Internal — called by Nginx, not clients |
| GET  | `/verify-token-optional` | Internal — for public routes that optionally expose user context |

---

### User Service — `:8002`

User profiles and account management. Receives authenticated user context from Nginx and never validates tokens itself. Also exposes internal endpoints used by the ML data-ingestion service to bootstrap the feature store.

**Nginx route:** `/users/` (JWT required)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET  | `/users/me` | JWT | Own profile |
| PUT  | `/users/me` | JWT | Update profile (specialties, interests, district) |
| GET  | `/users/farmers` | JWT | List all farmers (paginated) — also used internally by ML ingestion |
| GET  | `/users/buyers` | JWT | List all buyers (paginated) — also used internally by ML ingestion |
| GET  | `/users/{id}` | JWT | Single farmer profile |

---

### Produce Service — `:8003`

Produce listings — creation, search, and stock management. Farmers create listings; buyers browse them. Supports image uploads via Cloudinary.

**Nginx route:** `/listings/` (JWT optional — public browsing, auth to create)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST   | `/listings/` | farmer | Create a listing |
| GET    | `/listings/` | optional | Browse / search (filter by category, district, price) |
| GET    | `/listings/{id}` | optional | Single listing |
| PUT    | `/listings/{id}` | farmer | Update own listing |
| DELETE | `/listings/{id}` | farmer | Remove listing |

---

### Order Service — `:8004`

Order lifecycle from placement to completion. Publishes `purchase_completed` events to `soko.transactions` on Kafka on every successful checkout — this is the primary data source for ML price observations and interaction boosts.

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
| POST  | `/orders/` | buyer | Place order → publishes to `soko.transactions` |
| GET   | `/orders/` | buyer | List own orders |
| POST  | `/orders/{id}/cancel` | buyer | Cancel → publishes cancellation to Kafka |
| POST  | `/orders/{id}/review` | buyer | Rate after completion |
| GET   | `/orders/incoming/` | farmer | Orders for farmer's produce |
| PATCH | `/orders/{id}/status` | farmer | Advance status |

---

### Payment Service — `:8005`

Payment initiation and reconciliation via PesaPal (MTN Mobile Money / Airtel Money). The `/webhook/` endpoint is public so PesaPal can POST confirmations without a token.

**Nginx routes:** `/payments/` (JWT required) · `/webhook/` (public)

---

### Message Service — `:8006`

Real-time direct messaging over WebSocket. Token is validated by the service itself on WebSocket connection.

**Nginx routes:** `/message/` (JWT required) · `/message/ws/` (WebSocket, service-auth)

---

### Notification Service — `:8007`

Push notifications delivered over WebSocket. Receives events from order and payment services and pushes them to connected clients.

**Nginx routes:** `/notifications/` (JWT required) · `/notifications/ws/` (WebSocket, service-auth)

---

### Blog Service — `:8008`

Agri-knowledge articles and market commentary. Supports image uploads up to 10 MB via Cloudinary.

**Nginx route:** `/posts/` (JWT optional — public reading, auth to create)

---

### USSD Service — `:8009`

USSD session handler for feature-phone users. Allows farmers with basic handsets to check prices and receive order notifications without a smartphone. Calls the ML gateway for price predictions.

**Nginx route:** `/ussd/` (public — USSD networks carry no auth headers)

---

## ML Layer

The ML layer lives in `services/soko-ml/` and runs as a separate Docker Compose stack. All six services connect to the core stack via the `soko-ml-bridge` Docker network.

### ml-gateway-service — host `:8080` / internal `:8000`

Single entry point for all ML capabilities. Nginx proxies `/ml/*` and `/recommendations/*` here. Adds circuit breaking (3 failures → open, 30s reset), request logging, and graceful fallback responses.

| Gateway Endpoint | Routes to | Auth |
|---|---|---|
| `POST /price/predict` | price-prediction-service | public |
| `GET /price/markets` | price-prediction-service | public |
| `GET /price/crops` | price-prediction-service | public |
| `GET /recommend/farmers-for-buyer/{buyer_id}` | recommendation-service | JWT required |
| `GET /recommend/buyers-for-farmer/{farmer_id}` | recommendation-service | JWT required |
| `POST /location/route` | location-service | public |
| `POST /location/discover` | location-service | public |
| `GET /gaps/summary` | data-ingestion-service | public |
| `GET /coverage` | data-ingestion-service | public |
| `POST /ingest/bootstrap` | data-ingestion-service | internal |
| `GET /health` | aggregated from all downstream | public |

---

### price-prediction-service — internal `:8001`

Serves 4-week price forecasts per market–crop pair in UGX using pre-trained **Prophet** models.

- Loads `.pkl` model files from `models/` at startup
- Falls back to Uganda bimodal seasonal heuristics when no model file exists
- Caches predictions in Redis (TTL 24 h, key: `price:v1:{market}:{crop}:{weeks}`)
- Consumes `soko.price.requests`; publishes to `soko.price.results`

**Supported markets:** Kisenyi_Kampala · Gulu · Mbarara · Mbale · Lira · Masaka

**Supported crops:** maize_grain · yellow_beans · irish_potatoes · tomatoes · matoke · cassava_chips · sorghum · millet

**Uganda bimodal seasonality factors applied:**
- Jun–Jul, Nov–Dec: ×0.92 (post-harvest abundance)
- Jan–Feb: ×1.10 (lean dry season)

---

### recommendation-service — internal `:8002`

Recommends high-performing farmers to buyers and vice versa using **weighted content-based scoring** enriched in real-time from Kafka interaction events.

- Loads profiles from the ML feature store (PostgreSQL `soko_ml_db`) at startup
- Refreshes profiles every 15 minutes (`PROFILE_REFRESH_INTERVAL_SECONDS`)
- Exposes `POST /internal/reload` so data-ingestion can trigger an immediate refresh after bootstrap
- Enforces identity: `x-user-id` from JWT must match the `{buyer_id}` or `{farmer_id}` path parameter
- Caches results in Redis (TTL 1 h)
- Invalidates cache on relevant Kafka interaction events

**Scoring — farmers for buyer:**

| Signal | Weight |
|---|---|
| Crop overlap: buyer interests ∩ farmer specialties / \|buyer interests\| | 0.35 |
| District match (exact) | 0.20 |
| Farmer average rating (normalised / 5.0) | 0.20 |
| Farmer fulfillment rate | 0.15 |
| Interaction boost from `soko.interactions` (capped +0.20) | additive |

**Scoring — buyers for farmer:**

| Signal | Weight |
|---|---|
| Crop overlap: farmer specialties ∩ buyer interests / \|farmer specialties\| | 0.35 |
| District match (exact) | 0.20 |
| Buyer payment reliability | 0.25 |
| Buyer spend volume (normalised by dataset max) | 0.20 |

**Interaction boost values:**

| Event type | Boost |
|---|---|
| `farmer_viewed` | +0.02 |
| `buyer_inquiry` | +0.05 |
| `purchase_completed` | +0.10 |
| `rating_submitted` | +0.04 |
| `high_rating` | +0.08 |

---

### data-ingestion-service — internal `:8004`

Populates and maintains the ML feature store.

**Bootstrap (runs at startup or `POST /bootstrap`):**
1. Fetches all farmer profiles from `GET /users/farmers` on the user service
2. Fetches all buyer profiles from `GET /users/buyers` on the user service
3. Transforms and upserts into `farmer_features` and `buyer_features` tables
4. After bootstrap, immediately calls `POST /internal/reload` on the recommendation service so new users appear in recommendations within seconds rather than waiting for the 15-minute timer

**Streaming:**
- Consumes `soko.transactions` Kafka topic
- Normalises crop names and maps delivery districts to ML market nodes
- Inserts price observations into `price_observations` table
- Detects outliers (rejects prices > 3σ from rolling 30-obs mean)

**Coverage tracking:** Maintains `coverage_map` per (crop, market) pair. When a pair reaches 52 observations, it is flagged as `model_ready`.

---

### location-service — internal `:8003`

Routes farmers to optimal markets and helps buyers discover local supply.

**Tiered routing:**

| Tier | Condition | Response |
|---|---|---|
| 1 | Crop supported + ≥52 price observations for market | Top 3 markets ranked by price minus transport cost |
| 2 | Crop supported + <52 observations | Fallback to aggregated cross-market price data |
| 3 | Crop completely unsupported | Publishes `CoverageGapEvent` to `soko.gaps`; returns generic suggestion |

---

### kafka-agent — no HTTP port

Long-running process that bridges the Kafka event stream:

| Consumes | Action |
|---|---|
| `soko.transactions` | Forwards `purchase_completed` events to `soko.interactions` (recommendation boost pipeline) |
| `soko.transactions` | Forwards to data-ingestion via `POST /ingest/order-event` (price observations) |
| `soko.price.requests` | Calls price-prediction-service, publishes result to `soko.price.results` |
| `soko.interactions` | Logged (recommendation-service has its own consumer on this topic) |
| `soko.gaps` | Logs coverage gap events for monitoring |

Failed messages go to `soko.dlq` with full error context for replay.

---

### ML Infrastructure

| Component | Image | Config |
|---|---|---|
| Kafka | `confluentinc/cp-kafka:7.5.0` | 1 broker, auto-topic creation off |
| Zookeeper | `confluentinc/cp-zookeeper:7.5.0` | — |
| Redis | `redis:7-alpine` | 256 MB max, `allkeys-lru` eviction |
| PostgreSQL | `postgres:16-alpine` | `soko_ml_db` database |

**Kafka topics:**

| Topic | Partitions | Retention | Purpose |
|---|---|---|---|
| `soko.transactions` | 6 | 7 days | Purchase and order events from order-service |
| `soko.interactions` | 6 | 3 days | Views, inquiries, ratings (recommendation boosts) |
| `soko.price.requests` | 3 | 1 day | Async price prediction requests |
| `soko.price.results` | 3 | 1 day | Async price prediction responses |
| `soko.ml.events` | 2 | 14 days | Model lifecycle events |
| `soko.gaps` | 2 | 30 days | Coverage gap notifications |
| `soko.dlq` | 2 | 30 days | Dead-letter queue |

---

## Auth → ML: The Authenticated Recommendation Flow

This is the full end-to-end flow for a user receiving personalised recommendations:

```
1. User registers
   POST /auth/register { email, password, role: "buyer" }
   → auth_service creates account + user_service creates profile

2. User updates profile with interests
   PUT /users/me { interests: ["Grains", "Legumes"], district: "Kampala" }
   → user_service stores interests and district

3. ML data-ingestion bootstrap (runs on startup or make ingest-bootstrap)
   data-ingestion-service fetches:
     GET http://user_service:8002/users/farmers  (with X-Internal-Secret)
     GET http://user_service:8002/users/buyers   (with X-Internal-Secret)
   → upserts into farmer_features / buyer_features in soko_ml_db
   → immediately calls POST http://recommendation-service:8002/internal/reload
   → recommendation-service reloads profiles from soko_ml_db within seconds

4. User requests recommendations (authenticated)
   GET /ml/recommend/farmers-for-buyer/{user_id}
       Authorization: Bearer <token>

   Nginx flow:
   a) /_verify_token subrequest → auth_service validates JWT
   b) auth_service returns X-User-Id: {user_id}, X-User-Role: buyer
   c) Nginx injects X-User-Id, X-User-Role and proxies to ml-gateway:8000

   ML gateway flow:
   d) Extracts X-User-Id and X-User-Role from incoming headers
   e) Forwards to recommendation-service:8002/recommend/farmers-for-buyer/{user_id}
      with X-User-Id header attached

   Recommendation service:
   f) Reads X-User-Id header
   g) Validates: X-User-Id MUST equal {buyer_id} path parameter (403 if mismatch)
   h) Looks up buyer profile from in-memory ProfileStore (loaded from soko_ml_db)
   i) Scores all farmers: crop_overlap × 0.35 + district_match × 0.20 +
      avg_rating × 0.20 + fulfillment × 0.15 + interaction_boost (max +0.20)
   j) Returns top N farmers ranked by score, with matchScore field

5. As the user transacts, scores improve automatically
   Order placed → order_service publishes to soko.transactions
   kafka-agent → soko.interactions (purchase_completed event)
   recommendation-service Kafka consumer → interaction_store += +0.10 boost
   → Redis cache invalidated → next request returns re-ranked results
```

---

## How the Two Stacks Integrate

```
Core stack                              ML stack
──────────────────                      ──────────────────────────────────
order_service:8004  ──Kafka──►          soko.transactions
                                         └── data-ingestion (price obs)
                                         └── kafka-agent → soko.interactions
                                              └── recommendation (boost)

nginx:80  ──proxy──►                    ml-gateway:8000
  /ml/price/     (public)               └── price-prediction-service:8001
  /ml/recommend/ (JWT auth) ──x-user-id──► recommendation-service:8002
  /recommendations/ (JWT auth)

data-ingestion:8004  ──HTTP──►          user_service:8002
                                         GET /users/farmers
                                         GET /users/buyers

ussd_service:8009  ──HTTP──►            ml-gateway:8000
                                         POST /price/predict
```

Both stacks share the `soko-ml-bridge` Docker network. Core service names (`user_service`, `order_service`, `produce_service`) are resolvable from ML services on that bridge.

**Internal secret:** All service-to-service calls use `X-Internal-Secret: internal-secret` (set by `INTERNAL_SECRET` in core services and `INTERNAL_API_KEY` in the ML stack). These must match.

---

## API Reference

All external requests enter via `http://localhost:80` through Nginx. Protected routes require `Authorization: Bearer <token>`.

### Authentication

```http
POST /auth/register   { "email": "...", "password": "...", "role": "farmer|buyer|both" }
POST /auth/login      { "email": "...", "password": "..." }
GET  /auth/me         Authorization: Bearer <token>
POST /auth/refresh    Authorization: Bearer <token>
```

### User Profile

```http
GET  /users/me        Authorization: Bearer <token>
PUT  /users/me        { "fullName": "...", "district": "Kampala",
                        "specialties": ["maize", "beans"],   # farmers
                        "interests": ["Grains", "Legumes"] } # buyers
GET  /users/farmers   ?district=Kampala&page=1&limit=20
GET  /users/{id}
```

### Produce

```http
GET    /listings/     ?category=grains&district=Kampala&min_price=500&max_price=2000
POST   /listings/     { "title", "category", "price_per_kg", "quantity_kg", "district" }
GET    /listings/{id}
PUT    /listings/{id}
DELETE /listings/{id}
```

### Orders

```http
POST  /orders/              { "listing_id": "...", "quantity_kg": 100 }
GET   /orders/
POST  /orders/{id}/cancel
POST  /orders/{id}/review   { "rating": 5, "comment": "..." }
GET   /orders/incoming/                                  (farmer only)
PATCH /orders/{id}/status   { "new_status": "confirmed|completed|rejected" }
```

### Payments

```http
POST /payments/initiate   { "order_id": "...", "phone": "256700000000" }
GET  /payments/{id}/status
POST /webhook/pesapal     (public — PesaPal callback)
```

### ML — Price Prediction (public, via Nginx)

```http
POST /ml/price/predict    { "market": "Kisenyi_Kampala", "crop": "maize_grain", "weeks_ahead": 4 }
GET  /ml/price/markets
GET  /ml/price/crops
```

### ML — Recommendations (JWT required, via Nginx)

```http
GET /ml/recommend/farmers-for-buyer/{your_user_id}?top_n=5
    Authorization: Bearer <token>

GET /ml/recommend/buyers-for-farmer/{your_user_id}?top_n=5
    Authorization: Bearer <token>
```

The path `{your_user_id}` must be your own user ID from the JWT. The recommendation service returns 403 if you attempt to request another user's recommendations.

### ML — Admin/Internal (bypass Nginx, dev only)

```http
GET  http://localhost:8080/health
POST http://localhost:8096/bootstrap
GET  http://localhost:8096/bootstrap/status
GET  http://localhost:8096/coverage
GET  http://localhost:8096/gaps/summary
POST http://localhost:8095/internal/reload   X-Internal-Secret: internal-secret
```

---

## User Flows

### Farmer — complete flow

```
1. POST /auth/register  { role: "farmer" }
2. POST /auth/login     → JWT
3. PUT  /users/me       { specialties: ["maize", "beans"], district: "Kampala" }
4. POST /listings/      List produce with price and available quantity
5. GET  /orders/incoming/    See buyer orders
6. PATCH /orders/{id}/status  { new_status: "confirmed" }
7. PATCH /orders/{id}/status  { new_status: "completed" }
8. GET  /ml/recommend/buyers-for-farmer/{farmer_id}   See matched buyers
```

### Buyer — complete flow

```
1. POST /auth/register  { role: "buyer" }
2. POST /auth/login     → JWT
3. PUT  /users/me       { interests: ["Grains", "Legumes"], district: "Gulu" }
4. GET  /listings/      Browse produce (filter by district, crop, price)
5. POST /orders/        Place order
6. POST /payments/initiate   Pay via Mobile Money
7. POST /orders/{id}/review  Rate after completion
8. GET  /ml/recommend/farmers-for-buyer/{buyer_id}   See matched farmers
                                                      (personalised to your interests)
```

### Price check (USSD — feature phones)

```
1. Farmer dials USSD short code
2. ussd_service calls POST http://ml-gateway:8000/price/predict
3. 4-week price forecast returned as plain text to handset
```

---

## Event System (Kafka)

The core stack publishes order events to Kafka. The ML layer consumes them for price learning and recommendation boosting.

### Topics and flows

```
order_service (checkout)
  └── PUBLISH soko.transactions { event_type: "purchase_completed",
                                   buyer_id, farmer_id, crop, market,
                                   quantity_kg, price_per_kg_ugx, total_ugx }

kafka-agent (transaction consumer)
  ├── PUBLISH soko.interactions { event_type: "purchase_completed",
  │                                buyer_id, farmer_id }
  │       └── recommendation-service Kafka consumer applies +0.10 boost
  │               and invalidates Redis cache for this buyer-farmer pair
  └── HTTP POST data-ingestion-service /ingest/order-event
          └── normalises crop name, maps district → market, inserts price_observation

location-service (Tier 3 fallback — unsupported crop)
  └── PUBLISH soko.gaps { event_type: "crop_coverage_gap",
                           crop_submitted, category_guess, priority }
          └── kafka-agent CoverageGapConsumer logs and monitors

Any service
  └── PUBLISH soko.price.requests { market, crop, weeks_ahead }
          └── kafka-agent PriceRequestConsumer calls price-prediction-service
              └── PUBLISH soko.price.results { predictions: [...] }
```

### Dead-letter queue

Any message that fails processing after all retries is written to `soko.dlq` with the original topic, raw value, error type, and error message — enabling offline replay and audit.

---

## Getting Started

All commands run from the **project root**. Prerequisites: Docker 20+, Python 3.11+, Make.

### 1. Copy and configure environment files

```bash
cp services/soko-ml/.env.example services/soko-ml/.env
# Edit services/soko-ml/.env — set POSTGRES_PASSWORD and any keys
```

Core service `.env` files are already populated with development defaults in each `services/<name>/.env`.

### 2. Start the core stack

```bash
make core-up
# or: docker compose up --build -d
```

Verify all 9 core services are healthy:
```bash
curl http://localhost/health
```

### 3. Start the ML stack

```bash
make ml-up
# or: cd services/soko-ml && docker compose --env-file .env up --build -d
```

Watch startup logs until all services report healthy:
```bash
make ml-logs
```

### 4. Seed the database with test data

```bash
make seed
```

Registers 13 farmers and 10 buyers via the auth API, updates their profiles (district, specialties, interests), and creates produce listings. After seeding, triggers `make ingest-bootstrap` automatically.

### 5. Bootstrap the ML feature store

```bash
make ingest-bootstrap
```

Pulls all profiles from user-service into `soko_ml_db` and immediately triggers a recommendation-service reload. After this, recommendations are live.

### 6. Verify the full stack

```bash
make health      # Check all ML service health endpoints
make smoke-test  # End-to-end: price prediction + farmer recs + buyer recs
```

### What to look for if something fails

| Symptom | Cause | Fix |
|---|---|---|
| `"recommendation": "unreachable"` | Feature store empty at startup | Run `make ingest-bootstrap` |
| `"models_loaded": 0"` in price health | No `.pkl` files | Run `make train`, or rely on seasonal fallback |
| Gateway returns `503` | Service startup race | Wait 30 s, check `make ml-logs` |
| `kafka-init` exits immediately | Kafka not ready | It restarts automatically; wait |
| Recommendations return 404 | User not in feature store | Run `make ingest-bootstrap` |
| Recommendations return 403 | JWT user_id ≠ path param | Use your own user ID in the URL |
| `"cached": true` on first call | Stale Redis from prior run | `docker exec soko-ml-redis redis-cli FLUSHDB` |

---

## Makefile Reference

All targets run from the **project root**.

### Stack lifecycle

| Command | What it does |
|---|---|
| `make core-up` | Start core stack (docker compose up --build -d) |
| `make core-down` | Stop core stack |
| `make ml-up` | Start ML stack (services/soko-ml) |
| `make ml-down` | Stop ML stack |
| `make up` | Start both stacks |
| `make down` | Stop both stacks |
| `make restart` | Restart ML stack |
| `make logs` / `make ml-logs` | Follow ML service logs |
| `make logs-price` | price-prediction-service logs only |
| `make logs-rec` | recommendation-service logs only |
| `make logs-gateway` | ml-gateway-service logs only |
| `make logs-agent` | kafka-agent logs only |

### Data and models

| Command | What it does |
|---|---|
| `make seed` | Seed core DBs with Ugandan farmer/buyer test data |
| `make ingest-bootstrap` | Pull profiles into ML feature store + reload recommendation-service |
| `make generate-data` | Generate synthetic price CSVs for model training |
| `make train` | Train 48 Prophet models → `price-prediction-service/models/` |

### Testing

| Command | What it does |
|---|---|
| `make test` | Run all pytest suites |
| `make test-price` | price-prediction-service tests only |
| `make test-rec` | recommendation-service tests only |
| `make test-gateway` | ml-gateway-service tests only |
| `make health` | Curl all ML `/health` endpoints |
| `make smoke-test` | Randomised end-to-end: price + farmer recs + buyer recs |

### Cleanup

| Command | What it does |
|---|---|
| `make clean` | Remove `__pycache__`, `.pyc` files, venvs |
| `make clean-models` | Remove trained `.pkl` files |
| `make clean-docker` | Full ML docker wipe (`down -v --rmi all`) |

---

## Environment Variables

### Core stack (each service has its own `.env`)

| Variable | Services | Description |
|---|---|---|
| `DATABASE_URL` | all | PostgreSQL connection string |
| `INTERNAL_SECRET` | all | Inter-service auth key (must be `internal-secret` in dev) |
| `SECRET_KEY` | auth | JWT signing key |
| `FRONTEND_URL` | auth, payment | Allowed redirect origin |
| `REDIS_URL` | produce, blog | Shared Redis for caching |
| `KAFKA_BOOTSTRAP_SERVERS` | order | `kafka:9092` (core stack's Kafka is the ML stack's Kafka) |
| `KAFKA_TRANSACTION_TOPIC` | order | `soko.transactions` |

### ML stack (`services/soko-ml/.env.example`)

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_USER` | `soko_ml` | ML DB user |
| `POSTGRES_PASSWORD` | `changeme` | **REQUIRED: change before production** |
| `REDIS_HOST` | `redis` | ML Redis hostname |
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:9092` | Must match the Kafka started by the ML stack |
| `USER_SERVICE_URL` | `http://user_service:8002` | Core user service (via soko-ml-bridge) |
| `ORDER_SERVICE_URL` | `http://order_service:8004` | Core order service (via soko-ml-bridge) |
| `PRODUCE_SERVICE_URL` | `http://produce_service:8003` | Core produce service (via soko-ml-bridge) |
| `INTERNAL_API_KEY` | `internal-secret` | **Must match core services' `INTERNAL_SECRET`** |
| `BOOTSTRAP_ON_STARTUP` | `true` | Pull profiles from user-service at startup |
| `PROFILE_REFRESH_INTERVAL_SECONDS` | `900` | How often recommendation-service reloads from DB |
| `PRICE_CACHE_TTL_SECONDS` | `86400` | 24 hours |
| `REC_CACHE_TTL_SECONDS` | `3600` | 1 hour |
| `GATEWAY_PORT` | `8080` | Host port for ML gateway |
| `REC_SERVICE_PORT` | `8095` | Host port for recommendation-service |
| `INGEST_SERVICE_PORT` | `8096` | Host port for data-ingestion-service |
| `LOCATION_SERVICE_PORT` | `8097` | Host port for location-service |
| `PRICE_SERVICE_PORT` | `8094` | Host port for price-prediction-service |

---

## Project Structure

```
soko/
├── Makefile                             ← All stack commands (run from here)
├── docker-compose.yml                   ← Core Soko stack (9 services + DBs + Redis)
├── nginx/
│   └── nginx.conf                       ← API gateway: routing, auth subrequests, CORS
├── scripts/
│   ├── seed.py                          ← Seed core DBs with Ugandan test users + listings
│   └── smoke_test.py                    ← Randomised ML end-to-end test
├── services/
│   ├── auth/                            ← JWT auth, /verify-token          :8001
│   │   └── .env
│   ├── user/                            ← User profiles                    :8002
│   │   └── .env
│   ├── produce/                         ← Listings, stock, Cloudinary       :8003
│   │   └── .env
│   ├── order/                           ← Orders, Kafka publisher           :8004
│   │   └── .env
│   ├── payment/                         ← PesaPal Mobile Money             :8005
│   │   └── .env
│   ├── message/                         ← WebSocket messaging               :8006
│   │   └── .env
│   ├── notification/                    ← WebSocket push                    :8007
│   │   └── .env
│   ├── blog/                            ← Articles, Cloudinary              :8008
│   │   └── .env
│   ├── ussd/                            ← Feature-phone USSD handler        :8009
│   │   └── .env
│   └── soko-ml/                         ← ML stack (own compose)
│       ├── docker-compose.yml
│       ├── .env.example                 ← Copy to .env before starting
│       ├── shared/
│       │   └── events.py                ← Kafka event dataclasses
│       ├── ml-gateway-service/          ← Proxy + circuit breaker  host:8080
│       │   └── src/
│       │       ├── main.py              ← FastAPI routes, header forwarding
│       │       ├── proxy.py             ← Circuit breaker, retries, fallbacks
│       │       └── logger.py
│       ├── price-prediction-service/    ← Prophet + Redis          host:8094
│       │   ├── src/
│       │   │   ├── predictor.py
│       │   │   └── feature_store_client.py
│       │   └── models/                  ← .pkl files (gitignored, make train)
│       ├── recommendation-service/      ← Content scoring + Postgres host:8095
│       │   └── src/
│       │       ├── main.py              ← Identity validation, /internal/reload
│       │       ├── recommender.py       ← Scoring algorithm
│       │       ├── feature_store_client.py
│       │       ├── interaction_store.py
│       │       └── kafka_consumer.py
│       ├── data-ingestion-service/      ← Bootstrap + streaming   host:8096
│       │   └── src/
│       │       ├── main.py              ← Bootstrap, reload notification
│       │       ├── clients/             ← user_client.py, order_client.py
│       │       ├── transformers/        ← Crop normalisation, price transform
│       │       ├── bootstrap/           ← Farmers, buyers, orders, markets
│       │       └── streams/             ← Kafka transaction consumer
│       ├── location-service/            ← Market routing          host:8097
│       │   └── src/
│       │       ├── market_router.py     ← Tier 1/2 routing
│       │       ├── fallback.py          ← Tier 3 + close_pool
│       │       └── gap_notifier.py      ← Coverage gap events
│       ├── kafka-agent/                 ← Event backbone (no HTTP port)
│       │   └── src/
│       │       ├── agent.py
│       │       ├── consumers/           ← Per-topic consumers
│       │       ├── producers/
│       │       └── dlq.py
│       └── db/
│           └── schema.sql               ← ML feature store DDL
└── tests/
    └── integration/                     ← Core stack integration tests
```

---

## Production Bug Report

The following bugs were identified and fixed during the ML integration audit. All fixes are in this codebase.

### SECURITY-01 — `/recommendations/` endpoint bypassed authentication

**Severity:** High  
**Location:** `nginx/nginx.conf`

The legacy `/recommendations/` route proxied to the ML recommendation service without any `auth_request` call. Any unauthenticated client could retrieve another user's personalised recommendations by guessing their UUID.

**Fix:** Added `auth_request /_verify_token` with `X-User-Id` and `X-User-Role` injection, matching the protection on `/ml/recommend/`.

---

### SECURITY-02 — Recommendation service accepted any user ID in path

**Severity:** High  
**Location:** `services/soko-ml/recommendation-service/src/main.py`

The recommendation endpoints accepted `{buyer_id}` and `{farmer_id}` path parameters without checking whether the requesting user was actually that person. An authenticated attacker could harvest recommendations for any user by iterating through UUIDs.

**Fix:** Added `_check_identity()` — reads `x-user-id` header (injected by Nginx from the JWT), compares it against the path parameter, returns 403 on mismatch. Admin role bypasses the check.

---

### SECURITY-03 — ML Gateway did not forward `X-User-Id` to recommendation service

**Severity:** High (prerequisite for SECURITY-02 fix to function)  
**Location:** `services/soko-ml/ml-gateway-service/src/main.py` and `src/proxy.py`

The gateway's `recommend_farmers` and `recommend_buyers` handlers did not accept a `Request` object and therefore could not read or forward the `x-user-id` header injected by Nginx. The recommendation service always received requests with no identity header and therefore could never enforce identity.

**Fix:** Both recommendation handlers now accept `request: Request`, extract `x-user-id` and `x-user-role`, and pass them via the new `headers` parameter on `proxy_request()`.

---

### BUG-01 — Wrong default service ports in data-ingestion clients

**Severity:** High (breaks bootstrap on fresh install)  
**Locations:**
- `services/soko-ml/data-ingestion-service/src/clients/user_client.py` — default `http://user-service:3003` (should be `8002`)
- `services/soko-ml/data-ingestion-service/src/clients/order_client.py` — default `http://order-service:3002` (should be `8004`)

These defaults are only used when the env var is not set. If `.env` is missing or incomplete, bootstrap silently fails — no profiles are ingested, recommendations return empty results.

**Fix:** Corrected both defaults to match the actual service ports.

---

### BUG-02 — Swapped ports in `.env.example` and `docker-compose.yml` defaults

**Severity:** Medium  
**Locations:**
- `services/soko-ml/.env.example` lines 31–32
- `services/soko-ml/docker-compose.yml` data-ingestion environment block

`ORDER_SERVICE_URL` defaulted to port `8003` (produce service port) and `PRODUCE_SERVICE_URL` defaulted to port `8004` (order service port). These were swapped.

**Fix:** Corrected to `ORDER_SERVICE_URL=http://order_service:8004` and `PRODUCE_SERVICE_URL=http://produce_service:8003` in both files.

---

### BUG-03 — Recommendation service missing `POSTGRES_DSN` and `INTERNAL_API_KEY` in docker-compose

**Severity:** High  
**Location:** `services/soko-ml/docker-compose.yml` recommendation-service environment block

The recommendation service loads all profiles from PostgreSQL via `feature_store_client.py`, but `POSTGRES_DSN` was not wired into the container environment. The service would use the hardcoded default DSN string which may not match the actual DB credentials. `INTERNAL_API_KEY` was also missing, meaning the `/internal/reload` endpoint would accept any call without authentication.

**Fix:** Added `POSTGRES_DSN`, `INTERNAL_API_KEY`, `PROFILE_REFRESH_INTERVAL_SECONDS` to the recommendation-service environment. Added `soko-ml-db` to its `depends_on`.

---

### BUG-04 — New users waited up to 15 minutes to appear in recommendations

**Severity:** Medium  
**Location:** `services/soko-ml/recommendation-service/src/main.py`

The recommendation service reloads profiles from the ML feature store on a 15-minute timer (`PROFILE_REFRESH_INTERVAL_SECONDS=900`). After `make seed` or `POST /bootstrap`, newly registered users would not appear in recommendations for up to 15 minutes.

**Fix:** Added `POST /internal/reload` endpoint to the recommendation service. Data-ingestion now calls this endpoint immediately after each successful bootstrap (both at startup and on manual trigger), reducing the lag from up to 15 minutes to under 10 seconds.

---

### What to watch in production

| Risk | Mitigation |
|---|---|
| `INTERNAL_SECRET` / `INTERNAL_API_KEY` mismatch | Keep in a shared secrets manager; both must be identical |
| Feature store staleness | Monitor `GET /ingest/bootstrap/status`; set up an alert if `farmers_ingested = 0` |
| Kafka consumer lag | Monitor `soko.transactions` consumer group `soko-ml-price-collector` lag |
| Recommendation cache too aggressive | Tune `REC_CACHE_TTL_SECONDS` down if personalisation feels stale |
| Coverage gaps accumulating | Monitor `GET /gaps/summary`; high-frequency gaps signal unmet demand |
| Prophet model staleness | Re-run `make train` as price observations accumulate (>52 per market-crop pair triggers `is_model_ready`) |

---

## Known Limitations

- **Alembic not wired** — schema changes to either stack require dropping the affected DB volume
- **Shared JWT secret** — all core services share one key; use a secrets manager in production
- **Order service `/internal/orders` endpoint not implemented** — data-ingestion bootstrap skips order history and relies on live Kafka streaming for price observations instead; price models need real transaction volume before achieving 52-observation model readiness
- **Interaction boosts are in-memory only** — the `InteractionStore` in the recommendation service is not persisted; a service restart resets all boost scores (they rebuild from `soko.interactions` with `auto.offset.reset=latest`, so only future events contribute)
- **Single Kafka broker** — `KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1` is not suitable for production; deploy a 3-broker cluster with replication factor 3
- **No password reset** — requires an outbound email provider
- **Google Maps API optional** — location-service falls back to Haversine straight-line distances when `GOOGLE_MAPS_API_KEY` is empty; transport cost estimates will be less accurate
