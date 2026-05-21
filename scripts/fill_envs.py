#!/usr/bin/env python3
"""
Write consistent dev credentials to all core service .env files.

Idempotent: only fills keys that are currently missing or empty, unless
--force is passed (which overwrites everything).

Run before 'make seed' or after a fresh 'make setup'.
"""
import sys
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"

# ── Shared dev values ─────────────────────────────────────────────────────────
INTERNAL_SECRET = "internal-secret"        # matches ML INTERNAL_API_KEY
SECRET_KEY      = "soko-dev-secret-key-2026-change-before-production"
FRONTEND_URL    = "http://localhost:3000"

# ── Per-service desired env values ────────────────────────────────────────────
ENVS: dict[str, dict[str, str]] = {
    "auth": {
        "DATABASE_URL":        "postgresql://auth_user:auth_pass@auth_db:5432/auth_db",
        "SECRET_KEY":          SECRET_KEY,
        "INTERNAL_SECRET":     INTERNAL_SECRET,
        "FRONTEND_URL":        FRONTEND_URL,
        "USER_SERVICE_URL":    "http://user_service:8002",
        "GOOGLE_CLIENT_ID":    "",
        "GOOGLE_CLIENT_SECRET": "",
        "GOOGLE_REDIRECT_URI": "http://localhost/auth/google/callback",
    },
    "user": {
        "DATABASE_URL":     "postgresql://user_user:user_pass@user_db:5432/user_db",
        "INTERNAL_SECRET":  INTERNAL_SECRET,
        "AUTH_SERVICE_URL": "http://auth_service:8001",
    },
    "produce": {
        "DATABASE_URL":             "postgresql://produce_user:produce_pass@produce_db:5432/produce_db",
        "INTERNAL_SECRET":          INTERNAL_SECRET,
        "USER_SERVICE_URL":         "http://user_service:8002",
        "REDIS_URL":                "redis://redis:6379/0",
        "NOTIFICATION_SERVICE_URL": "http://notification_service:8007",
        "CLOUDINARY_CLOUD_NAME":    "",
        "CLOUDINARY_API_KEY":       "",
        "CLOUDINARY_API_SECRET":    "",
    },
    "order": {
        "DATABASE_URL":             "postgresql://order_user:order_pass@order_db:5432/order_db",
        "INTERNAL_SECRET":          INTERNAL_SECRET,
        "PRODUCE_SERVICE_URL":      "http://produce_service:8003",
        "USER_SERVICE_URL":         "http://user_service:8002",
        "PAYMENT_SERVICE_URL":      "http://payment_service:8005",
        "NOTIFICATION_SERVICE_URL": "http://notification_service:8007",
    },
    "payment": {
        "DATABASE_URL":             "postgresql://payment_user:payment_pass@payment_db:5432/payment_db",
        "INTERNAL_SECRET":          INTERNAL_SECRET,
        "PESAPAL_CONSUMER_KEY":     "",
        "PESAPAL_CONSUMER_SECRET":  "",
        "PESAPAL_ENV":              "sandbox",
        "PESAPAL_IPN_URL":          "http://localhost/webhook/pesapal/ipn",
        "PESAPAL_CALLBACK_URL":     "http://localhost/webhook/pesapal/callback",
        "ORDER_SERVICE_URL":        "http://order_service:8004",
        "USER_SERVICE_URL":         "http://user_service:8002",
        "NOTIFICATION_SERVICE_URL": "http://notification_service:8007",
        "FRONTEND_URL":             FRONTEND_URL,
    },
    "message": {
        "DATABASE_URL":             "postgresql://message_user:message_pass@message_db:5432/message_db",
        "INTERNAL_SECRET":          INTERNAL_SECRET,
        "SECRET_KEY":               SECRET_KEY,
        "USER_SERVICE_URL":         "http://user_service:8002",
        "PRODUCE_SERVICE_URL":      "http://produce_service:8003",
        "NOTIFICATION_SERVICE_URL": "http://notification_service:8007",
    },
    "notification": {
        "DATABASE_URL":    "postgresql://notification_user:notification_pass@notification_db:5432/notification_db",
        "INTERNAL_SECRET": INTERNAL_SECRET,
        "SECRET_KEY":      SECRET_KEY,
        "ALGORITHM":       "HS256",
        "AT_USERNAME":     "",
        "AT_API_KEY":      "",
        "AT_SENDER_ID":    "",
        "USER_SERVICE_URL": "http://user_service:8002",
    },
    "blog": {
        "DATABASE_URL":          "postgresql://blog_user:blog_pass@blog_db:5432/blog_db",
        "INTERNAL_SECRET":       INTERNAL_SECRET,
        "REDIS_URL":             "redis://redis:6379/1",
        "USER_SERVICE_URL":      "http://user_service:8002",
        # Placeholder values so the service starts; image uploads will fail gracefully
        "CLOUDINARY_CLOUD_NAME": "soko_dev",
        "CLOUDINARY_API_KEY":    "dev_key",
        "CLOUDINARY_API_SECRET": "dev_secret",
    },
    "ussd": {
        "DATABASE_URL":             "postgresql://ussd_user:ussd_pass@ussd_db:5432/ussd_db",
        "INTERNAL_SECRET":          INTERNAL_SECRET,
        "AT_USERNAME":              "",
        "AT_API_KEY":               "",
        "PRODUCE_SERVICE_URL":      "http://produce_service:8003",
        "ORDER_SERVICE_URL":        "http://order_service:8004",
        "AUTH_SERVICE_URL":         "http://auth_service:8001",
        "USER_SERVICE_URL":         "http://user_service:8002",
        "NOTIFICATION_SERVICE_URL": "http://notification_service:8007",
    },
}


def _parse_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _write_env(path: Path, desired: dict[str, str], force: bool) -> None:
    existing = _parse_env(path)
    merged   = dict(existing)
    filled: list[str] = []

    for key, val in desired.items():
        current = existing.get(key, None)
        if force or current is None or current == "":
            if current != val:
                merged[key] = val
                filled.append(key)

    path.write_text("\n".join(f"{k}={v}" for k, v in merged.items()) + "\n")

    rel = path.relative_to(ROOT)
    if filled:
        print(f"  updated  {rel}  ({len(filled)} key(s): {', '.join(filled)})")
    else:
        print(f"  ok       {rel}  (already complete)")


def main() -> None:
    force = "--force" in sys.argv
    if force:
        print("--force: overwriting all .env values")

    print("\nFilling service .env files with dev credentials...\n")

    for service, env_vars in ENVS.items():
        env_path = SERVICES / service / ".env"
        if not env_path.parent.exists():
            print(f"  skip     {service}/.env (directory not found)")
            continue
        # Create .env from .env.example if it doesn't exist yet
        if not env_path.exists():
            example = env_path.with_suffix(".env.example")
            if example.exists():
                import shutil
                shutil.copy(example, env_path)
        _write_env(env_path, env_vars, force=force)

    # ML stack .env — copy example if missing, leave values as-is
    ml_env = SERVICES / "soko-ml" / ".env"
    if not ml_env.exists():
        ml_ex = ml_env.with_suffix(".env.example")
        if ml_ex.exists():
            import shutil
            shutil.copy(ml_ex, ml_env)
            print(f"  created  services/soko-ml/.env from .env.example")
        else:
            print(f"  skip     services/soko-ml/.env (no .env.example found)")
    else:
        # Ensure INTERNAL_API_KEY matches core INTERNAL_SECRET
        _write_env(ml_env, {"INTERNAL_API_KEY": INTERNAL_SECRET}, force=force)

    print("\nDone. If services are already running, restart them to pick up new values:")
    print("  make core-down && make core-up")


if __name__ == "__main__":
    main()
