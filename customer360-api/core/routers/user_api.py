"""API router for user management endpoints (CRUD, SSO linking, profile access).

Enforces multi-tenant isolation: users can only access/modify their own tenant's users.
Respects auth_middleware.tenant_id and user_id from request.state.
"""

from typing import Any, Generator, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from core.database import SessionLocal
from core.models.system import SysUser
from core.repositories.user_repository import UserRepository
from core.schemas.user import UserCreate, UserUpdate, UserResponse, UserListResponse


router = APIRouter(prefix="/users", tags=["Users"])


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
    
    Only authenticated users can create users (pre-provisioning for future SSO login).
    Username and email must be unique within the tenant.
    """
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
        db.commit()
        return new_user
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create user: {str(e)}",
        ) from e


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """Fetch a specific user by ID (within current tenant). Redis read-through cached."""
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
    skip: int = 0,
    limit: int = 100,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """List all users in the current tenant (paginated)."""
    if skip < 0 or limit < 1 or limit > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pagination parameters",
        )
    
    repo = UserRepository(db)
    
    # Get total count
    total_query = db.query(func.count(SysUser.user_id)).filter(
        SysUser.tenant_id == tenant_id
    )
    if status_filter:
        total_query = total_query.filter(SysUser.status == status_filter)
    total = total_query.scalar() or 0
    
    # Get paginated results
    items = repo.list_users(tenant_id, status=status_filter, skip=skip, limit=limit)
    
    return UserListResponse(
        total=total,
        skip=skip,
        limit=limit,
        items=items,
    )


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    user_update: UserUpdate,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """Update user profile (only mutable fields: name, job_title, department, etc.)."""
    repo = UserRepository(db)
    user = repo.get_user_by_id(user_id, tenant_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found in this workspace",
        )
    
    try:
        updated_user = repo.update_user(user, user_update)
        db.commit()
        return updated_user
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update user: {str(e)}",
        ) from e


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
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


@router.get("/{user_id}/sso-identities", response_model=list)
async def get_user_sso_identities(
    user_id: UUID,
    db: Session = Depends(get_db_session),
    current_user: dict = Depends(get_current_user),
    tenant_id: UUID = Depends(get_tenant_id),
) -> Any:
    """List all SSO identities linked to a user."""
    repo = UserRepository(db)
    user = repo.get_user_by_id(user_id, tenant_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{user_id}' not found in this workspace",
        )
    
    return user.sso_identities or []


all_user_routers = [router]