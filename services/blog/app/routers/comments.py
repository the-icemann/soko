import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user_id
from app.db.database import get_db
from app.helpers.builders import build_comment_out, make_initials
from app.helpers.cache import (
    get_cached_comments, set_cached_comments, invalidate_comments,
    invalidate_post,
)
from app.models.blog import Post, Comment, CommentLike
from app.schemas.schemas import CommentOut, CreateCommentPayload

router = APIRouter(tags=["Comments"])


# ── Public — get comments for a post
# Matches Comment[] type in frontend exactly
@router.get("/{post_id}/comments", response_model=list[CommentOut])
def get_comments(
    post_id:   str,
    page:      int = Query(default=1,  ge=1),
    limit:     int = Query(default=20, le=50),
    x_user_id: str = Header(default=None),
    db:        Session = Depends(get_db),
):
    # Cache anonymous requests only
    if not x_user_id:
        cached = get_cached_comments(post_id, page, limit)
        if cached:
            return cached

    comments = db.query(Comment).filter(
        Comment.post_id == uuid.UUID(post_id)
    ).order_by(Comment.created_at.desc()) \
     .offset((page - 1) * limit).limit(limit).all()

    result = [build_comment_out(c, viewer_id=x_user_id) for c in comments]

    if not x_user_id:
        set_cached_comments(post_id, page, limit, [r.model_dump() for r in result])

    return result


# ── Authenticated — add comment
# Matches addComment() in the frontend store
@router.post("/{post_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(
    post_id:     str,
    payload:     CreateCommentPayload,
    user_id:     str = Depends(get_current_user_id),
    x_user_name: str = Header(...),   # injected by Gateway from JWT
    db:          Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == uuid.UUID(post_id)).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if not post.is_published:
        raise HTTPException(status_code=400, detail="Cannot comment on an unpublished post")

    comment = Comment(
        post_id=uuid.UUID(post_id),
        author_id=uuid.UUID(user_id),
        author_name=x_user_name,
        author_initials=make_initials(x_user_name),
        body=payload.body,
    )
    db.add(comment)

    # Increment denormalised comment count — matches Post.comments in frontend
    post.comments += 1

    db.commit()
    db.refresh(comment)

    invalidate_comments(post_id)
    invalidate_post(post.slug)   # comment count changed

    return build_comment_out(comment, viewer_id=user_id)


# ── Author only — delete own comment
# Matches deleteComment() in the frontend store
@router.delete("/{post_id}/comments/{comment_id}", status_code=204)
def delete_comment(
    post_id:    str,
    comment_id: str,
    user_id:    str     = Depends(get_current_user_id),
    db:         Session = Depends(get_db),
):
    comment = db.query(Comment).filter(
        Comment.id      == uuid.UUID(comment_id),
        Comment.post_id == uuid.UUID(post_id),
    ).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if str(comment.author_id) != user_id:
        raise HTTPException(status_code=403, detail="Not your comment")

    post = db.query(Post).filter(Post.id == uuid.UUID(post_id)).first()
    if post:
        post.comments = max(0, post.comments - 1)
        invalidate_post(post.slug)

    db.delete(comment)
    db.commit()

    invalidate_comments(post_id)


# ── Authenticated — toggle comment like
# Matches toggleCommentLike() in the frontend store
@router.post("/{post_id}/comments/{comment_id}/like")
def toggle_comment_like(
    post_id:    str,
    comment_id: str,
    user_id:    str     = Depends(get_current_user_id),
    db:         Session = Depends(get_db),
):
    comment = db.query(Comment).filter(
        Comment.id      == uuid.UUID(comment_id),
        Comment.post_id == uuid.UUID(post_id),
    ).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    existing = db.query(CommentLike).filter(
        CommentLike.comment_id == uuid.UUID(comment_id),
        CommentLike.user_id    == uuid.UUID(user_id),
    ).first()

    if existing:
        db.delete(existing)
        comment.likes = max(0, comment.likes - 1)
        liked = False
    else:
        db.add(CommentLike(
            comment_id=uuid.UUID(comment_id),
            user_id=uuid.UUID(user_id),
        ))
        comment.likes += 1
        liked = True

    db.commit()
    invalidate_comments(post_id)

    # Return shape matches what the store expects
    return {"liked": liked, "likes": comment.likes}