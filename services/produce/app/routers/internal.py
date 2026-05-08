import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.produce import Listing, ListingStatus
from app.schemas.produce import StockUpdatePayload,ListingOut
from app.core.dependencies import internal_only
from app.helpers.builders import build_listing_out

router = APIRouter(tags=["Internal"], dependencies=[Depends(internal_only)])


@router.put("/stock/decrement")
def decrement_stock(payload: StockUpdatePayload, db: Session = Depends(get_db)):
    """Called by Order Service when an order is confirmed."""
    listing = db.query(Listing).filter(Listing.id == uuid.UUID(payload.listing_id)).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing.available_qty < payload.quantity:
        raise HTTPException(status_code=400, detail="Insufficient stock")

    listing.available_qty -= payload.quantity
    if listing.available_qty <= 0:
        listing.available_qty = 0
        listing.status        = ListingStatus.sold_out

    db.commit()
    return {"available_qty": listing.available_qty, "status": listing.status.value}


@router.put("/stock/restore")
def restore_stock(payload: StockUpdatePayload, db: Session = Depends(get_db)):
    """Called by Order Service when an order is cancelled."""
    listing = db.query(Listing).filter(Listing.id == uuid.UUID(payload.listing_id)).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    listing.available_qty += payload.quantity
    if listing.status == ListingStatus.sold_out and listing.available_qty > 0:
        listing.status = ListingStatus.active

    db.commit()
    return {"available_qty": listing.available_qty, "status": listing.status.value}

@router.get("/listing/{listing_id}", response_model=ListingOut)
def get_listing_by_id(
    listing_id: str,
    db: Session = Depends(get_db)
):
    """
    Called by Order Service to verify stock and fetch product snapshot
    before checkout. Protected by internal_only dependency.
    """
    try:
        lid = uuid.UUID(listing_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid listing ID")

    listing = db.query(Listing).filter(Listing.id == lid).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    return build_listing_out(listing)