# =============================================================================
# Soko — root Makefile
#
# Quick reference:
#   make setup   — first-time setup (network + .env files)
#   make start   — start the full stack (ML + core)
#   make stop    — stop the full stack
#   make restart — stop then start
#
#   make ml-up / make ml-down     — ML stack only
#   make core-up / make core-down — core stack only
# =============================================================================

# ── Compose handles ───────────────────────────────────────────────────────────
ML_DIR       := services/soko-ml
COMPOSE_ML   := docker compose -f $(ML_DIR)/docker-compose.yml --project-directory $(ML_DIR)
COMPOSE_DEV  := docker compose \
                  -f $(ML_DIR)/docker-compose.yml \
                  -f $(ML_DIR)/docker-compose.dev.yml \
                  --project-directory $(ML_DIR)
COMPOSE_CORE := docker compose -f docker-compose.yml

# ── Python venvs (ML layer) ───────────────────────────────────────────────────
PRICE_VENV   := $(ML_DIR)/price-prediction-service/.venv
REC_VENV     := $(ML_DIR)/recommendation-service/.venv
GATEWAY_VENV := $(ML_DIR)/ml-gateway-service/.venv
AGENT_VENV   := $(ML_DIR)/kafka-agent/.venv
DATA_VENV    := $(ML_DIR)/data-generator/.venv
INGEST_VENV  := $(ML_DIR)/data-ingestion-service/.venv
LOC_VENV     := $(ML_DIR)/location-service/.venv

# ── Core services that need .env files ───────────────────────────────────────
CORE_SERVICES := auth user produce order payment message notification blog ussd

.PHONY: setup start stop restart \
        bridge-network \
        ml-up ml-down ml-logs \
        core-up core-down core-logs core-restart \
        install generate-data train cold-start \
        dev dev-price dev-rec dev-gateway dev-location dev-ingest \
        db-up db-shell db-reset \
        infra-up infra-down kafka-topics kafka-ui redis-cli \
        ingest-bootstrap ingest-status gaps-summary gaps-reset \
        logs logs-price logs-rec logs-gateway logs-agent logs-location logs-ingest \
        test test-price test-rec test-gateway test-location test-ingest \
        health smoke-test smoke-route smoke-discover smoke-fallback smoke-tier3 smoke-ingest \
        clean clean-models clean-docker \
        port-reference \
        fill-envs seed destroy-seed \
        help

# =============================================================================
# HELP
# =============================================================================

help:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                     Soko — Makefile Reference                       ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "► RUNNING THE FULL STACK (recommended order for first time)"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  1. make setup       Create bridge network + all .env files"
	@echo "     ↳ Edit each services/*/.env with real secrets before continuing"
	@echo "  2. make start       Build and start ML stack then core stack"
	@echo "     ↳ API gateway    → http://localhost"
	@echo "     ↳ ML gateway     → http://localhost:8080"
	@echo ""
	@echo "  Subsequent runs:    make start   (skips rebuild if images unchanged)"
	@echo "  Tear everything down: make stop"
	@echo ""
	@echo "► FULL STACK"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make setup          First-time: bridge network + .env files for all services"
	@echo "  make start          Build and start the full stack (ML + core)"
	@echo "  make stop           Stop the full stack"
	@echo "  make restart        Stop then start the full stack"
	@echo ""
	@echo "► ML STACK"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make ml-up          Build and start the ML stack"
	@echo "     ↳ ML gateway       → http://localhost:8080"
	@echo "     ↳ Price service    → http://localhost:8094/docs"
	@echo "     ↳ Rec service      → http://localhost:8095/docs"
	@echo "     ↳ Location service → http://localhost:8003/docs"
	@echo "     ↳ Ingest service   → http://localhost:8096/docs"
	@echo "  make ml-down        Stop and remove ML containers + volumes"
	@echo "  make ml-logs        Tail logs for all ML containers"
	@echo "  make cold-start     First-time: bring up infra, bootstrap DB, then full ML stack"
	@echo ""
	@echo "► ML — DATABASE (Feature Store)"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make db-up          Start only the ML Postgres (soko-ml-db) + run schema"
	@echo "  make db-shell       Open psql shell into soko_ml_db"
	@echo "  make db-reset       Drop and re-create schema (destructive!)"
	@echo ""
	@echo "► ML — DATA INGESTION"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make ingest-bootstrap   Trigger initial data sync from backend services"
	@echo "  make ingest-status      Show bootstrap progress"
	@echo "  make gaps-summary       Show crop/market coverage gap report"
	@echo "  make gaps-reset         Reset all gap counters (dev only)"
	@echo ""
	@echo "► CORE STACK"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make core-up        Build and start the core backend services"
	@echo "     ↳ All services   → http://localhost (via nginx)"
	@echo "  make core-down      Stop core containers"
	@echo "  make core-restart   Stop then start core containers"
	@echo "  make core-logs      Tail logs for all core containers"
	@echo ""
	@echo "► ML — LOCAL DEVELOPMENT"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make install        Create Python venvs and install all ML dependencies"
	@echo "  make generate-data  Generate synthetic training data (farmers/buyers/prices)"
	@echo "  make train          Train price-prediction models locally"
	@echo "  make dev            Run ML stack with hot-reload (docker compose dev override)"
	@echo "  make dev-price      Run price service locally with uvicorn on :8094"
	@echo "  make dev-rec        Run recommendation service locally with uvicorn on :8095"
	@echo "  make dev-gateway    Run ML gateway locally with uvicorn on :8080"
	@echo "  make dev-location   Run location service locally with uvicorn on :8003"
	@echo "  make dev-ingest     Run data-ingestion service locally with uvicorn on :8096"
	@echo ""
	@echo "► ML — INFRASTRUCTURE"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make infra-up       Start only Zookeeper, Kafka, and Redis"
	@echo "  make infra-down     Stop and remove infrastructure containers"
	@echo "  make kafka-topics   Create all required Kafka topics"
	@echo "  make kafka-ui       List all Kafka topics"
	@echo "  make redis-cli      Open a Redis CLI session inside the ML Redis container"
	@echo ""
	@echo "► LOGS"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make logs           Tail all ML stack logs"
	@echo "  make logs-price     Tail price-prediction-service logs"
	@echo "  make logs-rec       Tail recommendation-service logs"
	@echo "  make logs-gateway   Tail ml-gateway-service logs"
	@echo "  make logs-agent     Tail kafka-agent logs"
	@echo "  make logs-location  Tail location-service logs"
	@echo "  make logs-ingest    Tail data-ingestion-service logs"
	@echo ""
	@echo "► TESTING"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make test           Run all ML service test suites"
	@echo "  make test-price     Run price-prediction-service tests only"
	@echo "  make test-rec       Run recommendation-service tests only"
	@echo "  make test-gateway   Run ml-gateway-service tests only"
	@echo "  make test-location  Run location-service tests only"
	@echo "  make test-ingest    Run data-ingestion-service tests only"
	@echo ""
	@echo "► HEALTH & SMOKE"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make health           Hit /health on API gateway + all ML services"
	@echo "  make smoke-test       Price prediction + recommendation calls"
	@echo "  make smoke-route      Location /route with a sample farmer payload"
	@echo "  make smoke-discover   Location /discover buyer→farmers query"
	@echo "  make smoke-fallback   Location /route Tier 2 fallback (rare crop)"
	@echo "  make smoke-tier3      Location /route Tier 3 unknown crop"
	@echo "  make smoke-ingest     POST a synthetic order event to /ingest/order-event"
	@echo ""
	@echo "► CLEAN"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make clean          Remove Python venvs and cached files"
	@echo "  make clean-models   Remove trained model .pkl files"
	@echo "  make clean-docker   Remove all containers, volumes, and images (both stacks)"
	@echo ""
	@echo "► REFERENCE"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make port-reference Show all container/host port mappings for both stacks"
	@echo ""
	@echo "► SEED & DESTROY"
	@echo "  ─────────────────────────────────────────────────────────"
	@echo "  make fill-envs      Write consistent dev credentials to all service .env files"
	@echo "  make seed           fill-envs + populate all services with Ugandan dummy data"
	@echo "     ↳ Phases: register users → profiles → listings → orders → messages"
	@echo "     ↳         blog posts → reviews → ML bootstrap → rec-service reload"
	@echo "  make destroy-seed   Remove all seeded data from every service database"
	@echo "     ↳ Reads scripts/.seed_manifest.json written by 'make seed'"
	@echo ""

# =============================================================================
# FIRST-TIME SETUP
# =============================================================================

setup: bridge-network
	@for svc in $(CORE_SERVICES); do \
	  if [ ! -f services/$$svc/.env ]; then \
	    cp services/$$svc/.env.example services/$$svc/.env; \
	    echo "  created  services/$$svc/.env"; \
	  else \
	    echo "  exists   services/$$svc/.env (skipped)"; \
	  fi; \
	done
	@if [ ! -f $(ML_DIR)/.env ]; then \
	  cp $(ML_DIR)/.env.example $(ML_DIR)/.env; \
	  echo "  created  $(ML_DIR)/.env"; \
	else \
	  echo "  exists   $(ML_DIR)/.env (skipped)"; \
	fi
	@echo ""
	@echo "Setup complete. Before running 'make start', open each .env and set:"
	@echo "  services/auth/.env         → SECRET_KEY, FRONTEND_URL"
	@echo "  services/*/.env            → INTERNAL_SECRET (same value in all services)"
	@echo "  services/payment/.env      → PESAPAL_CONSUMER_KEY / SECRET (optional)"
	@echo "  services/produce/.env      → CLOUDINARY_* (optional, for image uploads)"
	@echo "  services/notification/.env → AT_USERNAME / AT_API_KEY (optional, for SMS)"
	@echo "  services/ussd/.env         → AT_USERNAME / AT_API_KEY (optional)"

# =============================================================================
# SHARED NETWORK
# =============================================================================
# soko-ml-bridge connects the ML stack to nginx, order, and produce services.
# Created once; both docker-compose files reference it as external.

bridge-network:
	@docker network create soko-ml-bridge 2>/dev/null && \
	  echo "Bridge network soko-ml-bridge created." || \
	  echo "Bridge network soko-ml-bridge already exists."

# =============================================================================
# FULL STACK
# =============================================================================

start: bridge-network ml-up core-up
	@echo ""
	@echo "Full Soko stack is live:"
	@echo "  API gateway (all services) → http://localhost"
	@echo "  ML price predictions       → http://localhost/ml/price/predict"
	@echo "  ML recommendations         → http://localhost/ml/recommend/"
	@echo "  ML gateway (direct)        → http://localhost:8080"

stop: core-down ml-down

restart: stop start

# =============================================================================
# ML STACK
# =============================================================================

ml-up: bridge-network
	$(COMPOSE_ML) up --build -d
	@echo "ML stack live → http://localhost:8080  (gateway)"
	@echo "               http://localhost:8094  (price-prediction)"
	@echo "               http://localhost:8095  (recommendation)"

ml-down:
	$(COMPOSE_ML) down -v

ml-logs:
	$(COMPOSE_ML) logs -f

# Legacy aliases kept for backward compatibility
up: ml-up
down: ml-down

# =============================================================================
# CORE STACK
# =============================================================================

core-up:
	$(COMPOSE_CORE) up --build -d
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                     Soko Core Stack — Live                          ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@docker compose ps
	@echo ""
	@echo "► SERVICES & DOCS"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  API Gateway          →  http://localhost"
	@echo "  Auth Service         →  http://localhost/auth/docs"
	@echo "  User Service         →  http://localhost/users/docs"
	@echo "  Produce Service      →  http://localhost/listings/docs"
	@echo "  Order Service        →  http://localhost/orders/docs"
	@echo "  Payment Service      →  http://localhost/payments/docs"
	@echo "  Message Service      →  http://localhost/message/docs"
	@echo "  Notification Service →  http://localhost/notifications/docs"
	@echo "  Blog Service         →  http://localhost/posts/docs"
	@echo "  USSD Service         →  http://localhost/ussd/ (no docs)"
	@echo ""
	@echo "► DATABASES (internal — reachable only within soko_net)"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  auth_db         →  postgresql://auth_user:auth_pass@auth_db:5432/auth_db"
	@echo "  user_db         →  postgresql://user_user:user_pass@user_db:5432/user_db"
	@echo "  produce_db      →  postgresql://produce_user:produce_pass@produce_db:5432/produce_db"
	@echo "  order_db        →  postgresql://order_user:order_pass@order_db:5432/order_db"
	@echo "  payment_db      →  postgresql://payment_user:payment_pass@payment_db:5432/payment_db"
	@echo "  message_db      →  postgresql://message_user:message_pass@message_db:5432/message_db"
	@echo "  notification_db →  postgresql://notification_user:notification_pass@notification_db:5432/notification_db"
	@echo "  blog_db         →  postgresql://blog_user:blog_pass@blog_db:5432/blog_db"
	@echo "  ussd_db         →  postgresql://ussd_user:ussd_pass@ussd_db:5432/ussd_db"
	@echo "  redis           →  redis://redis:6379"
	@echo ""
	@echo "► ML GATEWAY (if ml stack is running)"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  Price predictions  →  http://localhost/ml/price/predict"
	@echo "  Recommendations    →  http://localhost/ml/recommend/"
	@echo ""

core-down:
	$(COMPOSE_CORE) down

core-restart: core-down core-up

core-logs:
	$(COMPOSE_CORE) logs -f

# =============================================================================
# ML — SETUP (local Python, training)
# =============================================================================

install:
	python3.12 -m venv $(PRICE_VENV)   && $(PRICE_VENV)/bin/pip install -q --timeout 120 -r $(ML_DIR)/price-prediction-service/requirements.txt
	python3.12 -m venv $(REC_VENV)     && $(REC_VENV)/bin/pip install -q --timeout 120 -r $(ML_DIR)/recommendation-service/requirements.txt
	python3.12 -m venv $(GATEWAY_VENV) && $(GATEWAY_VENV)/bin/pip install -q --timeout 120 -r $(ML_DIR)/ml-gateway-service/requirements.txt
	python3.12 -m venv $(AGENT_VENV)   && $(AGENT_VENV)/bin/pip install -q --timeout 120 -r $(ML_DIR)/kafka-agent/requirements.txt
	python3.12 -m venv $(DATA_VENV)    && $(DATA_VENV)/bin/pip install -q --timeout 120 -r $(ML_DIR)/data-generator/requirements.txt
	python3.12 -m venv $(INGEST_VENV)  && $(INGEST_VENV)/bin/pip install -q --timeout 120 -r $(ML_DIR)/data-ingestion-service/requirements.txt
	python3.12 -m venv $(LOC_VENV)     && $(LOC_VENV)/bin/pip install -q --timeout 120 -r $(ML_DIR)/location-service/requirements.txt
	@echo "All ML dependencies installed."
	@echo "Installing CmdStan 2.33.1 into Prophet's internal path (one-time, ~400 MB)..."
	$(PRICE_VENV)/bin/python $(ML_DIR)/install_cmdstan.py

generate-data:
	@mkdir -p $(ML_DIR)/recommendation-service/data/raw
	OUTPUT_DIR=$(abspath $(ML_DIR)/recommendation-service/data/raw) \
	  $(DATA_VENV)/bin/python $(ML_DIR)/data-generator/generate_prices.py
	OUTPUT_DIR=$(abspath $(ML_DIR)/recommendation-service/data/raw) \
	  $(DATA_VENV)/bin/python $(ML_DIR)/data-generator/generate_profiles.py
	@echo "Data generated in $(ML_DIR)/recommendation-service/data/raw/"

train:
	@mkdir -p $(ML_DIR)/price-prediction-service/models
	cd $(ML_DIR)/price-prediction-service && \
	  MODEL_DIR=$(abspath $(ML_DIR)/price-prediction-service/models) \
	  DATA_DIR=$(abspath $(ML_DIR)/recommendation-service/data/raw) \
	  $(abspath $(PRICE_VENV))/bin/python -c \
	  "from src.predictor import train_all_models; train_all_models()"
	@echo "Models trained → $(ML_DIR)/price-prediction-service/models/"

# =============================================================================
# ML — DEVELOPMENT (local uvicorn with hot-reload)
# =============================================================================

dev:
	$(COMPOSE_DEV) up --build

dev-price:
	cd $(ML_DIR)/price-prediction-service && \
	  $(abspath $(PRICE_VENV))/bin/uvicorn src.main:app --host 0.0.0.0 --port 8094 --reload

dev-rec:
	cd $(ML_DIR)/recommendation-service && \
	  $(abspath $(REC_VENV))/bin/uvicorn src.main:app --host 0.0.0.0 --port 8095 --reload

dev-gateway:
	cd $(ML_DIR)/ml-gateway-service && \
	  $(abspath $(GATEWAY_VENV))/bin/uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload

dev-location:
	cd $(ML_DIR)/location-service && \
	  $(abspath $(LOC_VENV))/bin/uvicorn src.main:app --host 0.0.0.0 --port 8003 --reload

dev-ingest:
	cd $(ML_DIR)/data-ingestion-service && \
	  $(abspath $(INGEST_VENV))/bin/uvicorn src.main:app --host 0.0.0.0 --port 8096 --reload

# =============================================================================
# ML — DATABASE (Feature Store)
# =============================================================================

db-up:
	$(COMPOSE_ML) up -d soko-ml-db db-init
	@echo "Waiting for schema init to complete..."
	@sleep 5
	@$(COMPOSE_ML) logs db-init

db-shell:
	$(COMPOSE_ML) exec soko-ml-db psql -U $${POSTGRES_USER:-soko_ml} -d soko_ml_db

db-reset:
	@echo "WARNING: This will drop and re-apply the schema. Press Ctrl-C to abort (5s)..."
	@sleep 5
	$(COMPOSE_ML) exec -T soko-ml-db psql -U $${POSTGRES_USER:-soko_ml} -d soko_ml_db \
	  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
	$(COMPOSE_ML) exec -T soko-ml-db psql -U $${POSTGRES_USER:-soko_ml} -d soko_ml_db \
	  -f /schema/schema.sql
	@echo "Schema reset complete."

# =============================================================================
# ML — DATA INGESTION
# =============================================================================

ingest-bootstrap:
	@curl -sf -X POST http://localhost:8096/bootstrap | python3 -m json.tool || \
	  curl -sf -X POST http://localhost:8080/ingest/bootstrap | python3 -m json.tool

ingest-status:
	@curl -sf http://localhost:8096/bootstrap/status | python3 -m json.tool || \
	  curl -sf http://localhost:8080/ingest/status | python3 -m json.tool

gaps-summary:
	@curl -sf http://localhost:8096/gaps/summary | python3 -m json.tool || \
	  curl -sf http://localhost:8080/gaps/summary | python3 -m json.tool

gaps-reset:
	@$(COMPOSE_ML) exec -T soko-ml-db psql -U $${POSTGRES_USER:-soko_ml} -d soko_ml_db \
	  -c "TRUNCATE coverage_gaps;"
	@echo "Gap counters reset."

cold-start: bridge-network db-up
	@echo "Waiting for DB to be fully ready..."
	@sleep 10
	$(COMPOSE_ML) up --build -d
	@echo "Stack up — triggering bootstrap..."
	@sleep 20
	@$(MAKE) ingest-bootstrap
	@echo ""
	@echo "Cold start complete:"
	@echo "  ML gateway  → http://localhost:8080"
	@echo "  Ingest      → http://localhost:8096/docs"
	@echo "  Location    → http://localhost:8003/docs"

# =============================================================================
# ML — INFRASTRUCTURE HELPERS
# =============================================================================

infra-up:
	$(COMPOSE_ML) up -d zookeeper kafka kafka-init redis soko-ml-db db-init
	@echo "ML infrastructure starting (Kafka may take ~30s to be ready)."

infra-down:
	$(COMPOSE_ML) stop zookeeper kafka kafka-init redis soko-ml-db db-init
	$(COMPOSE_ML) rm -f zookeeper kafka kafka-init redis soko-ml-db db-init

kafka-topics:
	$(COMPOSE_ML) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.transactions    --partitions 6 --replication-factor 1
	$(COMPOSE_ML) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.interactions   --partitions 6 --replication-factor 1
	$(COMPOSE_ML) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.price.requests --partitions 3 --replication-factor 1
	$(COMPOSE_ML) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.price.results  --partitions 3 --replication-factor 1
	$(COMPOSE_ML) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.ml.events      --partitions 2 --replication-factor 1
	$(COMPOSE_ML) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.dlq            --partitions 2 --replication-factor 1
	$(COMPOSE_ML) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.gaps           --partitions 2 --replication-factor 1
	@echo "All Kafka topics created."

kafka-ui:
	$(COMPOSE_ML) exec kafka kafka-topics --bootstrap-server localhost:9092 --list

redis-cli:
	$(COMPOSE_ML) exec redis redis-cli

# =============================================================================
# ML — LOGGING
# =============================================================================

logs:
	$(COMPOSE_ML) logs -f

logs-price:
	$(COMPOSE_ML) logs -f price-prediction-service

logs-rec:
	$(COMPOSE_ML) logs -f recommendation-service

logs-gateway:
	$(COMPOSE_ML) logs -f ml-gateway-service

logs-agent:
	$(COMPOSE_ML) logs -f kafka-agent

logs-location:
	$(COMPOSE_ML) logs -f location-service

logs-ingest:
	$(COMPOSE_ML) logs -f data-ingestion-service

# =============================================================================
# TESTING
# =============================================================================

test: test-price test-rec test-gateway test-location test-ingest

test-price:
	$(PRICE_VENV)/bin/pytest $(ML_DIR)/price-prediction-service/tests/ -v

test-rec:
	$(REC_VENV)/bin/pytest $(ML_DIR)/recommendation-service/tests/ -v

test-gateway:
	$(GATEWAY_VENV)/bin/pytest $(ML_DIR)/ml-gateway-service/tests/ -v

test-location:
	$(LOC_VENV)/bin/pytest $(ML_DIR)/location-service/tests/ -v

test-ingest:
	$(INGEST_VENV)/bin/pytest $(ML_DIR)/data-ingestion-service/tests/ -v

# =============================================================================
# HEALTH & SMOKE
# =============================================================================

health:
	@echo "=== API Gateway ===" && \
	  curl -sf http://localhost/health || echo "UNREACHABLE"
	@echo "=== ML Gateway ===" && \
	  curl -sf http://localhost:8080/health | python3 -m json.tool || echo "UNREACHABLE"
	@echo "=== Price Service ===" && \
	  curl -sf http://localhost:8094/health | python3 -m json.tool || echo "UNREACHABLE"
	@echo "=== Recommendation Service ===" && \
	  curl -sf http://localhost:8095/health | python3 -m json.tool || echo "UNREACHABLE"
	@echo "=== Location Service ===" && \
	  curl -sf http://localhost:8003/health | python3 -m json.tool || echo "UNREACHABLE"
	@echo "=== Data Ingestion Service ===" && \
	  curl -sf http://localhost:8096/health | python3 -m json.tool || echo "UNREACHABLE"

smoke-test:
	@python3 scripts/smoke_test.py

smoke-route:
	@echo "=== Smoke: Market Route (farmer sell signal) ==="
	@curl -sf -X POST http://localhost:8080/location/route \
	  -H 'Content-Type: application/json' \
	  -d '{"farmer_id":"F0001","crop":"maize_grain","quantity_kg":500,"harvest_month":8}' \
	  | python3 -m json.tool

smoke-discover:
	@echo "=== Smoke: Discover Farmers Near Buyer ==="
	@curl -sf -X POST http://localhost:8080/location/discover \
	  -H 'Content-Type: application/json' \
	  -d '{"buyer_id":"B0001","crop":"maize_grain","max_distance_km":150,"max_price_ugx":2000,"top_n":5}' \
	  | python3 -m json.tool

smoke-fallback:
	@echo "=== Smoke: Tier 2 fallback (sesame seed — limited coverage) ==="
	@curl -sf -X POST http://localhost:8080/location/route \
	  -H 'Content-Type: application/json' \
	  -d '{"farmer_id":"F0001","crop":"sesame","quantity_kg":200,"harvest_month":10}' \
	  | python3 -m json.tool

smoke-tier3:
	@echo "=== Smoke: Tier 3 unknown crop ==="
	@curl -sf -X POST http://localhost:8080/location/route \
	  -H 'Content-Type: application/json' \
	  -d '{"farmer_id":"F0001","crop":"moringa","quantity_kg":50,"harvest_month":6}' \
	  | python3 -m json.tool

smoke-ingest:
	@echo "=== Smoke: POST synthetic order event to ingest ==="
	@curl -sf -X POST http://localhost:8096/ingest/order-event \
	  -H 'Content-Type: application/json' \
	  -d '{"event_type":"purchase_completed","order_id":"TEST-001","product_name":"Maize (Dry)","crop":"Grains","market":"Kampala","price_per_kg_ugx":1400,"quantity_kg":50,"total_ugx":70000,"farmer_id":"F0001","buyer_id":"B0001","timestamp":"2026-05-14T10:00:00Z"}' \
	  | python3 -m json.tool

# =============================================================================
# CLEAN
# =============================================================================

clean:
	find $(ML_DIR) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find $(ML_DIR) -name "*.pyc" -delete 2>/dev/null || true
	rm -rf $(PRICE_VENV) $(REC_VENV) $(GATEWAY_VENV) $(AGENT_VENV) $(DATA_VENV) $(INGEST_VENV) $(LOC_VENV)
	rm -f $(ML_DIR)/recommendation-service/data/raw/*.csv
	@echo "Cleaned."

clean-models:
	rm -f $(ML_DIR)/price-prediction-service/models/*.pkl
	@echo "Model files removed."

clean-docker:
	$(COMPOSE_ML) down -v --rmi all
	$(COMPOSE_CORE) down --rmi all
	@echo "All containers, volumes, and images removed."

# =============================================================================
# REFERENCE
# =============================================================================

port-reference:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║              Soko — Port Reference (container → host)               ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "► CORE STACK  (network: soko_net — internal container ports only)"
	@echo "  ─────────────────────────────────────────────────────────────────"
	@echo "  nginx (API gateway)      container :80    → host :80"
	@echo "  auth-service             container :8001  → host: NOT EXPOSED"
	@echo "  user-service             container :8002  → host: NOT EXPOSED"
	@echo "  produce-service          container :8003  → host: NOT EXPOSED"
	@echo "  order-service            container :8004  → host: NOT EXPOSED"
	@echo "  payment-service          container :8005  → host: NOT EXPOSED"
	@echo "  message-service          container :8006  → host: NOT EXPOSED"
	@echo "  notification-service     container :8007  → host: NOT EXPOSED"
	@echo "  blog-service             container :8008  → host: NOT EXPOSED"
	@echo "  ussd-service             container :8009  → host: NOT EXPOSED"
	@echo "  core Redis               container :6379  → host: NOT EXPOSED"
	@echo "  core PostgreSQL ×9       container :5432  → host: NOT EXPOSED"
	@echo ""
	@echo "► ML STACK  (network: soko-ml-network; bridge: soko-ml-bridge)"
	@echo "  ─────────────────────────────────────────────────────────────"
	@echo "  ml-gateway-service       container :8000  → host :8080  (production)"
	@echo "  price-prediction-service container :8001  → host :8094  (dev only)"
	@echo "  recommendation-service   container :8002  → host :8095  (dev only)"
	@echo "  location-service         container :8003  → host :8003"
	@echo "  data-ingestion-service   container :8004  → host :8096  (dev only)"
	@echo ""
	@echo "► INFRASTRUCTURE  (ML stack — internal only)"
	@echo "  ─────────────────────────────────────────────────────────────"
	@echo "  Kafka                    container :9092  → host: NOT EXPOSED"
	@echo "  Zookeeper                container :2181  → host: NOT EXPOSED"
	@echo "  ML Redis                 container :6379  → host: NOT EXPOSED"
	@echo "  soko-ml-db (PostgreSQL)  container :5432  → host: NOT EXPOSED"
	@echo ""
	@echo "  NOTE: 'NOT EXPOSED' = reachable only within the Docker network."
	@echo "        Dev-only ports are mapped by docker-compose.dev.yml / make dev-*."
	@echo ""

# =============================================================================
# SEED & DESTROY
# =============================================================================

fill-envs:
	@python3 scripts/fill_envs.py

seed: fill-envs
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                     Soko — Seeding all services                     ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@echo "Both stacks must be running: make start"
	@echo ""
	@python3 scripts/seed.py

destroy-seed:
	@echo ""
	@echo "╔══════════════════════════════════════════════════════════════════════╗"
	@echo "║                   Soko — Destroying seed data                       ║"
	@echo "╚══════════════════════════════════════════════════════════════════════╝"
	@echo ""
	@python3 scripts/destroy_seed.py
