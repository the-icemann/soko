# Soko — Production Deployment Guide (Option A)

Single EC2 in af-south-1 (Cape Town) + S3 + CloudFront.
Estimated monthly cost: ~$90–130 USD.

---

## Overview — what gets created

```
                    Users (Uganda)
                         |
              CloudFront (South Africa edge)
              /                          \
    React PWA (S3)             NGINX :80/:443 (EC2)
                                         |
                          ┌──────────────┴──────────────┐
                          │   EC2 t3.xlarge (af-south-1) │
                          │                              │
                          │  Docker Compose (core):      │
                          │    auth, user, produce,      │
                          │    order, payment, message,  │
                          │    notification, blog, ussd  │
                          │    + 9x Postgres + Redis     │
                          │                              │
                          │  Docker Compose (ML):        │
                          │    ml-gateway, price, rec,   │
                          │    location, ingest, kafka   │
                          │    + Postgres + Redis        │
                          └──────────────────────────────┘
                                         |
                               AWS Secrets Manager
                               AWS S3 (produce images)
```

---

## Prerequisites

Before starting, you need:
- [ ] AWS account with billing set up
- [ ] AWS CLI installed locally: `brew install awscli` or https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
- [ ] Terraform installed: `brew install terraform` or https://developer.hashicorp.com/terraform/install
- [ ] A domain name (recommended; can skip for initial testing)
- [ ] SSH key pair — you'll create one in Step 2

Configure AWS CLI with your credentials:
```bash
aws configure
# AWS Access Key ID: <your key>
# AWS Secret Access Key: <your secret>
# Default region: af-south-1
# Default output format: json
```

---

## Step 1 — Create an EC2 Key Pair

This is the SSH key that lets you (and GitHub Actions) connect to the server.

```bash
# Create key pair and save the .pem file
aws ec2 create-key-pair \
  --key-name soko-prod-key \
  --region af-south-1 \
  --query "KeyMaterial" \
  --output text > ~/.ssh/soko-prod-key.pem

chmod 400 ~/.ssh/soko-prod-key.pem
```

---

## Step 2 — Bootstrap Terraform State Storage

Terraform needs an S3 bucket to store its state file. Run this ONCE:

```bash
chmod +x scripts/bootstrap-tf-state.sh
./scripts/bootstrap-tf-state.sh
```

This creates:
- S3 bucket `soko-terraform-state` (versioned, encrypted)
- DynamoDB table `soko-terraform-locks` (prevents concurrent applies)

---

## Step 3 — Configure Terraform Variables

```bash
cd infrastructure
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars`:
```hcl
aws_region       = "af-south-1"
environment      = "prod"
instance_type    = "t3.xlarge"
ec2_key_name     = "soko-prod-key"
allowed_ssh_cidr = "YOUR_HOME_IP/32"   # get it: curl ifconfig.me
domain_name      = "yourdomain.com"    # or "" if you don't have one yet
alert_email      = "andrewssuubi@gmail.com"
github_repo      = "the-icemann/soko"
```

> `terraform.tfvars` is gitignored — it never gets committed.

---

## Step 4 — Provision Infrastructure

```bash
cd infrastructure
terraform init
terraform plan    # review what will be created
terraform apply   # type 'yes' when prompted
```

Takes ~3 minutes. At the end, copy the outputs:
```
ec2_public_ip           = "x.x.x.x"         ← point your domain A record here
cloudfront_domain       = "xxxx.cloudfront.net"
cloudfront_distribution_id = "EXXXXXXXXX"
frontend_bucket_name    = "soko-frontend-prod"
images_bucket_name      = "soko-produce-images-prod"
ssh_command             = "ssh -i ~/.ssh/soko-prod-key.pem ubuntu@x.x.x.x"
```

---

## Step 5 — Fill In Real Secrets

Terraform created skeleton secrets in AWS Secrets Manager with `CHANGE_ME` placeholders.
Now fill in the real values.

### Option A — AWS Console (easiest)
1. Open https://console.aws.amazon.com/secretsmanager
2. Click **soko/platform** → **Retrieve secret value** → **Edit**
3. Replace each `CHANGE_ME` with the real value
4. Click **Save**
5. Repeat for **soko/ml**

### Option B — AWS CLI
```bash
aws secretsmanager put-secret-value \
  --secret-id soko/platform \
  --region af-south-1 \
  --secret-string '{
    "AUTH_DB_PASS": "your-strong-password-here",
    "USER_DB_PASS": "your-strong-password-here",
    "PRODUCE_DB_PASS": "your-strong-password-here",
    "ORDER_DB_PASS": "your-strong-password-here",
    "PAYMENT_DB_PASS": "your-strong-password-here",
    "MESSAGE_DB_PASS": "your-strong-password-here",
    "NOTIFICATION_DB_PASS": "your-strong-password-here",
    "BLOG_DB_PASS": "your-strong-password-here",
    "USSD_DB_PASS": "your-strong-password-here",
    "SECRET_KEY": "a-64-char-random-string",
    "INTERNAL_SECRET": "another-random-string",
    "ALGORITHM": "HS256",
    "GOOGLE_CLIENT_ID": "your-google-client-id",
    "GOOGLE_CLIENT_SECRET": "your-google-client-secret",
    "PESAPAL_CONSUMER_KEY": "your-pesapal-key",
    "PESAPAL_CONSUMER_SECRET": "your-pesapal-secret",
    "PESAPAL_ENV": "production",
    "AT_USERNAME": "your-africas-talking-username",
    "AT_API_KEY": "your-at-api-key",
    "AT_SENDER_ID": "SOKO",
    "SENDGRID_API_KEY": "SG.xxxxx",
    "SENDGRID_FROM_EMAIL": "noreply@yourdomain.com",
    "DOMAIN": "yourdomain.com"
  }'

aws secretsmanager put-secret-value \
  --secret-id soko/ml \
  --region af-south-1 \
  --secret-string '{
    "ML_DB_PASS": "your-strong-password-here",
    "ML_REDIS_PASSWORD": "",
    "INTERNAL_API_KEY": "another-random-string",
    "GOOGLE_MAPS_API_KEY": "your-maps-api-key-or-empty"
  }'
```

> Generate strong passwords: `openssl rand -base64 32`
> Generate a SECRET_KEY: `openssl rand -hex 32`

---

## Step 6 — Add Secrets to GitHub

You need to add secrets to **both** GitHub repos.

### Backend repo (github.com/the-icemann/soko)

Go to: **Settings → Secrets and variables → Actions → New repository secret**

| Secret name  | Value |
|---|---|
| `EC2_HOST`   | The EC2 public IP from Terraform output |
| `EC2_SSH_KEY` | Full contents of `~/.ssh/soko-prod-key.pem` |
| `AWS_ACCESS_KEY_ID` | Your AWS access key (create a deploy-only IAM user) |
| `AWS_SECRET_ACCESS_KEY` | Your AWS secret key |

### Frontend repo (github.com/the-icemann/soko_client_final)

Same **Secrets** tab:

| Secret name | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | Same AWS key |
| `AWS_SECRET_ACCESS_KEY` | Same AWS secret |

Then go to **Settings → Secrets and variables → Variables → New repository variable**:

| Variable name | Value |
|---|---|
| `VITE_API_BASE_URL` | `https://yourdomain.com` or `http://x.x.x.x` |
| `FRONTEND_BUCKET` | `soko-frontend-prod` (from Terraform output) |
| `CF_DISTRIBUTION_ID` | The CloudFront distribution ID from Terraform output |

> **Security tip**: Create a separate IAM user for GitHub Actions with only `s3:*` on the frontend bucket and `cloudfront:CreateInvalidation`. Keep your personal admin key separate.

---

## Step 7 — First Server Setup

SSH into the EC2 instance. The user_data script ran automatically on boot and:
- Installed Docker, git, AWS CLI
- Cloned the repo to `/opt/soko`
- Fetched secrets and started all services

Check status:
```bash
ssh -i ~/.ssh/soko-prod-key.pem ubuntu@YOUR_EC2_IP

# Check core services
cd /opt/soko
docker compose ps

# Check ML stack
cd services/soko-ml
docker compose ps

# Check logs
docker compose logs --tail=50 auth_service
docker compose logs --tail=50 soko-ml-gateway
```

If the user_data script didn't run completely (can take 5-10 min on first boot):
```bash
# Run manually
cd /opt/soko
chmod +x scripts/fetch-secrets.sh scripts/fetch-ml-secrets.sh
bash scripts/fetch-secrets.sh
docker network create soko-ml-bridge 2>/dev/null || true
docker compose up -d --build

cd services/soko-ml
bash /opt/soko/scripts/fetch-ml-secrets.sh
docker compose up -d --build
```

---

## Step 8 — SSL with Let's Encrypt

Once your domain's A record points to the EC2 Elastic IP:

```bash
ssh -i ~/.ssh/soko-prod-key.pem ubuntu@YOUR_EC2_IP

# Install certbot
sudo apt-get install -y certbot python3-certbot-nginx

# Get certificate (NGINX must be running on port 80)
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com \
  --non-interactive --agree-tos -m andrewssuubi@gmail.com

# Certbot auto-renews via systemd timer — verify:
sudo systemctl status certbot.timer
```

Then update the PesaPal callback URLs in Secrets Manager to use `https://`.

---

## Step 9 — Deploy Frontend (First Time)

Push to main in the frontend repo to trigger the GitHub Actions workflow, or run manually:

```bash
cd /home/the-icemann/Desktop/soko_client_final

# Set the API URL for local build test
VITE_API_BASE_URL=https://yourdomain.com npm run build

# Deploy manually (AWS CLI must be configured)
aws s3 sync dist/assets/ s3://soko-frontend-prod/assets/ \
  --cache-control "public, max-age=31536000, immutable" --delete

aws s3 sync dist/ s3://soko-frontend-prod/ \
  --exclude "assets/*" \
  --cache-control "public, max-age=0, must-revalidate" --delete

aws cloudfront create-invalidation \
  --distribution-id YOUR_CF_DISTRIBUTION_ID \
  --paths "/*"
```

Frontend is now live at the CloudFront domain (or your custom subdomain if configured).

---

## Day-to-Day Operations

### Deploying changes
```bash
# Backend: just push to main — GitHub Actions SSHes in and redeploys
git push origin main

# Frontend: just push to main in the client repo
cd /path/to/soko_client_final && git push origin main
```

### Viewing logs
```bash
ssh -i ~/.ssh/soko-prod-key.pem ubuntu@YOUR_EC2_IP

# All core services
docker compose -f /opt/soko/docker-compose.yml logs -f

# Specific service
docker compose -f /opt/soko/docker-compose.yml logs -f auth_service

# ML stack
docker compose -f /opt/soko/services/soko-ml/docker-compose.yml logs -f
```

### Updating secrets
```bash
# Update in Secrets Manager (console or CLI), then:
ssh -i ~/.ssh/soko-prod-key.pem ubuntu@YOUR_EC2_IP
cd /opt/soko
bash scripts/fetch-secrets.sh
docker compose up -d  # restarts with new env
```

### Full restart
```bash
cd /opt/soko
docker compose down && docker compose up -d --build
```

### Database backups
```bash
# Backup all databases (run on EC2 or via ssh)
for db in auth user produce order payment message notification blog ussd; do
  docker exec ${db}_db pg_dump -U ${db}_user ${db}_db \
    | gzip > /opt/backups/${db}_$(date +%Y%m%d).sql.gz
done
# Upload to S3
aws s3 cp /opt/backups/ s3://soko-produce-images-prod/backups/ --recursive
```

---

## Secrets Flow Diagram

```
AWS Secrets Manager
  soko/platform  ←── You fill in real values (Step 5)
  soko/ml        ←── You fill in real values (Step 5)
        |
        | fetch-secrets.sh  (runs on EC2 at deploy time)
        ↓
  /opt/soko/.env            ← Docker Compose reads for ${VAR} substitution
  services/auth/.env        ← auth_service reads via env_file
  services/payment/.env     ← payment_service reads via env_file
  services/notification/.env
  services/ussd/.env
  services/message/.env
  services/soko-ml/.env     ← ML docker-compose reads
        |
        ↓
  Docker containers get secrets as environment variables
  Postgres containers get strong passwords (not the dev defaults)
  Services get real API keys at runtime

GitHub Actions Secrets
  EC2_SSH_KEY + EC2_HOST    → deploy.yml SSHes in to run git pull + docker compose
  AWS_ACCESS_KEY_ID/SECRET  → frontend deploy.yml syncs to S3 + invalidates CloudFront

Frontend (browser)
  VITE_API_BASE_URL         → baked into the JS bundle at build time (not secret)
                               Set as GitHub Actions variable (not secret)
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `docker compose up` fails with env var errors | Run `bash scripts/fetch-secrets.sh` first |
| Services can't reach ML gateway | Run `docker network create soko-ml-bridge` |
| NGINX 503 on prices page | Check ML stack: `cd services/soko-ml && docker compose ps` |
| SSL certificate not renewing | `sudo certbot renew --dry-run` to test; check `/var/log/letsencrypt` |
| EC2 out of disk | `docker system prune -a` removes unused images |
| GitHub Actions deploy fails | Check `EC2_SSH_KEY` — must be the full PEM contents including headers |
| CloudFront shows old version | Trigger invalidation: `aws cloudfront create-invalidation --distribution-id ID --paths "/*"` |
