# ── Platform secrets (all core services) ─────────────────────────────────────
# Terraform owns the secret container only (name, tags, recovery window).
# Secret VALUES are managed exclusively via AWS Console or CLI — never here.
# Run scripts/fetch-secrets.sh on EC2 after updating values.
#
# Keys to populate:
#   AUTH_DB_PASS, USER_DB_PASS, PRODUCE_DB_PASS, ORDER_DB_PASS,
#   PAYMENT_DB_PASS, MESSAGE_DB_PASS, NOTIFICATION_DB_PASS,
#   BLOG_DB_PASS, USSD_DB_PASS,
#   SECRET_KEY, INTERNAL_SECRET, ALGORITHM,
#   FRONTEND_URL, GOOGLE_REDIRECT_URI,
#   GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
#   PESAPAL_CONSUMER_KEY, PESAPAL_CONSUMER_SECRET, PESAPAL_ENV,
#   AT_USERNAME, AT_API_KEY, AT_SENDER_ID,
#   SENDGRID_API_KEY, SENDGRID_FROM_EMAIL
resource "aws_secretsmanager_secret" "platform" {
  name                    = "soko/platform"
  description             = "All Soko core platform secrets"
  recovery_window_in_days = 7

  tags = { Name = "soko-platform-secrets" }
}

# ── ML stack secrets ──────────────────────────────────────────────────────────
# Keys: ML_DB_PASS, ML_REDIS_PASSWORD, INTERNAL_API_KEY, GOOGLE_MAPS_API_KEY
resource "aws_secretsmanager_secret" "ml" {
  name                    = "soko/ml"
  description             = "Soko ML layer secrets"
  recovery_window_in_days = 7

  tags = { Name = "soko-ml-secrets" }
}
