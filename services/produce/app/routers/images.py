import uuid
from fastapi import APIRouter, Depends, Header, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import farmer_only, get_current_user_id
from app.db.database import get_db
from app.helpers.builders import build_listing_out
from app.helpers.cloudinary import upload_listing_image, delete_cloudinary_image
from app.models.produce import Listing, ListingImage
from app.schemas.produce import ListingOut

router  = APIRouter(tags=["Images"])
MAX_IMG = 5


@router.post("/{listing_id}/images", response_model=ListingOut,
             dependencies=[Depends(farmer_only)])
async def upload_images(
    listing_id: str,
    files:      list[UploadFile] = File(...),
    user_id:    str              = Depends(get_current_user_id),
    db:         Session          = Depends(get_db)
):
    listing = db.query(Listing).filter(Listing.id == uuid.UUID(listing_id)).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(listing.farmer_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your listing")

    current = len(listing.images)
    if current + len(files) > MAX_IMG:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_IMG} images. You already have {current}."
        )

    for idx, file in enumerate(files):
        order  = current + idx
        result = await upload_listing_image(file, listing_id, order)
        db.add(ListingImage(
            listing_id=listing.id,
            url=result["url"],           # Cloudinary CDN URL
            public_id=result["public_id"],  # stored for clean deletion
            order=order,
        ))

    db.commit()
    db.refresh(listing)
    return build_listing_out(listing)


@router.delete("/{listing_id}/images/{image_id}",
               dependencies=[Depends(farmer_only)])
def delete_image(
    listing_id: str,
    image_id:   str,
    user_id:    str     = Depends(get_current_user_id),
    db:         Session = Depends(get_db)
):
    listing = db.query(Listing).filter(Listing.id == uuid.UUID(listing_id)).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(listing.farmer_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your listing")

    image = db.query(ListingImage).filter(
        ListingImage.id         == uuid.UUID(image_id),
        ListingImage.listing_id == uuid.UUID(listing_id)
    ).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")

    # Delete from Cloudinary using stored public_id
    if image.public_id:
        delete_cloudinary_image(image.public_id)

    db.delete(image)
    db.commit()
    return {"deleted": True}


# ── Internal seed endpoint ─────────────────────────────────────────────────────
# Registers an external image URL without a Cloudinary upload.
# Only reachable with the INTERNAL_SECRET header — never called by real users.
# public_id is left null so the delete handler skips the Cloudinary call cleanly.

class _SeedImageBody(BaseModel):
    url: str


@router.post("/{listing_id}/images/url", response_model=ListingOut)
def seed_image_url(
    listing_id:      str,
    body:            _SeedImageBody,
    internal_secret: str | None = Header(None, alias="X-Internal-Secret"),
    db:              Session    = Depends(get_db),
):
    if internal_secret != settings.INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    listing = db.query(Listing).filter(Listing.id == uuid.UUID(listing_id)).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    existing = len(listing.images)
    db.add(ListingImage(
        listing_id=listing.id,
        url=body.url,
        public_id=None,
        order=existing,
    ))
    db.commit()
    db.refresh(listing)
    return build_listing_out(listing)