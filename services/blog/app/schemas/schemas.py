from pydantic import BaseModel, field_validator
from typing import List, Optional
from enum import Enum


class PostCategory(str, Enum):
    soil_crops     = "Soil & Crops"
    livestock      = "Livestock"
    agritech       = "AgriTech"
    sustainability = "Sustainability"
    business       = "Business"
    irrigation     = "Irrigation"
    climate        = "Climate"
    other          = "Other"


class PostSectionType(str, Enum):
    paragraph = "paragraph"
    heading   = "heading"
    quote     = "quote"
    image     = "image"


# ── PostSection — matches PostSection in frontend exactly
class PostSectionIn(BaseModel):
    type:        PostSectionType
    content:     str
    caption:     Optional[str] = None
    attribution: Optional[str] = None


class PostSectionOut(BaseModel):
    type:        str
    content:     str
    caption:     Optional[str] = None
    attribution: Optional[str] = None


# ── Post — matches Post type in frontend exactly
class PostOut(BaseModel):
    id:             str
    slug:           str
    image:          str
    category:       str
    title:          str
    excerpt:        str
    author:         str
    authorInitials: Optional[str] = None
    authorBio:      Optional[str] = None
    likes:          int
    comments:       int
    readTime:       str
    publishedAt:    str
    isLikedByMe:    Optional[bool] = None
    tags:           Optional[List[str]] = None
    # body only populated on single post fetch
    body:           Optional[List[PostSectionOut]] = None


# ── Comment — matches Comment in frontend exactly
class CommentOut(BaseModel):
    id:             str
    postId:         str
    author:         str
    authorInitials: str
    body:           str
    likes:          int
    isLikedByMe:    bool
    createdAt:      str


# ── Create post
class CreatePostPayload(BaseModel):
    title:    str
    excerpt:  str
    image:    Optional[str]       = None
    category: PostCategory
    tags:     Optional[List[str]] = []
    body:     List[PostSectionIn]

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v):
        if not v.strip():
            raise ValueError("Title cannot be empty")
        return v.strip()

    @field_validator("body")
    @classmethod
    def must_have_body(cls, v):
        if not v:
            raise ValueError("Post must have at least one section")
        return v

    @field_validator("tags")
    @classmethod
    def max_tags(cls, v):
        if v and len(v) > 10:
            raise ValueError("Maximum 10 tags allowed")
        return v


# ── Update post
class UpdatePostPayload(BaseModel):
    title:    Optional[str]             = None
    excerpt:  Optional[str]             = None
    image:    Optional[str]             = None
    category: Optional[PostCategory]   = None
    tags:     Optional[List[str]]       = None
    body:     Optional[List[PostSectionIn]] = None


# ── Create comment
class CreateCommentPayload(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def not_empty(cls, v):
        if not v.strip():
            raise ValueError("Comment cannot be empty")
        return v.strip()

class ImageUploadOut(BaseModel):
    url:       str
    public_id: str