"""API router for user management endpoints (CRUD, RBAC roles, SSO linking, profile access).

Enforces multi-tenant isolation: root and tenant-admin users can access/modify
their own tenant's users and role assignments.
Respects auth_middleware.tenant_id and user_id from request.state, which is set
either by the dev JWT / Keycloak token's tenant_id/user_id claims, or (dev mode,
no token) the X-Tenant-Id/X-User-Id headers -- see core/auth.py::auth_middleware
and customer360-api.md's "Authentication and Tenant Context" section for the
full flow, including how to obtain a token via POST /auth/login.
"""

from typing import Any, Generator, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.auth import is_root_user, require_user_management_admin
from core.database import SessionLocal
from core.repositories.user_repository import UserRepository
from leo_customer360_dao.schemas.user import (
    SYSTEM_ROLE_DEFINITIONS,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserRoleOption,
    UserRolesUpdate,
    UserUpdate,
)


router = APIRouter(prefix="/users", tags=["Users"])


def _validate_role_assignment(
    request: Request,
    repository: UserRepository,
    tenant_id: UUID,
    user_id: UUID,
    requested_roles: list[str],
) -> None:
    """Protect the root role and prevent a root user from self-demoting."""
    is_root = is_root_user(request)
    current_roles = {role.lower() for role in repository.get_role_codes_for_user(tenant_id, user_id)}

    if "root" in requested_roles and not is_root:
        raise HTTPException(status_code=403, detail="Only a root user can assign the root role")
    if "root" in current_roles and not is_root:
        raise HTTPException(status_code=403, detail="Only a root user can change a root user's roles")

    current_user_id = getattr(request.state, "user_id", None)
    if current_user_id and UUID(str(current_user_id)) == user_id and "root" in current_roles and "root" not in requested_roles:
        raise HTTPException(status_code=409, detail="A root user cannot remove their own root role")


# =============================================================================
# Dependencies
# =============================================================================

def get_db_session(request: Request) -> Generator[Session, None, None]:
    """Yields a DB session with tenant_id from auth_middleware set in app config."""
    tenant_id = getattr(request.state, "tenant_id", None)
    
    db = SessionLocal()
    try:
        if tenant_id:
            db.execute(
                text("SELECT set_config('app.tenant_id', :t_id, true)"),
                {"t_id": str(tenant_id)}
            )
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db_session)) -> Any:
    """Resolve the current authenticated user's profile for every protected request.

    Called on nearly every request in the API, so this is a Redis read-through
    cache hit in the common case (``UserRepository.get_user_by_id_cached``) -- at
    1M+ users we cannot afford a Postgres round-trip (+ SSO identities join) per
    request just to answer "is this session still valid / active?". A cache miss
    (first request, or after a profile update invalidates the entry) falls back
    to the DB and repopulates the cache.
    """
    user_id = getattr(request.state, "user_id", None)
    tenant_id = getattr(request.state, "tenant_id", None)
    
    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User identity could not be resolved from token",
        )
    
    repo = UserRepository(db)
    user = repo.get_user_by_id_cached(UUID(str(user_id)), UUID(str(tenant_id)))
    
    if not user or user.get("status") != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive or disabled",
        )
    
    return user


def get_tenant_id(request: Request) -> UUID:
    """Extract and validate tenant_id from request state."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context not available",
        )
    return UUID(str(tenant_id))


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: dict = Depends(get_current_user),
) -> Any:
    """Returns the currently authenticated user's profile with SSO identities."""
    return current_user


@router.get("/roles", response_model=list[UserRoleOption])
async def list_available_roles(
    request: Request,
    current_user: dict = Depends(get_current_user),
) -> list[UserRoleOption]:
    """List the canonical roles available to tenant user management."""
    require_user_management_admin(request)
    return [UserRoleOption(**role) for role in SYSTEM_ROLE_DEFINITIONS]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_in: UserCreate,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """
    Create a new user in the current tenant.
    
    Only root or tenant-admin users can create users (pre-provisioning for future
    SSO login). Username and email must be unique within the tenant.
    """
    require_user_management_admin(request)
    repo = UserRepository(db)
    
    # Check for duplicate username or email
    existing = repo.get_user_by_username(user_in.username, tenant_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{user_in.username}' already exists in this workspace",
        )
    
    if user_in.email:
        existing_email = repo.get_user_by_email(user_in.email, tenant_id)
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{user_in.email}' already exists in this workspace",
            )
    
    try:
        new_user = repo.create_user(tenant_id, user_in)
        _validate_role_assignment(request, repo, tenant_id, new_user.user_id, list(user_in.roles))
        repo.replace_user_roles(tenant_id, new_user.user_id, list(user_in.roles))
        db.commit()
        return repo.get_user_by_id_cached(new_user.user_id, tenant_id)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user: {str(e)}",
        ) from e


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """Fetch a specific user by ID (within current tenant). Redis read-through cached."""
    require_user_management_admin(request)
    repo = UserRepository(db)
    user = repo.get_user_by_id_cached(user_id, tenant_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found in this workspace",
        )
    
    return user


@router.get("", response_model=UserListResponse)
async def list_users(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """List all users in the current tenant (paginated)."""
    require_user_management_admin(request)
    if skip < 0 or limit < 1 or limit > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination parameters",
        )
    
    repo = UserRepository(db)
    
    # Get total count
    total = repo.count_users(tenant_id, status=status_filter)
    
    # Get paginated results
    items = repo.list_users(tenant_id, status=status_filter, skip=skip, limit=limit)
    roles_by_user = repo.get_roles_for_users(
        tenant_id,
        [user.user_id for user in items],
    )
    response_items = []
    for user in items:
        response_items.append(
            UserResponse.model_validate(user).model_copy(
                update={"roles": roles_by_user.get(user.user_id, [])}
            )
        )

    return UserListResponse(
        total=total,
        skip=skip,
        limit=limit,
        items=response_items,
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    user_update: UserUpdate,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """Update user profile (only mutable fields: name, job_title, department, etc.)."""
    require_user_management_admin(request)
    repo = UserRepository(db)
    user = repo.get_user_by_id(user_id, tenant_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found in this workspace",
        )
    
    try:
        updated_user = repo.update_user(user, user_update)
        if user_update.roles is not None:
            _validate_role_assignment(request, repo, tenant_id, user_id, list(user_update.roles))
            repo.replace_user_roles(tenant_id, user_id, list(user_update.roles))
        db.commit()
        return repo.get_user_by_id_cached(updated_user.user_id, tenant_id)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user: {str(e)}",
        ) from e


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    request: Request,
    hard_delete: bool = False,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> None:
    """
    Delete a user (soft delete by default, hard delete if hard_delete=true).
    
    Soft delete: marks user as INACTIVE.
    Hard delete: removes user and all linked SSO identities from database.
    """
    require_user_management_admin(request)
    repo = UserRepository(db)
    user = repo.get_user_by_id(user_id, tenant_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found in this workspace",
        )
    
    try:
        if hard_delete:
            repo.delete_user(user)
        else:
            repo.deactivate_user(user)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user: {str(e)}",
        ) from e


@router.put("/{user_id}/roles", response_model=UserResponse)
async def replace_user_roles(
    user_id: UUID,
    payload: UserRolesUpdate,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """Replace one tenant user's RBAC roles; root assignment is root-only."""
    require_user_management_admin(request)
    repo = UserRepository(db)
    user = repo.get_user_by_id(user_id, tenant_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found in this workspace",
        )

    try:
        _validate_role_assignment(request, repo, tenant_id, user_id, list(payload.roles))
        repo.replace_user_roles(tenant_id, user_id, list(payload.roles))
        db.commit()
        return repo.get_user_by_id_cached(user_id, tenant_id)
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user roles: {str(e)}",
        ) from e


@router.get("/{user_id}/sso-identities", response_model=list)
async def get_user_sso_identities(
    user_id: UUID,
    request: Request,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """List all SSO identities linked to a user."""
    require_user_management_admin(request)
    repo = UserRepository(db)
    user = repo.get_user_by_id(user_id, tenant_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found in this workspace",
        )
    
    return repo.list_sso_identities(user)


all_user_routers = [router]