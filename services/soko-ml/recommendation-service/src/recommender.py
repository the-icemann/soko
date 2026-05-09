import os
from pathlib import Path
from typing import Optional

import pandas as pd
import structlog

from .interaction_store import InteractionStore

log = structlog.get_logger()


def _parse_list_field(value) -> list[str]:
    """Parse a comma-separated string or pass-through a list."""
    if pd.isna(value) or value == "":
        return []
    if isinstance(value, list):
        return value
    return [v.strip() for v in str(value).split(",") if v.strip()]


class ProfileStore:
    """Loads farmer and buyer profile DataFrames from CSV at startup."""

    def __init__(self, farmers_path: str, buyers_path: str):
        self._farmers_path = farmers_path
        self._buyers_path = buyers_path
        self.farmers: pd.DataFrame = pd.DataFrame()
        self.buyers: pd.DataFrame = pd.DataFrame()

    def load(self) -> tuple[int, int]:
        try:
            self.farmers = pd.read_csv(self._farmers_path)
            self.farmers["crops_offered"] = self.farmers["crops_offered"].apply(_parse_list_field)
            self.farmers["markets_served"] = self.farmers["markets_served"].apply(_parse_list_field)
            log.info("farmers_loaded", count=len(self.farmers))
        except FileNotFoundError:
            log.warning("farmers_file_not_found", path=self._farmers_path)

        try:
            self.buyers = pd.read_csv(self._buyers_path)
            self.buyers["preferred_crops"] = self.buyers["preferred_crops"].apply(_parse_list_field)
            self.buyers["preferred_markets"] = self.buyers["preferred_markets"].apply(_parse_list_field)
            log.info("buyers_loaded", count=len(self.buyers))
        except FileNotFoundError:
            log.warning("buyers_file_not_found", path=self._buyers_path)

        return len(self.farmers), len(self.buyers)

    def get_farmer(self, farmer_id: str) -> Optional[pd.Series]:
        if self.farmers.empty:
            return None
        rows = self.farmers[self.farmers["farmer_id"] == farmer_id]
        return rows.iloc[0] if not rows.empty else None

    def get_buyer(self, buyer_id: str) -> Optional[pd.Series]:
        if self.buyers.empty:
            return None
        rows = self.buyers[self.buyers["buyer_id"] == buyer_id]
        return rows.iloc[0] if not rows.empty else None


class Recommender:
    """
    Content-based scoring with real-time interaction signal enrichment.

    Farmer scoring weights (recommending farmers to a buyer):
      crop_overlap   0.35
      market_overlap 0.20
      avg_rating     0.20  (normalised over 5.0)
      fulfillment    0.15
      interaction    additive boost up to +0.20

    Buyer scoring weights (recommending buyers to a farmer):
      crop_overlap          0.35
      market_overlap        0.20
      payment_reliability   0.25
      purchase_volume_score 0.20  (normalised by dataset max)
    """

    def __init__(self, profile_store: ProfileStore, interaction_store: InteractionStore):
        self.profiles = profile_store
        self.interactions = interaction_store

    # ── Farmers for Buyer ─────────────────────────────────────────────────────

    def _score_farmer_for_buyer(
        self, farmer: pd.Series, buyer: pd.Series, buyer_id: str
    ) -> float:
        buyer_crops = set(buyer["preferred_crops"])
        buyer_markets = set(buyer["preferred_markets"])
        farmer_crops = set(farmer["crops_offered"])
        farmer_markets = set(farmer["markets_served"])

        crop_overlap = len(buyer_crops & farmer_crops) / max(len(buyer_crops), 1)
        market_overlap = len(buyer_markets & farmer_markets) / max(len(buyer_markets), 1)
        avg_rating_norm = float(farmer.get("avg_rating", 3.0)) / 5.0
        fulfillment = float(farmer.get("fulfillment_rate", 0.8))

        base = (
            0.35 * crop_overlap
            + 0.20 * market_overlap
            + 0.20 * avg_rating_norm
            + 0.15 * fulfillment
        )
        boost = self.interactions.get_boost(buyer_id, str(farmer["farmer_id"]))
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
                "farmer_id": str(f["farmer_id"]),
                "name": str(f.get("name", f["farmer_id"])),
                "crops_offered": list(f["crops_offered"]),
                "avg_rating": round(float(f.get("avg_rating", 3.0)), 2),
                "fulfillment_rate": round(float(f.get("fulfillment_rate", 0.8)), 3),
                "match_score": round(score, 4),
            }
            for score, f in scored[:top_n]
        ]

    # ── Buyers for Farmer ─────────────────────────────────────────────────────

    def _max_volume(self) -> float:
        if self.profiles.buyers.empty or "avg_order_volume_kg" not in self.profiles.buyers:
            return 1.0
        return float(self.profiles.buyers["avg_order_volume_kg"].max()) or 1.0

    def _score_buyer_for_farmer(
        self, buyer: pd.Series, farmer: pd.Series, max_vol: float
    ) -> float:
        farmer_crops = set(farmer["crops_offered"])
        farmer_markets = set(farmer["markets_served"])
        buyer_crops = set(buyer["preferred_crops"])
        buyer_markets = set(buyer["preferred_markets"])

        crop_overlap = len(farmer_crops & buyer_crops) / max(len(farmer_crops), 1)
        market_overlap = len(farmer_markets & buyer_markets) / max(len(farmer_markets), 1)
        payment_reliability = float(buyer.get("payment_reliability", 0.75))
        volume_score = float(buyer.get("avg_order_volume_kg", 100)) / max_vol

        return (
            0.35 * crop_overlap
            + 0.20 * market_overlap
            + 0.25 * payment_reliability
            + 0.20 * volume_score
        )

    def recommend_buyers_for_farmer(self, farmer_id: str, top_n: int = 5) -> list[dict]:
        farmer = self.profiles.get_farmer(farmer_id)
        if farmer is None:
            log.warning("farmer_not_found", farmer_id=farmer_id)
            return []
        if self.profiles.buyers.empty:
            return []

        max_vol = self._max_volume()
        scored = [
            (self._score_buyer_for_farmer(buyer, farmer, max_vol), buyer)
            for _, buyer in self.profiles.buyers.iterrows()
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "buyer_id": str(b["buyer_id"]),
                "name": str(b.get("name", b["buyer_id"])),
                "preferred_crops": list(b["preferred_crops"]),
                "payment_reliability": round(float(b.get("payment_reliability", 0.75)), 3),
                "avg_order_volume_kg": round(float(b.get("avg_order_volume_kg", 100)), 1),
                "match_score": round(score, 4),
            }
            for score, b in scored[:top_n]
        ]
