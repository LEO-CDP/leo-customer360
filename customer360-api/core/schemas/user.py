"""Pydantic schemas for user-related API requests and responses."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, ConfigDict


class SSOIdentityResponse(BaseModel):
    """SSO identity linked to a user (provider account)."""
    userinfo_id: UUID
    auth_provider: str
    provider_subject_id: str
    status: str
    last_login_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    """Full user profile response with linked SSO identities."""
    user_id: UUID
    username: str
    email: Optional[EmailStr]
    full_name: Optional[str]
    phone: Optional[str]
    job_title: Optional[str]
    department: Optional[str]
    organization_id: Optional[UUID]
    language_code: str = "en"
    timezone: str = "UTC"
    status: str = "ACTIVE"
    last_login_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    sso_identities: List[SSOIdentityResponse] = []

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    """Request model for creating a new user."""
    username: str = Field(..., min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    job_title: Optional[str] = Field(None, max_length=255)
    department: Optional[str] = Field(None, max_length=255)
    organization_id: Optional[UUID] = None
    language_code: Optional[str] = "en"
    timezone: Optional[str] = "UTC"
    status: Optional[str] = "ACTIVE"
    # Optional: only present for admin-created system users (SSO-provisioned
    # users have no local password). Hashed before storage, never echoed back.
    password: Optional[str] = Field(None, min_length=8, max_length=128)


class UserUpdate(BaseModel):
    """Request model for updating a user (only mutable fields)."""
    full_name: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=20)
    job_title: Optional[str] = Field(None, max_length=255)
    department: Optional[str] = Field(None, max_length=255)
    organization_id: Optional[UUID] = None
    language_code: Optional[str] = None
    timezone: Optional[str] = None
    status: Optional[str] = None


class UserListResponse(BaseModel):
    """Paginated list of users."""
    total: int
    skip: int
    limit: int
    items: List[UserResponse]