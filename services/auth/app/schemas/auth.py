from pydantic import BaseModel, EmailStr, field_validator, model_validator
from typing import List, Optional
from enum import Enum


class UserRole(str, Enum):
    buyer = "buyer"
    farmer = "farmer"
    both = "both"
    admin = "admin"


class RegisterPayload(BaseModel):
    fullName: str
    email: EmailStr
    password: str
    phone: str
    district: str
    role: UserRole
    avatar_url: Optional[str] = None
    specialties: Optional[List[str]] = None
    interests: Optional[List[str]] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v):
        if len(v) < 5:
            raise ValueError("Password must be at least 5 characters")
        return v

    @model_validator(mode="after")
    def validate_role_requirements(self):
        if self.role == UserRole.farmer:
            if not self.specialties:
                raise ValueError("Farmers must provide at least 1 specialty")
        if self.role == UserRole.buyer:
            if not self.interests or not (1 <= len(self.interests) <= 3):
                raise ValueError("Buyers must provide at least 1 interest")
        if self.role == UserRole.both:
            if not self.specialties:
                raise ValueError("Role 'both' requires at least 1 specialty")
            if not self.interests:
                raise ValueError("Role 'both' requires at least 1 interest")
        return self


class LoginPayload(BaseModel):
    email: EmailStr
    password: str


class AuthTokens(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthUserMinimal(BaseModel):
    id: str
    email: str
    role: UserRole


class LoginResponse(BaseModel):
    tokens: AuthTokens
    user: AuthUserMinimal


class VerifyTokenRequest(BaseModel):
    token: str


class VerifyTokenResponse(BaseModel):
    valid: bool
    user_id: str
    role: str
    email: str


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password: str


class CompleteProfileRequest(BaseModel):
    role: UserRole
    phone: str
    district: str
    specialties: Optional[List[str]] = None
    interests: Optional[List[str]] = None

    @model_validator(mode="after")
    def validate_role_requirements(self):
        if self.role == UserRole.farmer:
            if not self.specialties:
                raise ValueError("Farmers must provide at least 1 specialty")
        if self.role == UserRole.buyer:
            if not self.interests or not (1 <= len(self.interests) <= 3):
                raise ValueError("Buyers must provide at least 1 interest")
        if self.role == UserRole.both:
            if not self.specialties:
                raise ValueError("Role 'both' requires at least 1 specialty")
            if not self.interests:
                raise ValueError("Role 'both' requires at least 1 interest")
        return self