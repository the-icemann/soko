#!/bin/bash
# EC2 first-boot bootstrap — runs once as root at instance launch
set -e

export DEBIAN_FRONTEND=noninteractive
AWS_REGION="${aws_region}"

# ── System update ─────────────────────────────────────────────────────────────
apt-get update -y
apt-get upgrade -y

# ── Docker ────────────────────────────────────────────────────────────────────
apt-get install -y ca-certificates curl gnupg lsb-release jq git make
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

systemctl enable docker
systemctl start docker
usermod -aG docker ubuntu

# ── AWS CLI v2 ────────────────────────────────────────────────────────────────
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
cd /tmp && unzip -q awscliv2.zip && ./aws/install && cd /
rm -rf /tmp/aws /tmp/awscliv2.zip

# ── Clone Soko repo ───────────────────────────────────────────────────────────
mkdir -p /opt/soko
git clone https://github.com/the-icemann/soko.git /opt/soko
chown -R ubuntu:ubuntu /opt/soko

# ── Fetch secrets and write .env files ───────────────────────────────────────
chmod +x /opt/soko/scripts/fetch-secrets.sh
sudo -u ubuntu bash /opt/soko/scripts/fetch-secrets.sh

# ── Create the external Docker bridge network for ML stack ───────────────────
docker network create soko-ml-bridge 2>/dev/null || true

# ── Start core platform ───────────────────────────────────────────────────────
cd /opt/soko
sudo -u ubuntu docker compose up -d --build

# ── Start ML stack ────────────────────────────────────────────────────────────
cd /opt/soko/services/soko-ml
chmod +x /opt/soko/scripts/fetch-ml-secrets.sh
sudo -u ubuntu bash /opt/soko/scripts/fetch-ml-secrets.sh

# Bootstrap ML data (generate synthetic data + train Prophet models)
# This runs in background — takes 5-10 min; service starts with fallback until done
sudo -u ubuntu bash -c "
  cd /opt/soko/services/soko-ml
  docker compose up -d --build
  sleep 30
  docker compose exec -T data-ingestion-service python -m src.main &
" &

echo "Soko bootstrap complete. Check 'docker compose logs' for status."
