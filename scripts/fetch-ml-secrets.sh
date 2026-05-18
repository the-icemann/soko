#!/usr/bin/env bash
# Fetches soko/ml from AWS Secrets Manager and writes:
#   - services/soko-ml/.env  (ML docker-compose substitution)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REGION="${AWS_DEFAULT_REGION:-af-south-1}"
SECRET_NAME="soko/ml"

echo "[fetch-ml-secrets] Pulling $SECRET_NAME from Secrets Manager ($REGION)..."

RAW=$(aws secretsmanager get-secret-value \
  --secret-id "$SECRET_NAME" \
  --region "$REGION" \
  --query "SecretString" \
  --output text)

s() { echo "$RAW" | jq -r ".${1}"; }

cat > "$REPO_DIR/services/soko-ml/.env" <<EOF
POSTGRES_PASSWORD=$(s ML_DB_PASS)
REDIS_PASSWORD=$(s ML_REDIS_PASSWORD)
INTERNAL_API_KEY=$(s INTERNAL_API_KEY)
GOOGLE_MAPS_API_KEY=$(s GOOGLE_MAPS_API_KEY)
LOG_LEVEL=INFO
BOOTSTRAP_ON_STARTUP=true
EOF
chmod 600 "$REPO_DIR/services/soko-ml/.env"

echo "[fetch-ml-secrets] ML .env written."
