from typing import Optional

import pandas as pd
import structlog

from .interaction_store import InteractionStore

log = structlog.get_logger()


def _parse_list_field(value) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value)) if not pd.isna(value) else default
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value) if not pd.isna(value) else default
    except (TypeError, ValueError):
        return default


class ProfileStore:
    """
    Holds farmer and buyer DataFrames loaded from the ML feature store (Postgres).
    Call reload() to refresh from the database without restarting the service.
    """

    def __init__(self) -> None:
        self.farmers: pd.DataFrame = pd.DataFrame()
        self.buyers:  pd.DataFrame = pd.DataFrame()

    async def reload(self) -> tuple[int, int]:
        """Load or refresh profiles from soko_ml_db. Never raises."""
        from .feature_store_client import load_farmers, load_buyers
        try:
            self.farmers = await load_farmers()
        except Exception as exc:
            log.error(f"Failed to load farmers from feature store: {exc}")
            # Keep existing data rather than going empty

        try:
            self.buyers = await load_buyers()
        except Exception as exc:
            log.error(f"Failed to load buyers from feature store: {exc}")

        log.info("profiles_reloaded", farmers=len(self.farmers), buyers=len(self.buyers))
        return len(self.farmers), len(self.buyers)

    def get_farmer(self, farmer_id: str) -> Optional[pd.Series]:
        if self.farmers.empty:
            return None
        rows = self.farmers[self.farmers["id"] == farmer_id]
        return rows.iloc[0] if not rows.empty else None

    def get_buyer(self, buyer_id: str) -> Optional[pd.Series]:
        if self.buyers.empty:
            return None
        rows = self.buyers[self.buyers["id"] == buyer_id]
        return rows.iloc[0] if not rows.empty else None


class Recommender:
    """
    Content-based scoring with real-time interaction signal enrichment.

    Farmer scoring weights (recommending farmers to a buyer):
      crop_overlap    0.35  — specialties ∩ interests / |interests|
      district_match  0.20  — exact district match (binary)
      avg_rating      0.20  — average_rating / 5.0
      fulfillment     0.15  — fulfillment_rate (ML-derived; falls back to 0.8)
      interaction     additive boost up to +0.20

    Buyer scoring weights (recommending buyers to a farmer):
      crop_overlap    0.35  — interests ∩ specialties / |specialties|
      district_match  0.20  — exact district match (binary)
      payment         0.25  — payment_reliability (ML-derived; falls back to 0.75)
      spend_volume    0.20  — total_spent / dataset max
    """

    def __init__(self, profile_store: ProfileStore, interaction_store: InteractionStore):
        self.profiles = profile_store
        self.interactions = interaction_store

    # ── Farmers for Buyer ─────────────────────────────────────────────────────

    def _score_farmer_for_buyer(
        self, farmer: pd.Series, buyer: pd.Series, buyer_id: str
    ) -> float:
        buyer_crops = set(buyer["interests"])
        buyer_district = str(buyer.get("district", ""))
        farmer_crops = set(farmer["specialties"])
        farmer_district = str(farmer.get("district", ""))

        crop_overlap = len(buyer_crops & farmer_crops) / max(len(buyer_crops), 1)
        district_match = 1.0 if buyer_district and farmer_district == buyer_district else 0.0
        avg_rating_norm = _safe_float(farmer.get("average_rating", 3.0)) / 5.0
        fulfillment = _safe_float(farmer.get("fulfillment_rate", 0.8))

        base = (
            0.35 * crop_overlap
            + 0.20 * district_match
            + 0.20 * avg_rating_norm
            + 0.15 * fulfillment
        )
        boost = self.interactions.get_boost(buyer_id, str(farmer["id"]))
        return min(base + boost, 1.0)

    def recommend_farmers_for_buyer(self, buyer_id: str, top_n: int = 5) -> list[dict]:
        buyer = self.profiles.get_buyer(buyer_id)
        if buyer is None:
            log.warning("buyer_not_found", buyer_id=buyer_id)
            return []
        if self.profiles.farmers.empty:
            return []

        scored = [
            (self._score_farmer_for_buyer(farmer, buyer, buyer_id), farmer)
            for _, farmer in self.profiles.farmers.iterrows()
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "id":            str(f["id"]),
                "name":          str(f.get("full_name", f["id"])),
                "district":      str(f.get("district", "")),
                "specialties":   list(f["specialties"]),
                "averageRating": round(_safe_float(f.get("average_rating", 3.0)), 2),
                "totalSales":    _safe_int(f.get("total_sales", 0)),
                "totalListings": _safe_int(f.get("total_listings", 0)),
                "matchScore":    round(score, 4),
            }
            for score, f in scored[:top_n]
        ]

    # ── Buyers for Farmer ─────────────────────────────────────────────────────

    def _max_spend(self) -> float:
        if self.profiles.buyers.empty or "total_spent" not in self.profiles.buyers:
            return 1.0
        return float(self.profiles.buyers["total_spent"].max()) or 1.0

    def _score_buyer_for_farmer(
        self, buyer: pd.Series, farmer: pd.Series, max_spend: float
    ) -> float:
        farmer_crops = set(farmer["specialties"])
        farmer_district = str(farmer.get("district", ""))
        buyer_crops = set(buyer["interests"])
        buyer_district = str(buyer.get("district", ""))

        crop_overlap = len(farmer_crops & buyer_crops) / max(len(farmer_crops), 1)
        district_match = 1.0 if buyer_district and farmer_district == buyer_district else 0.0
        payment_reliability = _safe_float(buyer.get("payment_reliability", 0.75))
        spend_score = _safe_float(buyer.get("total_spent", 100_000)) / max_spend

        return (
            0.35 * crop_overlap
            + 0.20 * district_match
            + 0.25 * payment_reliability
            + 0.20 * spend_score
        )

    def recommend_buyers_for_farmer(self, farmer_id: str, top_n: int = 5) -> list[dict]:
        farmer = self.profiles.get_farmer(farmer_id)
        if farmer is None:
            log.warning("farmer_not_found", farmer_id=farmer_id)
            return []
        if self.profiles.buyers.empty:
            return []

        max_spend = self._max_spend()
        scored = [
            (self._score_buyer_for_farmer(buyer, farmer, max_spend), buyer)
            for _, buyer in self.profiles.buyers.iterrows()
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "id":          str(b["id"]),
                "name":        str(b.get("full_name", b["id"])),
                "district":    str(b.get("district", "")),
                "interests":   list(b["interests"]),
                "totalOrders": _safe_int(b.get("total_orders", 0)),
                "matchScore":  round(score, 4),
            }
            for score, b in scored[:top_n]
        ]
