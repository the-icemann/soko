# ── Platform secrets (all core services) ─────────────────────────────────────
resource "aws_secretsmanager_secret" "platform" {
  name                    = "soko/platform"
  description             = "All Soko core platform secrets"
  recovery_window_in_days = 7

  tags = { Name = "soko-platform-secrets" }
}

# Skeleton — set real values via AWS Console or `aws secretsmanager put-secret-value`
# after `terraform apply`. Do NOT put real secrets in this file.
resource "aws_secretsmanager_secret_version" "platform" {
  secret_id = aws_secretsmanager_secret.platform.id

  secret_string = jsonencode({
    # ── Database passwords ───────────────────────────────────────────────────
    AUTH_DB_PASS         = "CHANGE_ME"
    USER_DB_PASS         = "CHANGE_ME"
    PRODUCE_DB_PASS      = "CHANGE_ME"
    ORDER_DB_PASS        = "CHANGE_ME"
    PAYMENT_DB_PASS      = "CHANGE_ME"
    MESSAGE_DB_PASS      = "CHANGE_ME"
    NOTIFICATION_DB_PASS = "CHANGE_ME"
    BLOG_DB_PASS         = "CHANGE_ME"
    USSD_DB_PASS         = "CHANGE_ME"

    # ── Auth / JWT ────────────────────────────────────────────────────────────
    SECRET_KEY        = "CHANGE_ME"
    INTERNAL_SECRET   = "CHANGE_ME"
    ALGORITHM         = "HS256"

    # ── Google OAuth ──────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID     = "CHANGE_ME"
    GOOGLE_CLIENT_SECRET = "CHANGE_ME"

    # ── PesaPal (Payments) ────────────────────────────────────────────────────
    PESAPAL_CONSUMER_KEY    = "CHANGE_ME"
    PESAPAL_CONSUMER_SECRET = "CHANGE_ME"
    PESAPAL_ENV             = "production"

    # ── Africa's Talking (USSD + SMS) ─────────────────────────────────────────
    AT_USERNAME  = "CHANGE_ME"
    AT_API_KEY   = "CHANGE_ME"
    AT_SENDER_ID = "SOKO"

    # ── SendGrid / Email ──────────────────────────────────────────────────────
    SENDGRID_API_KEY   = "CHANGE_ME"
    SENDGRID_FROM_EMAIL = "noreply@CHANGE_ME"
  })

  lifecycle {
    # Terraform manages the secret skeleton only — real values updated externally
    ignore_changes = [secret_string]
  }
}

# ── ML stack secrets ──────────────────────────────────────────────────────────
resource "aws_secretsmanager_secret" "ml" {
  name                    = "soko/ml"
  description             = "Soko ML layer secrets"
  recovery_window_in_days = 7

  tags = { Name = "soko-ml-secrets" }
}

resource "aws_secretsmanager_secret_version" "ml" {
  secret_id = aws_secretsmanager_secret.ml.id

  secret_string = jsonencode({
    ML_DB_PASS         = "CHANGE_ME"
    ML_REDIS_PASSWORD  = ""
    INTERNAL_API_KEY   = "CHANGE_ME"
    GOOGLE_MAPS_API_KEY = "CHANGE_ME"
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}
