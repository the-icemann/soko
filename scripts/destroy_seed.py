#!/usr/bin/env python3
"""
Destroy all data written by seed.py.

Reads scripts/.seed_manifest.json for the exact IDs that were seeded,
then runs targeted SQL deletes against each service database via docker exec.

Safe to run even if services are not all reachable — each step is independent.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT     = Path(__file__).resolve().parent.parent
MANIFEST = Path(__file__).parent / ".seed_manifest.json"


# ── SQL helper ────────────────────────────────────────────────────────────────

def psql(container: str, user: str, db: str, sql: str, compose_file: str = "docker-compose.yml") -> bool:
    """Run SQL in a postgres container. Returns True on success."""
    cmd = [
        "docker", "compose", "-f", compose_file,
        "exec", "-T", container,
        "psql", "-U", user, "-d", db, "-c", sql,
    ]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Silence "table not found" and "no rows" — those are fine
        if "does not exist" not in stderr and stderr:
            print(f"    WARN ({container}): {stderr[:120]}")
        return False
    return True


def ids_literal(ids: list[str]) -> str:
    """Build a SQL literal list: 'id1'::uuid, 'id2'::uuid, ..."""
    return ", ".join(f"'{i}'::uuid" for i in ids)


# ── Step functions ────────────────────────────────────────────────────────────

def destroy_blog_posts(farmer_ids: list[str]) -> None:
    if not farmer_ids:
        return
    print("  blog posts + sections + likes ...")
    lit = ids_literal(farmer_ids)
    # post_sections and post_likes cascade via FK if set; otherwise delete explicitly
    psql("blog_db", "blog_user", "blog_db",
         f"DELETE FROM post_likes    WHERE post_id IN (SELECT id FROM posts WHERE author_id IN ({lit}));")
    psql("blog_db", "blog_user", "blog_db",
         f"DELETE FROM comments      WHERE post_id IN (SELECT id FROM posts WHERE author_id IN ({lit}));")
    psql("blog_db", "blog_user", "blog_db",
         f"DELETE FROM post_sections WHERE post_id IN (SELECT id FROM posts WHERE author_id IN ({lit}));")
    psql("blog_db", "blog_user", "blog_db",
         f"DELETE FROM posts WHERE author_id IN ({lit});")


def destroy_product_reviews(buyer_ids: list[str]) -> None:
    if not buyer_ids:
        return
    print("  product reviews + helpful votes ...")
    lit = ids_literal(buyer_ids)
    psql("produce_db", "produce_user", "produce_db",
         f"DELETE FROM product_review_helpful WHERE review_id IN "
         f"(SELECT id FROM product_reviews WHERE reviewer_id IN ({lit}));")
    psql("produce_db", "produce_user", "produce_db",
         f"DELETE FROM product_reviews WHERE reviewer_id IN ({lit});")


def destroy_listings(farmer_ids: list[str]) -> None:
    if not farmer_ids:
        return
    print("  produce listings + price tiers + images ...")
    lit = ids_literal(farmer_ids)
    listing_subq = f"SELECT id FROM listings WHERE farmer_id IN ({lit})"
    psql("produce_db", "produce_user", "produce_db",
         f"DELETE FROM product_review_helpful WHERE review_id IN "
         f"(SELECT id FROM product_reviews WHERE listing_id IN ({listing_subq}));")
    psql("produce_db", "produce_user", "produce_db",
         f"DELETE FROM product_reviews WHERE listing_id IN ({listing_subq});")
    psql("produce_db", "produce_user", "produce_db",
         f"DELETE FROM price_tiers WHERE listing_id IN ({listing_subq});")
    psql("produce_db", "produce_user", "produce_db",
         f"DELETE FROM listing_images WHERE listing_id IN ({listing_subq});")
    psql("produce_db", "produce_user", "produce_db",
         f"DELETE FROM listings WHERE farmer_id IN ({lit});")


def destroy_messages(all_user_ids: list[str]) -> None:
    if not all_user_ids:
        return
    print("  conversations + messages ...")
    lit = ids_literal(all_user_ids)
    conv_subq = f"SELECT id FROM conversations WHERE buyer_id IN ({lit}) OR farmer_id IN ({lit})"
    psql("message_db", "message_user", "message_db",
         f"DELETE FROM messages WHERE conversation_id IN ({conv_subq});")
    psql("message_db", "message_user", "message_db",
         f"DELETE FROM conversations WHERE buyer_id IN ({lit}) OR farmer_id IN ({lit});")


def destroy_payments(order_ids: list[str]) -> None:
    if not order_ids:
        return
    print("  payment transactions ...")
    lit = ids_literal(order_ids)
    psql("payment_db", "payment_user", "payment_db",
         f"DELETE FROM transactions WHERE order_id IN ({lit});")


def destroy_orders(buyer_ids: list[str]) -> None:
    if not buyer_ids:
        return
    print("  orders + order items ...")
    lit = ids_literal(buyer_ids)
    order_subq = f"SELECT id FROM orders WHERE buyer_id IN ({lit})"
    psql("order_db", "order_user", "order_db",
         f"DELETE FROM order_items WHERE order_id IN ({order_subq});")
    psql("order_db", "order_user", "order_db",
         f"DELETE FROM orders WHERE buyer_id IN ({lit});")


def destroy_user_profiles(all_user_ids: list[str]) -> None:
    if not all_user_ids:
        return
    print("  user profiles + stats + follows ...")
    lit = ids_literal(all_user_ids)
    psql("user_db", "user_user", "user_db",
         f"DELETE FROM farmer_follows WHERE follower_id IN ({lit}) OR farmer_id IN ({lit});")
    psql("user_db", "user_user", "user_db",
         f"DELETE FROM review_helpful  WHERE voter_id    IN ({lit});")
    psql("user_db", "user_user", "user_db",
         f"DELETE FROM farmer_reviews  WHERE farmer_id   IN ({lit}) OR reviewer_id IN ({lit});")
    psql("user_db", "user_user", "user_db",
         f"DELETE FROM user_settings   WHERE user_id     IN ({lit});")
    psql("user_db", "user_user", "user_db",
         f"DELETE FROM buyer_stats     WHERE user_id     IN ({lit});")
    psql("user_db", "user_user", "user_db",
         f"DELETE FROM farmer_stats    WHERE user_id     IN ({lit});")
    psql("user_db", "user_user", "user_db",
         f"DELETE FROM user_profiles   WHERE id          IN ({lit});")


def destroy_auth_credentials() -> None:
    print("  auth credentials (@sokodev.ug accounts) ...")
    psql("auth_db", "auth_user", "auth_db",
         "DELETE FROM auth_credentials WHERE email LIKE '%@sokodev.ug';")


def reset_ml_feature_store() -> None:
    print("  ML feature store (user_profiles, price_observations, interactions, coverage_gaps) ...")
    ml_compose = str(ROOT / "services" / "soko-ml" / "docker-compose.yml")
    result = subprocess.run(
        [
            "docker", "compose", "-f", ml_compose,
            "--project-directory", str(ROOT / "services" / "soko-ml"),
            "exec", "-T", "soko-ml-db",
            "psql", "-U", "soko_ml", "-d", "soko_ml_db",
            "-c", "TRUNCATE user_profiles, price_observations, interactions, coverage_gaps RESTART IDENTITY CASCADE;",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "does not exist" not in result.stderr:
        print(f"    WARN (soko-ml-db): {result.stderr.strip()[:120]}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not MANIFEST.exists():
        print("No seed manifest found (scripts/.seed_manifest.json).")
        print("If you seeded manually, delete data by hand or truncate the databases.")
        sys.exit(0)

    manifest   = json.loads(MANIFEST.read_text())
    farmer_ids = [f["id"] for f in manifest.get("farmers", [])]
    buyer_ids  = [b["id"] for b in manifest.get("buyers",  [])]
    order_ids  = manifest.get("order_ids",  [])
    all_ids    = farmer_ids + buyer_ids

    if not all_ids:
        print("Manifest contains no user IDs. Nothing to destroy.")
        MANIFEST.unlink(missing_ok=True)
        sys.exit(0)

    print(f"\nDestroying seed data for {len(farmer_ids)} farmer(s) and {len(buyer_ids)} buyer(s)...\n")

    destroy_blog_posts(farmer_ids)
    destroy_product_reviews(buyer_ids)
    destroy_listings(farmer_ids)
    destroy_messages(all_ids)
    destroy_payments(order_ids)
    destroy_orders(buyer_ids)
    destroy_user_profiles(all_ids)
    destroy_auth_credentials()
    reset_ml_feature_store()

    MANIFEST.unlink(missing_ok=True)
    print("\n  Manifest removed.")
    print("\nAll seed data destroyed. Run 'make seed' to re-seed.")


if __name__ == "__main__":
    main()
