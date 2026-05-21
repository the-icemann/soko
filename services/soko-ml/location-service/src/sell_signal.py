"""
GO/WAIT sell signal derivation from a 4-week price forecast.
"""
from datetime import datetime
from typing import Optional

PERISHABLE_CROPS  = {"tomatoes", "matoke"}
HARVEST_MONTHS    = {6, 7, 11, 12}
LEAN_SEASON_MONTHS = {1, 2}
WAIT_THRESHOLD    = 0.10   # week4 > week1 by 10% → WAIT
SELL_THRESHOLD    = 0.05   # week4 < week1 by 5%  → SELL_NOW


def derive_signal(
    crop: str,
    predictions: list[dict],
    now: Optional[datetime] = None,
) -> dict:
    """
    Applies rules in order — first match wins.

    predictions: list of weekly prediction dicts, each with "predicted_price_ugx".
    Returns {"signal": str, "reason": str, "confidence": str}.
    """
    if now is None:
        now = datetime.utcnow()

    month = now.month

    if not predictions:
        return {"signal": "SELL_NOW", "reason": "No forecast data available.", "confidence": "low"}

    week1_price = float(predictions[0]["predicted_price_ugx"])
    week4_price = float(predictions[-1]["predicted_price_ugx"]) if len(predictions) >= 4 else week1_price

    slope = (week4_price - week1_price) / max(week1_price, 1)

    # Rule 1 — perishable crops, sell immediately regardless of price
    if crop in PERISHABLE_CROPS:
        return {
            "signal":     "SELL_NOW_PERISHABLE",
            "reason":     f"{crop.replace('_', ' ').title()} is perishable. Sell immediately to prevent losses.",
            "confidence": "high",
        }

    # Rule 2 — harvest season, prices will recover
    if month in HARVEST_MONTHS:
        return {
            "signal":     "WAIT",
            "reason":     "Harvest season is suppressing prices. Hold if you have storage — prices recover in 4–6 weeks.",
            "confidence": "high",
        }

    # Rule 3 — lean dry season peak
    if month in LEAN_SEASON_MONTHS:
        return {
            "signal":     "SELL_NOW",
            "reason":     "Prices are at lean season peak. This is typically the best time to sell.",
            "confidence": "high",
        }

    # Rule 4 — strong upward trend
    if slope > WAIT_THRESHOLD:
        pct = round(slope * 100, 1)
        return {
            "signal":     "WAIT",
            "reason":     f"Prices are forecast to rise {pct}% over the next 4 weeks. Consider waiting.",
            "confidence": "medium",
        }

    # Rule 5 — downward trend
    if slope < -SELL_THRESHOLD:
        pct = round(abs(slope) * 100, 1)
        return {
            "signal":     "SELL_NOW",
            "reason":     f"Prices are forecast to fall {pct}% over the next 4 weeks. Sell now.",
            "confidence": "medium",
        }

    # Default — uncertainty favours action for smallholders
    return {
        "signal":     "SELL_NOW",
        "reason":     "Prices are stable. Selling now avoids storage risk and provides immediate cash flow.",
        "confidence": "medium",
    }
