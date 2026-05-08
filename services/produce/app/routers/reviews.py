import uuid
from fastapi import APIRouter, Depends, HTTPException, Header,Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user_id
from app.db.database import get_db
from app.models.produce import Listing, ProductReview, ProductReviewHelpful
from app.schemas.produce import ProductReviewOut, CreateProductReviewPayload
from app.core.cache import (
    get_cached_reviews, set_cached_reviews, invalidate_reviews
)

router = APIRouter(tags=["Product Reviews"])


def make_initials(name: str) -> str:
    parts = name.strip().split()
    return (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else name[:2].upper()


@router.get("/{listing_id}/reviews", response_model=list[ProductReviewOut])
def get_reviews(
    listing_id: str,
    page:      int = Query(default=1,  ge=1),
    limit:     int = Query(default=10, le=50),
    x_user_id: str = Header(default=None),
    db: Session    = Depends(get_db)
):
    # Only cache anonymous requests — viewer-specific helpful flags vary per user
    if not x_user_id:
        cached = get_cached_reviews(listing_id, page, limit)
        if cached:
            return cached

    reviews = db.query(ProductReview).filter(
        ProductReview.listing_id == uuid.UUID(listing_id)
    ).order_by(ProductReview.created_at.desc()) \
     .offset((page - 1) * limit).limit(limit).all()

    result = []
    for r in reviews:
        is_helpful = None
        if x_user_id:
            is_helpful = db.query(ProductReviewHelpful).filter(
                ProductReviewHelpful.review_id == r.id,
                ProductReviewHelpful.voter_id  == uuid.UUID(x_user_id)
            ).first() is not None
        result.append(ProductReviewOut(
            id=str(r.id),
            reviewer=r.reviewer_name,
            reviewerInitials=r.reviewer_initials,
            rating=r.rating,
            body=r.body,
            createdAt=r.created_at.isoformat(),
            helpful=r.helpful,
            isHelpfulByMe=is_helpful,
        ))

    if not x_user_id:
        set_cached_reviews(listing_id, page, limit, [r.model_dump() for r in result])

    return result


@router.post("/{listing_id}/reviews", response_model=ProductReviewOut, status_code=201)
def add_review(
    listing_id:   str,
    payload:      CreateProductReviewPayload,
    user_id:      str     = Depends(get_current_user_id),
    x_user_name:  str     = Header(...),
    db:           Session = Depends(get_db)
):
    listing = db.query(Listing).filter(Listing.id == uuid.UUID(listing_id)).first()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(listing.farmer_id) == user_id:
        raise HTTPException(status_code=400, detail="Cannot review your own listing")

    if db.query(ProductReview).filter(
        ProductReview.listing_id  == uuid.UUID(listing_id),
        ProductReview.reviewer_id == uuid.UUID(user_id)
    ).first():
        raise HTTPException(status_code=409, detail="You have already reviewed this product")

    review = ProductReview(
        listing_id=uuid.UUID(listing_id),
        reviewer_id=uuid.UUID(user_id),
        reviewer_name=x_user_name,
        reviewer_initials=make_initials(x_user_name),
        rating=payload.rating,
        body=payload.body,
    )
    db.add(review)

    # Recalculate average rating
    all_ratings = [r.rating for r in db.query(ProductReview).filter(
        ProductReview.listing_id == uuid.UUID(listing_id)
    ).all()] + [payload.rating]
    listing.average_rating = round(sum(all_ratings) / len(all_ratings), 2)
    listing.review_count   = len(all_ratings)

    db.commit()
    db.refresh(review)
    invalidate_reviews(listing_id)
    return ProductReviewOut(
        id=str(review.id),
        reviewer=review.reviewer_name,
        reviewerInitials=review.reviewer_initials,
        rating=review.rating,
        body=review.body,
        createdAt=review.created_at.isoformat(),
        helpful=review.helpful,
        isHelpfulByMe=False,
    )


@router.post("/reviews/{review_id}/helpful")
def mark_helpful(
    review_id: str,
    user_id:   str     = Depends(get_current_user_id),
    db:        Session = Depends(get_db)
):
    review = db.query(ProductReview).filter(
        ProductReview.id == uuid.UUID(review_id)
    ).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    vote = db.query(ProductReviewHelpful).filter(
        ProductReviewHelpful.review_id == uuid.UUID(review_id),
        ProductReviewHelpful.voter_id  == uuid.UUID(user_id)
    ).first()

    if vote:
        db.delete(vote)
        review.helpful = max(0, review.helpful - 1)
    else:
        db.add(ProductReviewHelpful(
            review_id=uuid.UUID(review_id),
            voter_id=uuid.UUID(user_id)
        ))
        review.helpful += 1

    db.commit()
    return {"helpful": review.helpful}