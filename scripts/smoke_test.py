#!/usr/bin/env python3
"""
Randomised smoke test for the Soko ML stack.

Each run picks random farmers and buyers from the live feature store,
so no two runs hit the exact same pair.
"""

import json
import subprocess
import sys

import requests

GATEWAY = "http://localhost:8080"
REC     = "http://localhost:8095"
N_PAIRS = 3   # number of random buyer→farmer and farmer→buyer pairs to test


def psql(query: str) -> list[tuple]:
    """Run a query against soko_ml_db and return rows as (col1, col2, ...) tuples."""
    result = subprocess.run(
        ["docker", "exec", "soko-ml-db",
         "psql", "-U", "soko_ml", "-d", "soko_ml_db",
         "-At", "-F", "|", "-c", query],
        capture_output=True, text=True
    )
    rows = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line:
            rows.append(tuple(p.strip() for p in line.split("|")))
    return rows


def random_buyers(n: int) -> list[tuple[str, str]]:
    rows = psql(f"SELECT buyer_id, name FROM buyer_features ORDER BY RANDOM() LIMIT {n};")
    return [(r[0], r[1]) for r in rows]


def random_farmers(n: int) -> list[tuple[str, str]]:
    rows = psql(f"SELECT farmer_id, name FROM farmer_features ORDER BY RANDOM() LIMIT {n};")
    return [(r[0], r[1]) for r in rows]


def section(title: str):
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def hit(url: str) -> dict | None:
    try:
        resp = requests.get(url, timeout=10)
        if resp.ok:
            return resp.json()
        print(f"  HTTP {resp.status_code}: {resp.text[:120]}")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def post(url: str, body: dict) -> dict | None:
    try:
        resp = requests.post(url, json=body, timeout=10)
        if resp.ok:
            return resp.json()
        print(f"  HTTP {resp.status_code}: {resp.text[:120]}")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


# ── 0. Service health ─────────────────────────────────────────────────────────

section("Service health")
health = hit(f"{REC}/health")
if health:
    print(f"  farmers_loaded : {health.get('farmers_loaded')}")
    print(f"  buyers_loaded  : {health.get('buyers_loaded')}")
else:
    print("  Recommendation service unreachable — aborting.")
    sys.exit(1)


# ── 1. Price prediction ───────────────────────────────────────────────────────

section("Price prediction — Kisenyi_Kampala / maize_grain")
result = post(f"{GATEWAY}/price/predict", {
    "market": "Kisenyi_Kampala", "crop": "maize_grain", "weeks_ahead": 4
})
if result:
    print(f"  cached     : {result.get('cached')}")
    for p in result.get("predictions", []):
        print(f"  {p['date']}  {p['predicted_price_ugx']:>6} UGX/kg"
              f"  [{p['lower_bound']}–{p['upper_bound']}]")


# ── 2. Farmers for random buyers ──────────────────────────────────────────────

buyers = random_buyers(N_PAIRS)
if not buyers:
    print("\nNo buyers in feature store — run `make seed` first.")
    sys.exit(1)

for buyer_id, name in buyers:
    section(f"Farmers for buyer — {name}")
    data = hit(f"{GATEWAY}/recommend/farmers-for-buyer/{buyer_id}?top_n=3")
    if data:
        for i, f in enumerate(data.get("recommended_farmers", []), 1):
            print(f"  #{i}  {f['name']:<25} {f['district']:<12}"
                  f"  score={f['matchScore']}  crops={f['specialties']}")
        if not data.get("recommended_farmers"):
            print("  (no recommendations returned)")


# ── 3. Buyers for random farmers ──────────────────────────────────────────────

farmers = random_farmers(N_PAIRS)
if not farmers:
    print("\nNo farmers in feature store — run `make seed` first.")
    sys.exit(1)

for farmer_id, name in farmers:
    section(f"Buyers for farmer — {name}")
    data = hit(f"{GATEWAY}/recommend/buyers-for-farmer/{farmer_id}?top_n=3")
    if data:
        for i, b in enumerate(data.get("recommended_buyers", []), 1):
            print(f"  #{i}  {b['name']:<25} {b['district']:<12}"
                  f"  score={b['matchScore']}")
        if not data.get("recommended_buyers"):
            print("  (no recommendations returned)")

print(f"\n{'═' * 60}")
print("  Smoke test complete")
print(f"{'═' * 60}\n")
