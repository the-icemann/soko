# Soko — Pre-Deployment Checklist

Use this checklist before every production deployment. Work through each section top-to-bottom. All items must be checked before the Go/No-Go decision at the end.

---

## 1. Security Fixes Verification

Confirm that all security-critical fixes from the ML integration overhaul are present and tested.

- [ ] **JWT auth enforced at gateway** — every protected route returns `401` when called without a valid token
  ```bash
  curl -o /dev/null -sw "%{http_code}" http://localhost/users/me
  # expected: 401
  ```
- [ ] **Identity header injection validated** — `X-User-Id` and `X-User-Role` are injected by Nginx, never accepted from external clients
  ```bash
  curl -H "X-User-Id: 999" http://localhost/users/me
  # must return 401, not the spoofed user's data
  ```
- [ ] **Recommendation endpoint enforces own-account access** — a user cannot fetch another user's recommendations
  ```bash
  # Login as user A, try to GET /ml/recommend/{user_B_id} — must return 403
  ```
- [ ] **Header injection hardened** — confirm `proxy_set_header X-User-Id ""` in nginx.conf clears any client-supplied value before the subrequest
- [ ] **Internal token verify endpoint not reachable externally** — `/verify-token` returns 404 from outside the container network
  ```bash
  curl -o /dev/null -sw "%{http_code}" http://localhost/verify-token
  # expected: 404
  ```
- [ ] **INTERNAL_SECRET set and consistent** — same value across all core service `.env` files

**Rollback:** Revert nginx.conf and service configs to last known-good state; redeploy core stack only.

---

## 2. Bug Fixes Verification

Confirm all production bug fixes from the ML integration phase are present.

- [ ] **Service URLs use Docker DNS names** — no `localhost` in inter-service HTTP calls (e.g. `USER_SERVICE_URL=http://user_service:8002`)
- [ ] **Environment variables loaded at runtime** — no hardcoded secrets in source files; all sensitive values come from `.env`
- [ ] **Docker Compose `depends_on` health checks** — ML services wait for `soko-ml-db` and Kafka before starting
- [ ] **ml-gateway circuit breakers configured** — timeout and retry settings present in `ml-gateway-service` config
- [ ] **Kafka consumer group IDs unique per service** — price-prediction, recommendation, and kafka-agent use distinct `group_id` values

**Rollback:** Roll back the affected service image tag; `docker compose up -d <service>`.

---

## 3. Environment Configuration

- [ ] All core service `.env` files exist (run `make setup` if missing)
  ```bash
  ls services/auth/.env services/user/.env services/produce/.env \
     services/order/.env services/payment/.env services/message/.env \
     services/notification/.env services/blog/.env services/ussd/.env
  ```
- [ ] `SECRET_KEY` set in `services/auth/.env` (strong, production-grade value)
- [ ] `FRONTEND_URL` set in `services/auth/.env` (exact origin used by the web client)
- [ ] `INTERNAL_SECRET` set to the same value in every core service `.env`
- [ ] `services/soko-ml/.env` exists and is fully populated
- [ ] `PESAPAL_CONSUMER_KEY` and `PESAPAL_CONSUMER_SECRET` set (payment service)
- [ ] `CLOUDINARY_*` keys set (produce service image uploads)
- [ ] `AT_USERNAME` and `AT_API_KEY` set in notification and ussd `.env` files
- [ ] No `.env` file contains placeholder values like `changeme` or `your_secret_here`

**Rollback:** Not applicable — env changes do not affect running containers until restart.

---

## 4. Core Stack Services

Verify all 9 core services build and start cleanly.

- [ ] `make core-up` completes without errors
- [ ] All 9 service containers show `healthy` in `docker compose ps`
  ```bash
  docker compose ps
  ```
- [ ] Nginx gateway responds on `:80`
  ```bash
  curl -sf http://localhost/health
  ```
- [ ] Auth service responds
  ```bash
  curl -sf http://localhost/auth/docs | grep -q "FastAPI"
  ```
- [ ] User, produce, order, payment, message, notification, blog services all return `200` on their `/docs` routes
- [ ] USSD service container is running (no HTTP docs endpoint, check via `docker compose ps`)

**Rollback:** `make core-down && make core-up` — stateless services restart cleanly. If a database migration is involved, see Section 6.

---

## 5. ML Stack Services

Verify all 5 ML services build and start cleanly.

- [ ] `make ml-up` completes without errors
- [ ] ml-gateway-service responds on host port `:8080`
  ```bash
  curl -sf http://localhost:8080/health | python3 -m json.tool
  ```
- [ ] price-prediction-service responds (via gateway)
  ```bash
  curl -sf http://localhost:8080/price/health | python3 -m json.tool
  ```
- [ ] recommendation-service responds (via gateway)
  ```bash
  curl -sf http://localhost:8080/recommend/health | python3 -m json.tool
  ```
- [ ] location-service responds on `:8003`
  ```bash
  curl -sf http://localhost:8003/health | python3 -m json.tool
  ```
- [ ] data-ingestion-service responds (via gateway)
  ```bash
  curl -sf http://localhost:8080/ingest/status | python3 -m json.tool
  ```
- [ ] kafka-agent container is running (no HTTP port — check via `docker compose -f services/soko-ml/docker-compose.yml ps`)

**Rollback:** `make ml-down && make ml-up`. ML services are stateless (state lives in Postgres/Redis/Kafka). Safe to restart independently.

---

## 6. Database Setup & Migrations

- [ ] All 9 core PostgreSQL databases initialised (auto-created by service startup)
- [ ] ML feature store schema applied
  ```bash
  make db-up
  # or verify:
  make db-shell
  # in psql: \dt
  ```
- [ ] ML Postgres tables present: `price_observations`, `user_profiles`, `interactions`, `coverage_gaps`
- [ ] No pending Alembic migrations on any core service
  ```bash
  docker compose exec auth_service alembic current
  docker compose exec user_service alembic current
  # repeat for each service
  ```
- [ ] Database credentials in `.env` match the `docker-compose.yml` `POSTGRES_*` values

**Rollback:** `make db-reset` drops and re-applies the ML schema (destructive). Core DB rollback requires per-service Alembic downgrade — do not run without explicit incident runbook.

---

## 7. Kafka Topics & Configuration

- [ ] All 7 required Kafka topics created
  ```bash
  make kafka-topics
  make kafka-ui
  # verify: soko.transactions, soko.interactions, soko.price.requests,
  #         soko.price.results, soko.ml.events, soko.dlq, soko.gaps
  ```
- [ ] `soko.transactions` has 6 partitions
- [ ] `soko.interactions` has 6 partitions
- [ ] `soko.price.requests` and `soko.price.results` have 3 partitions each
- [ ] `soko.ml.events`, `soko.dlq`, `soko.gaps` have 2 partitions each
- [ ] Kafka broker healthy (no `LEADER_NOT_AVAILABLE` errors in logs)
  ```bash
  docker compose -f services/soko-ml/docker-compose.yml logs kafka | tail -30
  ```
- [ ] Zookeeper healthy and Kafka connected to it

**Rollback:** Topics are append-only — no rollback needed unless partitioning is changed, which requires recreation of the topic and replay of events.

---

## 8. Redis Configuration

- [ ] Core Redis instance running (`redis` container in `soko_net`)
  ```bash
  docker compose exec redis redis-cli ping
  # expected: PONG
  ```
- [ ] ML Redis instance running (`redis` container in `soko-ml-network`)
  ```bash
  make redis-cli
  # in redis-cli: PING → PONG
  ```
- [ ] Core Redis reachable from auth and user services (`redis://redis:6379`)
- [ ] ML Redis reachable from price-prediction and recommendation services
- [ ] No Redis memory warnings in logs

**Rollback:** Redis is a cache — data loss on restart is acceptable. Both instances can be restarted without data loss risk to core business logic.

---

## 9. Networking & Docker Networks

- [ ] `soko_net` network exists
  ```bash
  docker network inspect soko_net | grep Name
  ```
- [ ] `soko-ml-network` network exists
  ```bash
  docker network inspect soko-ml-network | grep Name
  ```
- [ ] `soko-ml-bridge` network exists (created by `make setup`)
  ```bash
  docker network inspect soko-ml-bridge | grep Name
  # or: make setup (idempotent)
  ```
- [ ] Nginx container is attached to both `soko_net` and `soko-ml-bridge`
- [ ] ml-gateway-service container is attached to both `soko-ml-network` and `soko-ml-bridge`
- [ ] Core services (`auth`, `user`, etc.) are attached to `soko_net` only — not to `soko-ml-bridge`
- [ ] Cross-stack request succeeds (Nginx → ml-gateway)
  ```bash
  curl -sf http://localhost/ml/price/health
  ```

**Rollback:** `docker network create soko-ml-bridge` recreates the bridge. Restart both stacks afterward.

---

## 10. Feature Store Initialization

- [ ] ML Postgres `user_profiles` table populated (run bootstrap if empty)
  ```bash
  make ingest-bootstrap
  make ingest-status
  ```
- [ ] At least one user profile row present
  ```bash
  make db-shell
  # SELECT COUNT(*) FROM user_profiles;
  ```
- [ ] `price_observations` table populated with initial historical data (if running ML price models)
- [ ] `coverage_gaps` table not showing excessive gap counts
  ```bash
  make gaps-summary
  ```
- [ ] Recommendation service returns non-empty results for a seeded user
  ```bash
  # login as a seeded farmer/buyer, then:
  curl -H "Authorization: Bearer <token>" http://localhost/ml/recommend/<user_id>
  ```

**Rollback:** Re-run `make ingest-bootstrap`. If profiles are corrupt, `make db-reset` and re-bootstrap. This is non-destructive to the core stack.

---

## 11. API Gateway & Authentication

- [ ] Nginx starts and serves on `:80`
- [ ] `/_verify_token` subrequest reaches auth service and returns user identity headers
- [ ] JWT issued by `/auth/login` is accepted by a protected route (e.g. `/users/me`)
  ```bash
  TOKEN=$(curl -sf -X POST http://localhost/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"test@example.com","password":"password"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
  curl -sf -H "Authorization: Bearer $TOKEN" http://localhost/users/me
  ```
- [ ] Expired token returns `401`
- [ ] OAuth routes (`/oauth/`) are publicly accessible
- [ ] Nginx rate limiting active (30 req/min per IP)
- [ ] CORS headers present on responses (if web client uses a different origin)

**Rollback:** Reload nginx config only: `docker compose exec nginx nginx -s reload`. Full core restart is rarely needed for gateway issues.

---

## 12. ML Model Deployment

- [ ] Prophet price-prediction models (`.pkl` files) present in `services/soko-ml/price-prediction-service/models/`
  ```bash
  ls services/soko-ml/price-prediction-service/models/*.pkl | wc -l
  # should be > 0 (one per crop/market combination trained)
  ```
- [ ] Models were trained on recent data (check file modification timestamps)
  ```bash
  ls -lt services/soko-ml/price-prediction-service/models/*.pkl | head -5
  ```
- [ ] Price prediction returns a valid forecast for a known crop
  ```bash
  curl -sf -X POST http://localhost/ml/price/predict \
    -H "Content-Type: application/json" \
    -d '{"crop":"maize_grain","market":"Kampala","forecast_days":7}'
  ```
- [ ] Recommendation service returns scores (not empty list) for a seeded user
- [ ] Circuit breakers on ml-gateway are not tripped (no `503` from gateway health endpoint)

**Rollback:** If model files are missing or corrupt, re-run `make train`. The price service falls back to static averages if no model file is found — confirm fallback behaviour is acceptable for the deployment window.

---

## 13. Testing & Smoke Tests

- [ ] All ML unit tests pass
  ```bash
  make test
  ```
- [ ] Smoke: full prediction pipeline
  ```bash
  make smoke-test
  ```
- [ ] Smoke: market routing (Tier 1 crop)
  ```bash
  make smoke-route
  ```
- [ ] Smoke: buyer discover endpoint
  ```bash
  make smoke-discover
  ```
- [ ] Smoke: Tier 2 fallback (limited-coverage crop)
  ```bash
  make smoke-fallback
  ```
- [ ] Smoke: Tier 3 unknown crop graceful degradation
  ```bash
  make smoke-tier3
  ```
- [ ] Smoke: data-ingestion order event
  ```bash
  make smoke-ingest
  ```
- [ ] Full health check on all services
  ```bash
  make health
  ```

**Rollback:** Failing smoke tests after deployment indicate a regression. Immediately run `make stop` and restore the previous image tags before investigating.

---

## 14. Monitoring & Logging

- [ ] Centralized log aggregation configured (or Docker log driver set to a persistent driver)
- [ ] All containers logging to stdout (not writing to files inside the container)
- [ ] No `ERROR` or `CRITICAL` log lines appearing at steady state
  ```bash
  make ml-logs | grep -i "error\|critical" | head -20
  docker compose logs | grep -i "error\|critical" | head -20
  ```
- [ ] Kafka consumer lag monitored (no runaway lag on `soko.transactions`)
- [ ] Redis memory usage within acceptable bounds
  ```bash
  make redis-cli
  # INFO memory → used_memory_human
  ```
- [ ] ML gateway request latency acceptable (check `/health` response time)
  ```bash
  time curl -sf http://localhost:8080/health > /dev/null
  ```

**Rollback:** Logging configuration is outside the application — no rollback needed. Fix log driver settings and restart the affected containers.

---

## 15. Backup & Disaster Recovery

- [ ] Core PostgreSQL databases backed up before deployment
  ```bash
  for svc in auth user produce order payment message notification blog ussd; do
    docker compose exec ${svc}_db pg_dump -U ${svc}_user ${svc}_db > backups/${svc}_db_$(date +%Y%m%d).sql
  done
  ```
- [ ] ML feature store backed up
  ```bash
  docker compose -f services/soko-ml/docker-compose.yml exec soko-ml-db \
    pg_dump -U soko_ml soko_ml_db > backups/soko_ml_db_$(date +%Y%m%d).sql
  ```
- [ ] Backup files stored outside the Docker host (S3, remote NFS, or similar)
- [ ] Restore procedure documented and tested in staging
- [ ] Kafka topic retention policy set (default: 7 days) — confirmed acceptable for replay window

**Rollback:** Restore from the pre-deployment backup. Core and ML databases can be restored independently.

---

## 16. Production Hardening

- [ ] No default passwords remain in any `.env` file
- [ ] `SECRET_KEY` is a cryptographically random string (min 32 bytes)
- [ ] SSL/TLS termination configured (Nginx with a valid certificate, or an upstream load balancer)
- [ ] Nginx rate limiting confirmed active (`limit_req_zone` in nginx.conf)
- [ ] `INTERNAL_SECRET` used for all cross-service calls and not exposed in logs
- [ ] Docker socket not mounted into any service container
- [ ] Image tags pinned to specific versions (not `latest`) in production Compose files
- [ ] Container resource limits (`mem_limit`, `cpus`) set in Compose files
- [ ] No development ports (`8094`, `8095`, `8096`) exposed in the production Compose file
- [ ] PesaPal webhook URL configured to the production hostname (not `localhost`)

**Rollback:** Hardening changes are config-only. Revert the relevant `.env` or `docker-compose.yml` line and redeploy the affected service.

---

## 17. Go/No-Go Decision Framework

Complete this section as the final gate before traffic is directed to the deployment.

### Pre-conditions (all must be true)

- [ ] Sections 1–16 above are fully checked with no open items
- [ ] At least one team member has reviewed this checklist independently
- [ ] A rollback plan is documented and the rollback person is identified and available
- [ ] A maintenance window has been communicated to users (if applicable)
- [ ] The on-call engineer is reachable for the 2-hour post-deployment watch period

### Decision

| Decision | Condition |
|---|---|
| **GO** | All pre-conditions met; all smoke tests pass |
| **NO-GO** | Any section has an open item; any smoke test fails; on-call unavailable |

**Approver:** ___________________________  **Date/Time:** ___________________________

---

## 18. Post-Deployment Monitoring

Run these checks in the 2 hours following deployment.

- [ ] **T+5 min** — all container health checks green (`docker compose ps`)
- [ ] **T+5 min** — ML gateway `/health` returns `200` with all downstream services `healthy`
  ```bash
  curl -sf http://localhost:8080/health | python3 -m json.tool
  ```
- [ ] **T+10 min** — first real user JWT successfully validated (check auth service logs)
- [ ] **T+15 min** — Kafka `soko.transactions` consumer lag not growing (run `make kafka-ui` and compare partition offsets)
- [ ] **T+30 min** — price prediction responding within SLA (< 2 s p95)
- [ ] **T+30 min** — recommendation service responding within SLA (< 1 s p95)
- [ ] **T+60 min** — no spike in `ERROR` log lines relative to pre-deployment baseline
- [ ] **T+120 min** — declare deployment stable; remove rollback readiness posture

**If any check fails:** Immediately execute the rollback procedure documented in the relevant section above, then open a post-incident review.

---

*Checklist maintained alongside `README.md` — keep both in sync when adding new services or ports.*
