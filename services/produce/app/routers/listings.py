import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.dependencies import farmer_only, get_current_user_id
from app.db.database import get_db
from app.helpers.builders import build_listing_out, generate_slug
from app.models.produce import Listing, ListingStatus, PriceTier
from app.schemas.produce import (
    AiPriceSuggestion, CreateListingPayload, CreateListingResponse,
    ListingOut, UpdateListingPayload
)
from app.core.cache import (
    get_cached_listings, set_cached_listings, invalidate_listings,
    get_cached_listing, set_cached_listing, invalidate_listing,
    get_cached_farmer_listings, set_cached_farmer_listings, invalidate_farmer_listings,
    get_cached_price_suggestion, set_cached_price_suggestion, invalidate_price_suggestions,
)
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Listings"])


async def fetch_farmer_snapshot(farmer_id: str) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{settings.USER_SERVICE_URL}/users/{farmer_id}",
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.warning(f"Could not fetch farmer snapshot for {farmer_id}: {e}")
    return {}


# ── Public — browse all active listings
@router.get("/", response_model=list[ListingOut])
def get_listings(
    category:  Optional[str]   = Query(default=None),
    district:  Optional[str]   = Query(default=None),
    fresh:     Optional[bool]  = Query(default=None),
    min_price: Optional[float] = Query(default=None),
    max_price: Optional[float] = Query(default=None),
    search:    Optional[str]   = Query(default=None),
    page:      int             = Query(default=1, ge=1),
    limit:     int             = Query(default=20, le=100),
    db: Session = Depends(get_db)
):
    # Check cache first
    cached = get_cached_listings(category, district, fresh, min_price, max_price, search, page, limit)
    if cached:
        return cached

    q = db.query(Listing).filter(Listing.status == ListingStatus.active)
    if category:          q = q.filter(Listing.category  == category)
    if district:          q = q.filter(Listing.district  == district)
    if fresh is not None: q = q.filter(Listing.fresh     == fresh)
    if min_price:         q = q.filter(Listing.price     >= min_price)
    if max_price:         q = q.filter(Listing.price     <= max_price)
    if search:
        term = f"%{search}%"
        q = q.filter(or_(
            Listing.name.ilike(term),
            Listing.description.ilike(term),
            Listing.tags.ilike(term),
        ))

    listings = q.order_by(Listing.created_at.desc()) \
                .offset((page - 1) * limit).limit(limit).all()

    result = [build_listing_out(l) for l in listings]

    # Serialise to dicts for caching
    set_cached_listings(
        category, district, fresh, min_price, max_price, search, page, limit,
        [r.model_dump() for r in result]
    )
    return result


# Public — single listing by slug
@router.get("/slug/{slug}", response_model=ListingOut)
def get_listing_by_slug(slug: str, db: Session = Depends(get_db)):
    cached = get_cached_listing(slug)
    if cached:
        return cached

    listing = db.query(Listing).filter(Listing.slug == slug).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    result = build_listing_out(listing)
    set_cached_listing(slug, result.model_dump())
    return result


# Public — all active listings by a specific farmer
@router.get("/farmer/{farmer_id}", response_model=list[ListingOut])
def get_farmer_listings(
    farmer_id: str,
    page:  int = Query(default=1,  ge=1),
    limit: int = Query(default=20, le=100),
    db: Session = Depends(get_db)
):
    cached = get_cached_farmer_listings(farmer_id, page, limit)
    if cached:
        return cached

    listings = db.query(Listing).filter(
        Listing.farmer_id == uuid.UUID(farmer_id),
        Listing.status    == ListingStatus.active
    ).order_by(Listing.created_at.desc()) \
     .offset((page - 1) * limit).limit(limit).all()

    result = [build_listing_out(l) for l in listings]
    set_cached_farmer_listings(farmer_id, page, limit, [r.model_dump() for r in result])
    return result


# ── Farmer — my own listings (all statuses)
@router.get("/me", response_model=list[ListingOut])
def get_my_listings(
    status: Optional[str] = Query(default=None),
    page:   int           = Query(default=1,  ge=1),
    limit:  int           = Query(default=20, le=100),
    user_id: str          = Depends(get_current_user_id),
    db: Session           = Depends(get_db)
):
    q = db.query(Listing).filter(Listing.farmer_id == uuid.UUID(user_id))
    if status:
        q = q.filter(Listing.status == status)

    listings = q.order_by(Listing.created_at.desc()) \
                .offset((page - 1) * limit).limit(limit).all()
    return [build_listing_out(l) for l in listings]


#  Farmer only — create listing
@router.post("/", response_model=CreateListingResponse, status_code=201,
             dependencies=[Depends(farmer_only)])
async def create_listing(
    payload: CreateListingPayload,
    user_id: str    = Depends(get_current_user_id),
    db: Session     = Depends(get_db)
):
    # Fetch farmer snapshot from User Service
    farmer = await fetch_farmer_snapshot(user_id)

    slug = generate_slug(payload.name, user_id)
    if db.query(Listing).filter(Listing.slug == slug).first():
        slug = f"{slug}-{str(uuid.uuid4())[:4]}"

    # Parse harvestDate if provided
    harvest_date = None
    if payload.harvestDate:
        try:
            harvest_date = datetime.fromisoformat(payload.harvestDate)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid harvestDate format — use ISO 8601")

    listing = Listing(
        farmer_id=uuid.UUID(user_id),
        slug=slug,
        name=payload.name,
        category=payload.category,
        description=payload.description,
        tags=",".join(payload.tags) if payload.tags else None,
        district=payload.district,
        village=payload.village,
        price=payload.price,
        unit=payload.unit,
        total_qty=payload.totalQty,
        available_qty=payload.totalQty,
        minimum_order=payload.minimumOrder,
        fresh=payload.fresh,
        harvest_date=harvest_date,
        storage_notes=payload.storage,
        status=ListingStatus.draft,
        # Farmer snapshot
        farmer_name=farmer.get("name", ""),
        farmer_district=farmer.get("district", payload.district),
        farmer_verified=farmer.get("verified", False),
        farmer_phone=farmer.get("phone"),
        farmer_response_time=farmer.get("responseTime"),
        farmer_member_since=farmer.get("memberSince"),
        farmer_total_sales=farmer.get("totalSales", 0),
    )
    db.add(listing)
    db.flush()

    if payload.priceTiers:
        for tier in payload.priceTiers:
            db.add(PriceTier(
                listing_id=listing.id,
                min_qty=tier.minQty,
                price=tier.price,
                label=tier.label,
            ))

    db.commit()
    db.refresh(listing)

    # Invalidate browse cache and farmer listings cache
    invalidate_listings()
    invalidate_farmer_listings(user_id)
    invalidate_price_suggestions()

    return CreateListingResponse(
        id=str(listing.id),
        slug=listing.slug,
        imageUrls=[],
        message="Listing created as draft. Upload images then publish."
    )


# ── Farmer only — update listing
@router.put("/{listing_id}", response_model=ListingOut,
            dependencies=[Depends(farmer_only)])
def update_listing(
    listing_id: str,
    payload:    UpdateListingPayload,
    user_id:    str     = Depends(get_current_user_id),
    db:         Session = Depends(get_db)
):
    listing = db.query(Listing).filter(Listing.id == uuid.UUID(listing_id)).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(listing.farmer_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your listing")

    if payload.name         is not None: listing.name          = payload.name
    if payload.category     is not None: listing.category      = payload.category
    if payload.district     is not None: listing.district      = payload.district
    if payload.village      is not None: listing.village       = payload.village
    if payload.description  is not None: listing.description   = payload.description
    if payload.price        is not None: listing.price         = payload.price
    if payload.unit         is not None: listing.unit          = payload.unit
    if payload.minimumOrder is not None: listing.minimum_order = payload.minimumOrder
    if payload.fresh        is not None: listing.fresh         = payload.fresh
    if payload.status       is not None: listing.status        = payload.status
    if payload.storage      is not None: listing.storage_notes = payload.storage
    if payload.tags         is not None: listing.tags          = ",".join(payload.tags)
    if payload.totalQty     is not None:
        listing.total_qty     = payload.totalQty
        listing.available_qty = payload.totalQty

    if payload.harvestDate is not None:
        try:
            listing.harvest_date = datetime.fromisoformat(payload.harvestDate)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid harvestDate format")

    if payload.priceTiers is not None:
        for t in listing.price_tiers:
            db.delete(t)
        for tier in payload.priceTiers:
            db.add(PriceTier(
                listing_id=listing.id,
                min_qty=tier.minQty,
                price=tier.price,
                label=tier.label,
            ))

    db.commit()
    db.refresh(listing)

    invalidate_listings()
    invalidate_listing(listing.slug)
    invalidate_farmer_listings(user_id)
    if payload.price is not None:
        invalidate_price_suggestions()

    return build_listing_out(listing)


# ── Farmer only — publish draft
@router.post("/{listing_id}/publish", response_model=ListingOut,
             dependencies=[Depends(farmer_only)])
def publish_listing(
    listing_id: str,
    user_id:    str     = Depends(get_current_user_id),
    db:         Session = Depends(get_db)
):
    listing = db.query(Listing).filter(Listing.id == uuid.UUID(listing_id)).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(listing.farmer_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your listing")
    if not listing.images:
        raise HTTPException(status_code=400, detail="Upload at least one image before publishing")

    listing.status = ListingStatus.active
    db.commit()
    db.refresh(listing)
    return build_listing_out(listing)


# ── Farmer only — archive (soft delete)
@router.delete("/{listing_id}", status_code=204,
               dependencies=[Depends(farmer_only)])
def archive_listing(
    listing_id: str,
    user_id:    str     = Depends(get_current_user_id),
    db:         Session = Depends(get_db)
):
    listing = db.query(Listing).filter(Listing.id == uuid.UUID(listing_id)).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(listing.farmer_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your listing")

    listing.status = ListingStatus.archived
    db.commit()

    invalidate_listings()
    invalidate_listing(listing.slug)
    invalidate_farmer_listings(user_id)


# ── Farmer only — price suggestion
@router.get("/price-suggestion", response_model=AiPriceSuggestion,
            dependencies=[Depends(farmer_only)])
def get_price_suggestion(
    category: str          = Query(...),
    unit:     str          = Query(...),
    district: Optional[str] = Query(default=None),
    db: Session             = Depends(get_db)
):
    cached = get_cached_price_suggestion(category, unit, district)
    if cached:
        return cached

    q = db.query(Listing).filter(
        Listing.category == category,
        Listing.unit     == unit,
        Listing.status   == ListingStatus.active,
    )
    if district:
        q = q.filter(Listing.district == district)

    prices = [l.price for l in q.all()]

    if not prices:
        return AiPriceSuggestion(
            min=0, max=0, suggested=0,
            basis=f"No listings found yet for {category} in {district or 'Uganda'}"
        )

    avg       = sum(prices) / len(prices)
    suggested = round(avg / 100) * 100   # round to nearest 100 UGX
    loc       = district or "Uganda"

    result = AiPriceSuggestion(
        min=round(min(prices)),
        max=round(max(prices)),
        suggested=suggested,
        basis=f"Based on {len(prices)} active listings in {loc}"
    )
    set_cached_price_suggestion(category,unit,district,result.model_dump())
    return result