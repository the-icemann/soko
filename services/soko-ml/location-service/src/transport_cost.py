"""
Uganda agricultural transport cost estimator — distance-band model.

Rates are calibrated to what a smallholder farmer (100–500 kg load) actually pays
for partial-load or shared transport in Uganda.

Short-haul figures mirror FarasUG cargo and SafeBoda heavy-goods schedules
(base fee ~5,000 UGX + ~400 UGX/km for 100 kg, giving ~290 UGX/kg over 20 km).
Medium and long-haul figures reflect shared-lorry rates for partial loads on Uganda's
major agricultural corridors (e.g. Kampala–Gulu ~850 UGX/kg for 275 km).

All rates include a ~35 UGX/kg market-handling allowance
(porterage + local authority market levy).
"""

TRANSPORT_RATE_BANDS: list[dict] = [
    # max_km is the upper bound of the band (inclusive)
    {"max_km": 25,   "ugx_per_kg": 290,  "mode": "boda_cargo",   "label": "Motorcycle / local delivery van"},
    {"max_km": 80,   "ugx_per_kg": 420,  "mode": "taxi_van",     "label": "Shared minibus taxi or cargo van"},
    {"max_km": 200,  "ugx_per_kg": 620,  "mode": "pickup_truck", "label": "Hired pickup truck or mini-lorry"},
    {"max_km": 400,  "ugx_per_kg": 850,  "mode": "shared_lorry", "label": "Shared long-haul lorry (partial load)"},
    {"max_km": 9999, "ugx_per_kg": 1100, "mode": "cross_region", "label": "Cross-region long-haul lorry (400 km+)"},
]

TRANSPORT_DISCLAIMER = (
    "Transport cost is an estimate based on Uganda road freight rates for partial loads "
    "(FarasUG / SafeBoda benchmarks). Actual cost depends on load size, road conditions, "
    "and your arrangement with a transporter. Soko does not provide or arrange transport."
)


def estimate(distance_km: float) -> dict:
    """
    Returns transport cost info for the given road distance.
    Keys: ugx_per_kg, mode, label, disclaimer.
    """
    for band in TRANSPORT_RATE_BANDS:
        if distance_km <= band["max_km"]:
            return {
                "ugx_per_kg": float(band["ugx_per_kg"]),
                "mode":       band["mode"],
                "label":      band["label"],
                "disclaimer": TRANSPORT_DISCLAIMER,
            }
    last = TRANSPORT_RATE_BANDS[-1]
    return {
        "ugx_per_kg": float(last["ugx_per_kg"]),
        "mode":       last["mode"],
        "label":      last["label"],
        "disclaimer": TRANSPORT_DISCLAIMER,
    }
