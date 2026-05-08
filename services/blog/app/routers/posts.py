import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query, UploadFile, File
from sqlalchemy import or_
from sqlalchemy.orm import Session
import httpx

from app.core.config import settings
from app.core.dependencies import get_current_user_id
from app.db.database import get_db
from app.helpers.builders import build_post_out, generate_slug, estimate_read_time
from app.helpers.cache import (
    get_cached_posts, set_cached_posts, invalidate_posts,
    get_cached_post, set_cached_post, invalidate_post,
)
from app.models.blog import Post, PostSection, PostLike
from app.schemas.schemas import PostOut, CreatePostPayload, UpdatePostPayload
from app.helpers.cloudinary import (
    upload_cover_image,
    upload_body_image,
    delete_post_images,
)
from app.schemas.schemas import ImageUploadOut

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Posts"])


async def fetch_author(author_id: str) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{settings.USER_SERVICE_URL}/users/{author_id}",
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0,
            )
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.warning(f"Could not fetch author {author_id}: {e}")
    return {}


# ── Public — list posts (paginated + filtered)
@router.get("/", response_model=list[PostOut])
def get_posts(
    category:  Optional[str] = Query(default=None),
    tag:       Optional[str] = Query(default=None),
    search:    Optional[str] = Query(default=None),
    author_id: Optional[str] = Query(default=None),
    page:      int           = Query(default=1,  ge=1),
    limit:     int           = Query(default=20, le=100),
    x_user_id: str           = Header(default=None),
    db:        Session       = Depends(get_db),
):
    # Cache anonymous requests only — isLikedByMe varies per user
    if not x_user_id and not search:
        cached = get_cached_posts(category, tag, search, page, limit)
        if cached:
            return cached

    q = db.query(Post).filter(Post.is_published == True)

    if category:  q = q.filter(Post.category  == category)
    if author_id: q = q.filter(Post.author_id == uuid.UUID(author_id))
    if tag:       q = q.filter(Post.tags.ilike(f"%{tag}%"))
    if search:
        term = f"%{search}%"
        q = q.filter(or_(
            Post.title.ilike(term),
            Post.excerpt.ilike(term),
            Post.tags.ilike(term),
        ))

    posts = q.order_by(Post.published_at.desc()) \
              .offset((page - 1) * limit).limit(limit).all()

    result = [build_post_out(p, viewer_id=x_user_id) for p in posts]

    if not x_user_id and not search:
        set_cached_posts(category, tag, search, page, limit,
                         [r.model_dump() for r in result])

    return result


# ── Public — single post by slug (with full body)
@router.get("/{slug}", response_model=PostOut)
def get_post(
    slug:      str,
    x_user_id: str     = Header(default=None),
    db:        Session = Depends(get_db),
):
    if not x_user_id:
        cached = get_cached_post(slug)
        if cached:
            return cached

    post = db.query(Post).filter(
        Post.slug         == slug,
        Post.is_published == True,
    ).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    result = build_post_out(post, viewer_id=x_user_id, with_body=True)

    if not x_user_id:
        set_cached_post(slug, result.model_dump())

    return result


# ── Authenticated — author's own posts (drafts + published)
@router.get("/me/posts", response_model=list[PostOut])
def get_my_posts(
    page:    int     = Query(default=1,  ge=1),
    limit:   int     = Query(default=20, le=100),
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db),
):
    posts = db.query(Post).filter(
        Post.author_id == uuid.UUID(user_id)
    ).order_by(Post.created_at.desc()) \
     .offset((page - 1) * limit).limit(limit).all()

    return [build_post_out(p, viewer_id=user_id) for p in posts]


# ── Authenticated — create post
@router.post("/", response_model=PostOut, status_code=201)
async def create_post(
    payload: CreatePostPayload,
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db),
):
    author = await fetch_author(user_id)

    slug = generate_slug(payload.title, user_id)
    if db.query(Post).filter(Post.slug == slug).first():
        import uuid as _uuid
        slug = f"{slug}-{str(_uuid.uuid4())[:4]}"

    post = Post(
        author_id=uuid.UUID(user_id),
        slug=slug,
        author_name=author.get("name", ""),
        author_initials=author.get("initials") or
            (author.get("name", "")[:2].upper() if author.get("name") else ""),
        author_bio=author.get("farmerBio") or author.get("bio"),
        author_avatar=author.get("avatarUrl"),
        title=payload.title,
        excerpt=payload.excerpt,
        image=payload.image,
        category=payload.category,
        tags=",".join(payload.tags) if payload.tags else None,
        is_published=False,
    )
    db.add(post)
    db.flush()

    for idx, section in enumerate(payload.body):
        db.add(PostSection(
            post_id=post.id,
            type=section.type,
            content=section.content,
            caption=section.caption,
            attribution=section.attribution,
            order=idx,
        ))

    post.read_time = estimate_read_time(payload.body)
    db.commit()
    db.refresh(post)

    return build_post_out(post, viewer_id=user_id, with_body=True)


# ── Author only — update post
@router.put("/{post_id}", response_model=PostOut)
def update_post(
    post_id: str,
    payload: UpdatePostPayload,
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == uuid.UUID(post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if str(post.author_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your post")

    if payload.title    is not None: post.title    = payload.title
    if payload.excerpt  is not None: post.excerpt  = payload.excerpt
    if payload.image    is not None: post.image    = payload.image
    if payload.category is not None: post.category = payload.category
    if payload.tags     is not None: post.tags     = ",".join(payload.tags)

    if payload.body is not None:
        for s in post.sections:
            db.delete(s)
        for idx, section in enumerate(payload.body):
            db.add(PostSection(
                post_id=post.id,
                type=section.type,
                content=section.content,
                caption=section.caption,
                attribution=section.attribution,
                order=idx,
            ))
        post.read_time = estimate_read_time(payload.body)

    db.commit()
    db.refresh(post)

    invalidate_post(post.slug)
    invalidate_posts()

    return build_post_out(post, viewer_id=user_id, with_body=True)


# ── Author only — publish draft
@router.post("/{post_id}/publish", response_model=PostOut)
def publish_post(
    post_id: str,
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == uuid.UUID(post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if str(post.author_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your post")
    if not post.sections:
        raise HTTPException(status_code=400, detail="Cannot publish a post with no content")

    post.is_published = True
    post.published_at = datetime.utcnow()
    db.commit()
    db.refresh(post)

    invalidate_posts()

    return build_post_out(post, viewer_id=user_id, with_body=True)


# ── Author only — delete post
# ── Author only — delete post
@router.delete("/{post_id}", status_code=204)
def delete_post(
    post_id: str,
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == uuid.UUID(post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if str(post.author_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your post")

    invalidate_post(post.slug)
    invalidate_posts()

    # Delete images from Cloudinary before removing from DB
    delete_post_images(post_id)    # ← add this line

    db.delete(post)
    db.commit()

# Frontend calls this after creating a draft, then sets post.image to the returned URL
@router.post("/{post_id}/cover", response_model=ImageUploadOut)
async def upload_cover(
    post_id: str,
    file:    UploadFile = File(...),
    user_id: str        = Depends(get_current_user_id),
    db:      Session    = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == uuid.UUID(post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if str(post.author_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your post")

    result = await upload_cover_image(file, post_id)

    # Save URL back to post
    post.image = result["url"]
    db.commit()

    # Bust cache so the new cover appears immediately
    invalidate_post(post.slug)
    invalidate_posts()

    return ImageUploadOut(url=result["url"], public_id=result["public_id"])

# Returns URL which is then used as content in a PostSection { type: "image", content: url }
@router.post("/{post_id}/body-image", response_model=ImageUploadOut)
async def upload_body_image_endpoint(
    post_id: str,
    order:   int        = 0,
    file:    UploadFile = File(...),
    user_id: str        = Depends(get_current_user_id),
    db:      Session    = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == uuid.UUID(post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if str(post.author_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your post")

    result = await upload_body_image(file, post_id, order)
    return ImageUploadOut(url=result["url"], public_id=result["public_id"])

# ── Authenticated — toggle post like
# Matches togglePostLike() in the frontend store
@router.post("/{post_id}/like")
def toggle_post_like(
    post_id: str,
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == uuid.UUID(post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    existing = db.query(PostLike).filter(
        PostLike.post_id == uuid.UUID(post_id),
        PostLike.user_id == uuid.UUID(user_id),
    ).first()

    if existing:
        db.delete(existing)
        post.likes = max(0, post.likes - 1)
        liked = False
    else:
        db.add(PostLike(
            post_id=uuid.UUID(post_id),
            user_id=uuid.UUID(user_id),
        ))
        post.likes += 1
        liked = True

    db.commit()

    # Bust single post cache — isLikedByMe changed
    invalidate_post(post.slug)

    # Return shape matches what the store expects for optimistic update sync
    return {"liked": liked, "likes": post.likes}