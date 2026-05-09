# Soko ML Services

Production-grade ML layer for the Soko digital marketplace — serving Ugandan farmers and buyers.

## Services

| Service | Port | Description |
|---|---|---|
| `ml-gateway-service` | 8000 | Single entry point for all ML capabilities |
| `price-prediction-service` | 8001 | Prophet-based crop price forecasting (UGX) |
| `recommendation-service` | 8002 | Content-based farmer/buyer matching |
| `kafka-agent` | — | Event backbone; bridges Soko transactions with ML layer |

## Prerequisites

- Docker + Docker Compose
- Python 3.11 (for local dev)
- Make

## Quick Start

```bash
# 1. Copy environment config
cp .env.example .env

# 2. Install local Python deps (for data generation and training)
make install

# 3. Generate synthetic training data
make generate-data

# 4. Train Prophet price models
make train

# 5. Start full stack
make up
```

Gateway is now live at **http://localhost:8000**.

## Endpoints

All external callers hit the gateway only — never the services directly.

### Price Prediction

```bash
# Predict maize prices in Kisenyi_Kampala for next 4 weeks
curl -X POST http://localhost:8000/price/predict \
  -H 'Content-Type: application/json' \
  -d '{"market": "Kisenyi_Kampala", "crop": "maize_grain", "weeks_ahead": 4}'

# List supported markets
curl http://localhost:8000/price/markets

# List supported crops
curl http://localhost:8000/price/crops
```

**Supported markets:** Kisenyi_Kampala, Gulu, Mbarara, Mbale, Lira, Masaka

**Supported crops:** maize_grain, yellow_beans, irish_potatoes, tomatoes, matoke, cassava_chips, sorghum, millet

### Recommendations

```bash
# Top 5 farmers for buyer B0001
curl "http://localhost:8000/recommend/farmers-for-buyer/B0001?top_n=5"

# Top 5 buyers for farmer F0001
curl "http://localhost:8000/recommend/buyers-for-farmer/F0001?top_n=5"
```

### Health

```bash
# Aggregated health of all downstream services
curl http://localhost:8000/health
```

## Makefile Reference

```
make install          Install all Python deps into local venvs
make generate-data    Generate CSVs in recommendation-service/data/raw/
make train            Train Prophet models → price-prediction-service/models/

make dev              Hot-reload full stack (docker-compose.dev.yml)
make dev-price        Run price-prediction-service locally without Docker
make dev-rec          Run recommendation-service locally without Docker
make dev-gateway      Run ml-gateway-service locally without Docker

make infra-up         Start Redis + Kafka + Zookeeper only
make infra-down       Stop infrastructure containers
make kafka-topics     Create all Kafka topics (idempotent)
make kafka-ui         List Kafka topics in terminal
make redis-cli        Open Redis CLI

make up               docker-compose up --build -d (full production stack)
make down             docker-compose down
make restart          down + up
make logs             Follow all service logs
make logs-price       Follow price-prediction-service logs
make logs-rec         Follow recommendation-service logs
make logs-gateway     Follow ml-gateway-service logs
make logs-agent       Follow kafka-agent logs

make test             Run all pytest suites
make test-price       price-prediction-service tests only
make test-rec         recommendation-service tests only
make test-gateway     ml-gateway-service tests only

make health           curl all /health endpoints
make smoke-test       End-to-end prediction + recommendation calls

make clean            Remove __pycache__, venvs, generated data
make clean-models     Remove trained .pkl files
make clean-docker     docker-compose down -v --rmi all
```

## Kafka Topics

| Topic | Partitions | Retention | Purpose |
|---|---|---|---|
| `soko.transactions` | 6 | 7 days | Purchase and order events |
| `soko.interactions` | 6 | 3 days | Views, inquiries, ratings |
| `soko.price.requests` | 3 | 1 day | Async price prediction requests |
| `soko.price.results` | 3 | 1 day | Async price prediction results |
| `soko.ml.events` | 2 | 14 days | Model lifecycle events |
| `soko.dlq` | 2 | 30 days | Dead-letter queue |

## Redis Cache Keys

| Key Pattern | TTL | Description |
|---|---|---|
| `price:v1:{market}:{crop}:{weeks}` | 24 h | Price prediction response |
| `rec:farmers:{buyer_id}:{top_n}` | 1 h | Farmer recommendations for buyer |
| `rec:buyers:{farmer_id}:{top_n}` | 1 h | Buyer recommendations for farmer |
| `model:meta:{market}:{crop}` | 7 days | Model training metadata |

## Architecture

```
Other Soko Services
        │
        ▼
 ml-gateway-service :8000
  ├── /price/predict  ──────► price-prediction-service :8001
  │                               │ loads Prophet .pkl from models/
  │                               │ Redis cache (24h TTL)
  │                               └ publishes to soko.price.results
  │
  └── /recommend/*   ──────► recommendation-service :8002
                                  │ content-based scoring
                                  │ Redis cache (1h TTL)
                                  └ Kafka consumer: soko.interactions

kafka-agent
  ├── consumes soko.transactions → enriches → soko.interactions
  ├── consumes soko.price.requests → calls price service → soko.price.results
  └── failed messages → soko.dlq
```

## Environment Variables

See `.env.example` for all configurable values. Key variables:

```
REDIS_HOST, REDIS_PORT       Redis connection
KAFKA_BOOTSTRAP_SERVERS      Kafka brokers
MODEL_DIR                    Path to .pkl model files
FARMERS_DATA_PATH            Path to farmers.csv
BUYERS_DATA_PATH             Path to buyers.csv
PRICE_CACHE_TTL_SECONDS      Price cache TTL (default 86400)
REC_CACHE_TTL_SECONDS        Recommendation cache TTL (default 3600)
LOG_LEVEL                    INFO or DEBUG
```

## Development

```bash
# Start infrastructure only (Redis + Kafka)
make infra-up

# Run a service locally with hot reload
make dev-price    # in one terminal
make dev-rec      # in another
make dev-gateway  # in another

# Run tests
make test
```
