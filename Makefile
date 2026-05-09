# ─────────────────────────────────────────────────────────────────────────────
# Soko — root Makefile
# Covers the ML layer living in services/soko-ml/.
# All docker-compose commands target that sub-stack exclusively.
# ─────────────────────────────────────────────────────────────────────────────

ML_DIR       := services/soko-ml
COMPOSE      := docker compose -f $(ML_DIR)/docker-compose.yml --project-directory $(ML_DIR)
COMPOSE_DEV  := docker compose \
                  -f $(ML_DIR)/docker-compose.yml \
                  -f $(ML_DIR)/docker-compose.dev.yml \
                  --project-directory $(ML_DIR)

PRICE_VENV   := $(ML_DIR)/price-prediction-service/.venv
REC_VENV     := $(ML_DIR)/recommendation-service/.venv
GATEWAY_VENV := $(ML_DIR)/ml-gateway-service/.venv
AGENT_VENV   := $(ML_DIR)/kafka-agent/.venv
DATA_VENV    := $(ML_DIR)/data-generator/.venv

.PHONY: install generate-data train \
        bridge-network \
        dev dev-price dev-rec dev-gateway \
        infra-up infra-down kafka-topics kafka-ui redis-cli \
        up down restart \
        logs logs-price logs-rec logs-gateway logs-agent \
        test test-price test-rec test-gateway \
        health smoke-test \
        clean clean-models clean-docker

# ── SHARED NETWORK ────────────────────────────────────────────────────────────
# soko-ml-bridge lets core services (order, produce) reach Kafka and ml-gateway.
# Run once before starting either stack; safe to re-run (exits 0 if exists).

bridge-network:
	docker network create soko-ml-bridge 2>/dev/null || true
	@echo "Bridge network soko-ml-bridge ready."

# ── SETUP ─────────────────────────────────────────────────────────────────────

install:
	python3.12 -m venv $(PRICE_VENV)   && $(PRICE_VENV)/bin/pip install -q -r $(ML_DIR)/price-prediction-service/requirements.txt
	python3.12 -m venv $(REC_VENV)     && $(REC_VENV)/bin/pip install -q -r $(ML_DIR)/recommendation-service/requirements.txt
	python3.12 -m venv $(GATEWAY_VENV) && $(GATEWAY_VENV)/bin/pip install -q -r $(ML_DIR)/ml-gateway-service/requirements.txt
	python3.12 -m venv $(AGENT_VENV)   && $(AGENT_VENV)/bin/pip install -q -r $(ML_DIR)/kafka-agent/requirements.txt
	python3.12 -m venv $(DATA_VENV)    && $(DATA_VENV)/bin/pip install -q -r $(ML_DIR)/data-generator/requirements.txt
	@echo "All ML dependencies installed."
	@echo "Installing CmdStan 2.33.1 into Prophet's internal path (one-time, ~400 MB)..."
	$(PRICE_VENV)/bin/python -c " \
	  import prophet, pathlib, cmdstanpy; \
	  d = pathlib.Path(prophet.__file__).parent / 'stan_model'; \
	  target = d / 'cmdstan-2.33.1'; \
	  d.mkdir(parents=True, exist_ok=True); \
	  (print('CmdStan 2.33.1 already present, skipping.') if (target / 'Makefile').exists() \
	   else (print('Downloading + compiling CmdStan 2.33.1...'), \
	         cmdstanpy.install_cmdstan(dir=str(d), version='2.33.1'), \
	         print('CmdStan 2.33.1 installed.')))"

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

# ── DEVELOPMENT ───────────────────────────────────────────────────────────────

dev:
	$(COMPOSE_DEV) up --build

dev-price:
	cd $(ML_DIR)/price-prediction-service && \
	  $(abspath $(PRICE_VENV))/bin/uvicorn src.main:app --host 0.0.0.0 --port 8081 --reload

dev-rec:
	cd $(ML_DIR)/recommendation-service && \
	  $(abspath $(REC_VENV))/bin/uvicorn src.main:app --host 0.0.0.0 --port 8082 --reload

dev-gateway:
	cd $(ML_DIR)/ml-gateway-service && \
	  $(abspath $(GATEWAY_VENV))/bin/uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload

# ── INFRASTRUCTURE ────────────────────────────────────────────────────────────

infra-up:
	$(COMPOSE) up -d zookeeper kafka kafka-init redis
	@echo "ML infrastructure starting (Kafka may take ~30s to be ready)."

infra-down:
	$(COMPOSE) stop zookeeper kafka kafka-init redis
	$(COMPOSE) rm -f zookeeper kafka kafka-init redis

kafka-topics:
	$(COMPOSE) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.transactions    --partitions 6 --replication-factor 1
	$(COMPOSE) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.interactions   --partitions 6 --replication-factor 1
	$(COMPOSE) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.price.requests --partitions 3 --replication-factor 1
	$(COMPOSE) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.price.results  --partitions 3 --replication-factor 1
	$(COMPOSE) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.ml.events      --partitions 2 --replication-factor 1
	$(COMPOSE) exec kafka kafka-topics --bootstrap-server localhost:9092 \
	  --create --if-not-exists --topic soko.dlq            --partitions 2 --replication-factor 1
	@echo "All Kafka topics created."

kafka-ui:
	$(COMPOSE) exec kafka kafka-topics --bootstrap-server localhost:9092 --list

redis-cli:
	$(COMPOSE) exec redis redis-cli

# ── PRODUCTION ────────────────────────────────────────────────────────────────

up: bridge-network
	$(COMPOSE) up --build -d
	@echo "ML stack live → http://localhost:8080  (gateway)"
	@echo "               http://localhost:8081  (price-prediction)"
	@echo "               http://localhost:8082  (recommendation)"

down:
	$(COMPOSE) down -v

restart: down up

logs:
	$(COMPOSE) logs -f

logs-price:
	$(COMPOSE) logs -f price-prediction-service

logs-rec:
	$(COMPOSE) logs -f recommendation-service

logs-gateway:
	$(COMPOSE) logs -f ml-gateway-service

logs-agent:
	$(COMPOSE) logs -f kafka-agent

# ── TESTING ───────────────────────────────────────────────────────────────────

test: test-price test-rec test-gateway

test-price:
	$(PRICE_VENV)/bin/pytest $(ML_DIR)/price-prediction-service/tests/ -v

test-rec:
	$(REC_VENV)/bin/pytest $(ML_DIR)/recommendation-service/tests/ -v

test-gateway:
	$(GATEWAY_VENV)/bin/pytest $(ML_DIR)/ml-gateway-service/tests/ -v

# ── HEALTH & SMOKE ────────────────────────────────────────────────────────────

health:
	@echo "=== ML Gateway ===" && \
	  curl -sf http://localhost:8080/health | python3 -m json.tool || echo "UNREACHABLE"
	@echo "=== Price Service ===" && \
	  curl -sf http://localhost:8081/health | python3 -m json.tool || echo "UNREACHABLE"
	@echo "=== Recommendation Service ===" && \
	  curl -sf http://localhost:8082/health | python3 -m json.tool || echo "UNREACHABLE"

smoke-test:
	@echo "=== Smoke: Price Prediction ==="
	@curl -sf -X POST http://localhost:8080/price/predict \
	  -H 'Content-Type: application/json' \
	  -d '{"market":"Kisenyi_Kampala","crop":"maize_grain","weeks_ahead":4}' \
	  | python3 -m json.tool
	@echo "=== Smoke: Farmers for Buyer ==="
	@curl -sf "http://localhost:8080/recommend/farmers-for-buyer/B0001?top_n=3" | python3 -m json.tool
	@echo "=== Smoke: Buyers for Farmer ==="
	@curl -sf "http://localhost:8080/recommend/buyers-for-farmer/F0001?top_n=3" | python3 -m json.tool

# ── CLEAN ─────────────────────────────────────────────────────────────────────

clean:
	find $(ML_DIR) -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find $(ML_DIR) -name "*.pyc" -delete 2>/dev/null || true
	rm -rf $(PRICE_VENV) $(REC_VENV) $(GATEWAY_VENV) $(AGENT_VENV) $(DATA_VENV)
	rm -f $(ML_DIR)/recommendation-service/data/raw/*.csv
	@echo "Cleaned."

clean-models:
	rm -f $(ML_DIR)/price-prediction-service/models/*.pkl
	@echo "Model files removed."

clean-docker:
	$(COMPOSE) down -v --rmi all
	@echo "ML Docker containers, volumes, and images removed."
