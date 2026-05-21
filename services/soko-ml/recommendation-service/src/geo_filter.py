"""
Haversine pre-filter for the recommendation-service.
Eliminates farmers/buyers that are too far away before the scoring step,
reducing the scoring set to a manageable size when profile counts grow large.
"""
import math
from typing import Optional

import pandas as pd

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Returns the great-circle distance in km between two GPS coordinates."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def filter_by_distance(
    df: pd.DataFrame,
    origin_lat: float,
    origin_lng: float,
    max_km: float,
    lat_col: str = "lat",
    lng_col: str = "lng",
    relax_factor: float = 1.5,
) -> pd.DataFrame:
    """
    Returns a filtered DataFrame containing only rows within max_km * relax_factor
    of the origin coordinates.

    relax_factor is applied to avoid cutting off profiles at the exact boundary
    when coordinate precision is low (district centroids can be ~30km off).

    Rows with NULL lat/lng are always included — they won't be excluded because
    their location is unknown, not because they're far away.
    """
    if df.empty:
        return df

    threshold = max_km * relax_factor

    def _within(row) -> bool:
        lat = row.get(lat_col)
        lng = row.get(lng_col)
        if lat is None or lng is None or (lat == 0 and lng == 0):
            return True  # unknown location — include by default
        try:
            return haversine_km(origin_lat, origin_lng, float(lat), float(lng)) <= threshold
        except Exception:
            return True

    mask = df.apply(_within, axis=1)
    return df[mask].copy()


def add_distance_column(
    df: pd.DataFrame,
    origin_lat: float,
    origin_lng: float,
    lat_col: str = "lat",
    lng_col: str = "lng",
    distance_col: str = "distance_km",
) -> pd.DataFrame:
    """Adds a distance_km column to the DataFrame. Rows with NULL coords get NaN."""
    if df.empty:
        df[distance_col] = pd.Series(dtype=float)
        return df

    def _dist(row) -> Optional[float]:
        lat = row.get(lat_col)
        lng = row.get(lng_col)
        if lat is None or lng is None:
            return None
        try:
            return round(haversine_km(origin_lat, origin_lng, float(lat), float(lng)), 1)
        except Exception:
            return None

    df = df.copy()
    df[distance_col] = df.apply(_dist, axis=1)
    return df
