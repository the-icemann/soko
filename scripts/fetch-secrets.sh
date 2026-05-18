#!/usr/bin/env bash
# Fetches soko/platform from AWS Secrets Manager and writes:
#   - /opt/soko/.env         (docker-compose variable substitution)
#   - services/<svc>/.env   (per-service env files)
#
# Requires: aws-cli v2, jq
# IAM: EC2 instance profile must have secretsmanager:GetSecretValue on soko/platform

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-af-south-1}"
SECRET_NAME="soko/platform"

echo "[fetch-secrets] Pulling $SECRET_NAME from Secrets Manager ($REGION)..."

RAW=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_NAME" \
  --region "$REGION" \
  --query "SecretString" \
  --output text)

# Helper: extract a key from the JSON
s() { echo "$RAW" | jq -r ".${1}"; }

# ── Root .env (docker-compose substitution) ───────────────────────────────────
# Docker Compose auto-loads this file for ${VAR} substitution
cat > "$REPO_DIR/.env" <<EOF
AUTH_DB_PASS=$(s AUTH_DB_PASS)
USER_DB_PASS=$(s USER_DB_PASS)
PRODUCE_DB_PASS=$(s PRODUCE_DB_PASS)
ORDER_DB_PASS=$(s ORDER_DB_PASS)
PAYMENT_DB_PASS=$(s PAYMENT_DB_PASS)
MESSAGE_DB_PASS=$(s MESSAGE_DB_PASS)
NOTIFICATION_DB_PASS=$(s NOTIFICATION_DB_PASS)
BLOG_DB_PASS=$(s BLOG_DB_PASS)
USSD_DB_PASS=$(s USSD_DB_PASS)
INTERNAL_SECRET=$(s INTERNAL_SECRET)
EOF
chmod 600 "$REPO_DIR/.env"
echo "[fetch-secrets] Root .env written."

# ── Auth Service ──────────────────────────────────────────────────────────────
cat > "$REPO_DIR/services/auth/.env" <<EOF
SECRET_KEY=$(s SECRET_KEY)
ALGORITHM=$(s ALGORITHM)
GOOGLE_CLIENT_ID=$(s GOOGLE_CLIENT_ID)
GOOGLE_CLIENT_SECRET=$(s GOOGLE_CLIENT_SECRET)
GOOGLE_REDIRECT_URI=https://$(s DOMAIN 2>/dev/null || echo "yourdomain.com")/auth/google/callback
FRONTEND_URL=https://$(s DOMAIN 2>/dev/null || echo "yourdomain.com")
INTERNAL_SECRET=$(s INTERNAL_SECRET)
USER_SERVICE_URL=http://user_service:8002
EOF
chmod 600 "$REPO_DIR/services/auth/.env"

# ── Payment Service ───────────────────────────────────────────────────────────
cat > "$REPO_DIR/services/payment/.env" <<EOF
INTERNAL_SECRET=$(s INTERNAL_SECRET)
PESAPAL_CONSUMER_KEY=$(s PESAPAL_CONSUMER_KEY)
PESAPAL_CONSUMER_SECRET=$(s PESAPAL_CONSUMER_SECRET)
PESAPAL_ENV=$(s PESAPAL_ENV)
PESAPAL_IPN_URL=https://$(s DOMAIN 2>/dev/null || echo "yourdomain.com")/payments/webhook/pesapal/ipn
PESAPAL_CALLBACK_URL=https://$(s DOMAIN 2>/dev/null || echo "yourdomain.com")/payments/callback
ORDER_SERVICE_URL=http://order_service:8004
USER_SERVICE_URL=http://user_service:8002
NOTIFICATION_SERVICE_URL=http://notification_service:8007
FRONTEND_URL=https://$(s DOMAIN 2>/dev/null || echo "yourdomain.com")
EOF
chmod 600 "$REPO_DIR/services/payment/.env"

# ── Notification Service ──────────────────────────────────────────────────────
cat > "$REPO_DIR/services/notification/.env" <<EOF
INTERNAL_SECRET=$(s INTERNAL_SECRET)
SECRET_KEY=$(s SECRET_KEY)
ALGORITHM=$(s ALGORITHM)
AT_USERNAME=$(s AT_USERNAME)
AT_API_KEY=$(s AT_API_KEY)
AT_SENDER_ID=$(s AT_SENDER_ID)
SENDGRID_API_KEY=$(s SENDGRID_API_KEY)
SENDGRID_FROM_EMAIL=$(s SENDGRID_FROM_EMAIL)
USER_SERVICE_URL=http://user_service:8002
EOF
chmod 600 "$REPO_DIR/services/notification/.env"

# ── USSD Service ──────────────────────────────────────────────────────────────
cat > "$REPO_DIR/services/ussd/.env" <<EOF
INTERNAL_SECRET=$(s INTERNAL_SECRET)
AT_USERNAME=$(s AT_USERNAME)
AT_API_KEY=$(s AT_API_KEY)
PRODUCE_SERVICE_URL=http://produce_service:8003
ORDER_SERVICE_URL=http://order_service:8004
AUTH_SERVICE_URL=http://auth_service:8001
USER_SERVICE_URL=http://user_service:8002
NOTIFICATION_SERVICE_URL=http://notification_service:8007
EOF
chmod 600 "$REPO_DIR/services/ussd/.env"

# ── Message Service ───────────────────────────────────────────────────────────
cat > "$REPO_DIR/services/message/.env" <<EOF
INTERNAL_SECRET=$(s INTERNAL_SECRET)
USER_SERVICE_URL=http://user_service:8002
PRODUCE_SERVICE_URL=http://produce_service:8003
NOTIFICATION_SERVICE_URL=http://notification_service:8007
EOF
chmod 600 "$REPO_DIR/services/message/.env"

# ── User / Produce / Order / Blog — only need INTERNAL_SECRET ─────────────────
for svc in user produce order blog; do
  cat > "$REPO_DIR/services/$svc/.env" <<EOF
INTERNAL_SECRET=$(s INTERNAL_SECRET)
EOF
  chmod 600 "$REPO_DIR/services/$svc/.env"
done

echo "[fetch-secrets] All service .env files written successfully."
