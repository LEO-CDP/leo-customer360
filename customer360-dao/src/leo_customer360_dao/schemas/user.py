"""Pydantic schemas for user-related API requests and responses."""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


SYSTEM_ROLE_DEFINITIONS = (
    {
        "role_code": "root",
        "role_name": "Root / Super Admin",
        "description": "Unrestricted platform administration and tenant RBAC control.",
    },
    {
        "role_code": "admin",
        "role_name": "Admin",
        "description": "Application administration, users, security policies, billing, and global settings.",
    },
    {
        "role_code": "operator",
        "role_name": "Operator",
        "description": "Daily operations, monitoring, backups, and service maintenance.",
    },
    {
        "role_code": "developer",
        "role_name": "Developer",
        "description": "Engineering access to development environments, APIs, and deployments.",
    },
    {
        "role_code": "auditor",
        "role_name": "Auditor",
        "description": "Read-only access to logs, histories, and security evidence.",
    },
    {
        "role_code": "partner",
        "role_name": "Partner",
        "description": "Restricted access to shared partner data and dashboards.",
    },
    {
        "role_code": "billing",
        "role_name": "Billing / Accountant",
        "description": "Subscriptions, invoices, and payment administration without technical settings.",
    },
    {
        "role_code": "editor",
        "role_name": "Editor / Manager",
        "description": "Create, edit, publish, and manage business content without system settings.",
    },
    {
        "role_code": "contributor",
        "role_name": "Contributor / Author",
        "description": "Create and edit owned work pending editor approval for global publishing.",
    },
    {
        "role_code": "viewer",
        "role_name": "Viewer",
        "description": "Read-only dashboards, reports, and data access.",
    },
)
SYSTEM_ROLE_CODES = tuple(item["role_code"] for item in SYSTEM_ROLE_DEFINITIONS)
SystemRoleCode = Literal[
    "root",
    "admin",
    "operator",
    "developer",
    "auditor",
    "partner",
    "billing",
    "editor",
    "contributor",
    "viewer",
]


def _reject_duplicate_roles(
    roles: Optional[List[SystemRoleCode]],
) -> Optional[List[SystemRoleCode]]:
    if roles is None:
        return None
    if len(roles) != len(set(roles)):
        raise ValueError("roles must not contain duplicates")
    return roles


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
    roles: List[str] = Field(default_factory=list)
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
    roles: List[SystemRoleCode] = Field(default_factory=list, max_length=len(SYSTEM_ROLE_CODES))

    _validate_roles = field_validator("roles")(_reject_duplicate_roles)


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
    roles: Optional[List[SystemRoleCode]] = Field(default=None, max_length=len(SYSTEM_ROLE_CODES))

    _validate_roles = field_validator("roles")(_reject_duplicate_roles)


class UserRoleOption(BaseModel):
    """One role that can be assigned to a tenant user."""

    role_code: SystemRoleCode
    role_name: str
    description: str


class UserRolesUpdate(BaseModel):
    """Replacement set of roles for one tenant user."""

    roles: List[SystemRoleCode] = Field(default_factory=list, max_length=len(SYSTEM_ROLE_CODES))

    _validate_roles = field_validator("roles")(_reject_duplicate_roles)


class UserListResponse(BaseModel):
    """Paginated list of users."""
    total: int
    skip: int
    limit: int
    items: List[UserResponse]