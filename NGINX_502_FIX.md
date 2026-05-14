# nginx 502 Bad Gateway — Root Cause & Fix

## What's happening

When `make core-up` rebuilds and restarts backend containers (e.g. `user_service`, `produce_service`), those containers can come up with different Docker-assigned IPs than they had before. nginx resolves static `upstream` block hostnames **once at startup** and caches the IPs — it does not re-resolve them when upstream containers restart.

Result: nginx sends `user_service` requests to produce_service's old IP and vice versa → `Connection refused (111)` → **502 Bad Gateway**.

Confirmed from nginx error log:
```
connect() failed (111) upstream: "http://172.20.0.12:8002/docs"   ← /users/ route
connect() failed (111) upstream: "http://172.20.0.15:8003/docs"   ← /listings/ route
```
But docker network shows `produce_service=172.20.0.12` and `user_service=172.20.0.15` — the ports are crossed because nginx cached the pre-rebuild IPs.

---

## Quick fix (immediate)

Restart nginx so it re-resolves all upstream hostnames:

```bash
docker restart api_gateway
```

Also add this to `make core-up` so it always reloads nginx after rebuilding services:

```makefile
core-up:
    $(COMPOSE_CORE) up --build -d
    @docker restart api_gateway   # re-resolve upstream IPs after container rebuilds
    ...
```

---

## Proper fix (nginx config)

Convert all static `upstream` blocks in `nginx/nginx.conf` to variable-based resolution, the same pattern already used for the ML gateway. Variable-based `proxy_pass` forces nginx to re-query the Docker DNS resolver (`127.0.0.11`) on every request.

**Current (broken on rebuild):**
```nginx
upstream user_service { server user_service:8002; }

location /users/ {
    proxy_pass http://user_service/;
}
```

**Fixed:**
```nginx
# Remove the upstream block entirely, use a variable instead:

location /users/ {
    set $user_svc "user_service:8002";
    proxy_pass http://$user_svc/;
}
```

The `resolver 127.0.0.11 valid=30s;` directive already in `nginx.conf` handles the periodic re-resolution — no other changes needed.

Apply the same pattern to every service: `auth_service`, `user_service`, `produce_service`, `order_service`, `payment_service`, `message_service`, `notification_service`, `blog_service`, `ussd_service`.

---

## Why this doesn't affect the ML gateway

The ML gateway already uses variable-based resolution:
```nginx
set $ml_gw "ml-gateway:8000";
proxy_pass http://$ml_gw/...;
```
That's why it survives ML stack restarts without nginx needing a reload.
