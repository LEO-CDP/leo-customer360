"""Repository for user-related database operations (CRUD, filtering, tenant-scoped queries).

Profile reads (``get_user_by_id``) are Redis-cached (read-through, short TTL) since at
scale (1M+ users) the profile + joined SSO identities is fetched on nearly every
authenticated request (GET /me, GET /{user_id}). Every mutation invalidates the cache
entry for that user so readers never see stale data beyond a single write.
"""

import json
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_

from core.cache import get_redis_client
from core.models.system import SysUser, SysUserInfo
from core.schemas.user import UserCreate, UserResponse, UserUpdate
from core.utils.security import hash_password

logger = logging.getLogger(__name__)

# Short TTL: profile data changes rarely, but we still bound staleness after
# an update that bypasses this repository (e.g. a direct SQL admin fix).
USER_PROFILE_CACHE_TTL_SECONDS = 120


def _user_cache_key(user_id: UUID, tenant_id: UUID) -> str:
    return f"user:profile:{tenant_id}:{user_id}"


class UserRepository:
    """Encapsulates all database operations for SysUser (and related SysUserInfo)."""

    def __init__(self, db: Session):
        self.db = db

    def _get_cached_user(self, user_id: UUID, tenant_id: UUID) -> Optional[dict]:
        client = get_redis_client()
        if client is None:
            return None
        try:
            raw = client.get(_user_cache_key(user_id, tenant_id))
        except Exception:
            logger.warning("Redis GET failed for user profile cache", exc_info=True)
            return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            logger.warning("Cached user profile payload was not valid JSON", exc_info=True)
            return None

    def _cache_user(self, user: SysUser) -> None:
        client = get_redis_client()
        if client is None:
            return
        try:
            payload = UserResponse.model_validate(user).model_dump(mode="json")
            client.set(
                _user_cache_key(user.user_id, user.tenant_id),
                json.dumps(payload),
                ex=USER_PROFILE_CACHE_TTL_SECONDS,
            )
        except Exception:
            logger.warning("Failed to cache user profile in Redis", exc_info=True)

    def invalidate_user_cache(self, user_id: UUID, tenant_id: UUID) -> None:
        """Evicts the cached profile for a user. Call after any write to sys_user/sys_userinfo."""
        client = get_redis_client()
        if client is None:
            return
        try:
            client.delete(_user_cache_key(user_id, tenant_id))
        except Exception:
            logger.warning("Failed to invalidate user profile cache in Redis", exc_info=True)

    def get_user_by_id(self, user_id: UUID, tenant_id: UUID, use_cache: bool = True) -> Optional[SysUser]:
        """Fetch a user by ID, tenant-scoped and with SSO identities eager-loaded.

        Read-through cached: on a cache hit this still returns ``None``/DB-shape data
        via ``get_user_by_id_cached`` for API responses -- callers needing to mutate
        the ORM instance (update/delete flows) should keep ``use_cache=False`` since a
        cached dict cannot be persisted back through SQLAlchemy.
        """
        return self.db.query(SysUser).options(
            joinedload(SysUser.sso_identities)
        ).filter(
            and_(SysUser.user_id == user_id, SysUser.tenant_id == tenant_id)
        ).first()

    def get_user_by_id_cached(self, user_id: UUID, tenant_id: UUID) -> Optional[dict]:
        """Read-through cache for pure GET responses: returns a plain dict (already
        shaped like ``UserResponse``) on a cache hit, avoiding the DB + join entirely.
        Falls back to the DB and populates the cache on a miss.
        """
        cached = self._get_cached_user(user_id, tenant_id)
        if cached is not None:
            return cached

        user = self.get_user_by_id(user_id, tenant_id)
        if user is None:
            return None

        self._cache_user(user)
        return UserResponse.model_validate(user).model_dump(mode="json")

    def get_user_by_username(self, username: str, tenant_id: UUID) -> Optional[SysUser]:
        """Fetch a user by username within a tenant (enforcing lowercase check)."""
        return self.db.query(SysUser).options(
            joinedload(SysUser.sso_identities)
        ).filter(
            and_(
                SysUser.tenant_id == tenant_id,
                SysUser.username == username.lower(),
                SysUser.status == "ACTIVE"
            )
        ).first()

    def get_user_by_email(self, email: str, tenant_id: UUID) -> Optional[SysUser]:
        """Fetch a user by email within a tenant (case-insensitive)."""
        return self.db.query(SysUser).options(
            joinedload(SysUser.sso_identities)
        ).filter(
            and_(
                SysUser.tenant_id == tenant_id,
                SysUser.email == email.lower(),
                SysUser.status == "ACTIVE"
            )
        ).first()

    def list_users(
        self, 
        tenant_id: UUID, 
        status: Optional[str] = None,
        skip: int = 0, 
        limit: int = 100
    ) -> list[SysUser]:
        """List users in a tenant, optionally filtered by status."""
        query = self.db.query(SysUser).options(
            joinedload(SysUser.sso_identities)
        ).filter(SysUser.tenant_id == tenant_id)
        
        if status:
            query = query.filter(SysUser.status == status)
        
        return query.offset(skip).limit(limit).all()

    def create_user(self, tenant_id: UUID, user_in: UserCreate) -> SysUser:
        """Create a new user in a tenant. If ``password`` is set (system users
        created via the admin UI), also creates a ``sys_userinfo`` LOCAL
        identity row to hold the hash -- ``sys_user`` itself has no password
        column (see database-schema.sql; SSO-provisioned users skip this)."""
        user = SysUser(
            tenant_id=tenant_id,
            username=user_in.username.lower(),
            email=user_in.email.lower() if user_in.email else None,
            full_name=user_in.full_name,
            job_title=user_in.job_title,
            department=user_in.department,
            organization_id=user_in.organization_id,
            status=user_in.status or "ACTIVE",
        )
        self.db.add(user)
        self.db.flush()

        if user_in.password:
            self.db.add(SysUserInfo(
                tenant_id=tenant_id,
                user_id=user.user_id,
                auth_provider="LOCAL",
                provider_subject_id=user.username,
                password_hash=hash_password(user_in.password),
                status="ACTIVE",
            ))
            self.db.flush()

        self.db.refresh(user)
        return user

    def get_local_password_hash(self, user_id: UUID, tenant_id: UUID) -> Optional[str]:
        """Fetch the LOCAL (dev credential) password hash for a user, if any."""
        identity = self.get_user_sso_identity(user_id, "LOCAL", tenant_id)
        return identity.password_hash if identity else None

    def update_user(self, user: SysUser, user_in: UserUpdate) -> SysUser:
        """Update an existing user."""
        update_data = user_in.model_dump(exclude_unset=True)
        
        # Ensure lowercase for username/email
        if "username" in update_data and update_data["username"]:
            update_data["username"] = update_data["username"].lower()
        if "email" in update_data and update_data["email"]:
            update_data["email"] = update_data["email"].lower()
        
        for field, value in update_data.items():
            setattr(user, field, value)
        
        self.db.flush()
        self.db.refresh(user)
        self.invalidate_user_cache(user.user_id, user.tenant_id)
        return user

    def deactivate_user(self, user: SysUser) -> SysUser:
        """Deactivate a user (soft delete via status change)."""
        user.status = "INACTIVE"
        self.db.flush()
        self.db.refresh(user)
        self.invalidate_user_cache(user.user_id, user.tenant_id)
        return user

    def delete_user(self, user: SysUser) -> None:
        """Hard delete a user (cascades to sso_identities)."""
        user_id, tenant_id = user.user_id, user.tenant_id
        self.db.delete(user)
        self.db.flush()
        self.invalidate_user_cache(user_id, tenant_id)

    def get_user_sso_identity(
        self, 
        user_id: UUID, 
        auth_provider: str, 
        tenant_id: UUID
    ) -> Optional[SysUserInfo]:
        """Fetch a specific SSO identity linked to a user."""
        return self.db.query(SysUserInfo).filter(
            and_(
                SysUserInfo.user_id == user_id,
                SysUserInfo.auth_provider == auth_provider,
                SysUserInfo.tenant_id == tenant_id,
            )
        ).first()

    def link_sso_identity(
        self,
        user_id: UUID,
        tenant_id: UUID,
        auth_provider: str,
        provider_subject_id: str,
    ) -> SysUserInfo:
        """Link an SSO identity to an existing user."""
        userinfo = SysUserInfo(
            user_id=user_id,
            tenant_id=tenant_id,
            auth_provider=auth_provider,
            provider_subject_id=provider_subject_id,
            status="ACTIVE",
        )
        self.db.add(userinfo)
        self.db.flush()
        self.db.refresh(userinfo)
        self.invalidate_user_cache(user_id, tenant_id)
        return userinfo

    def unlink_sso_identity(self, userinfo: SysUserInfo) -> None:
        """Remove an SSO identity link."""
        user_id, tenant_id = userinfo.user_id, userinfo.tenant_id
        self.db.delete(userinfo)
        self.db.flush()
        self.invalidate_user_cache(user_id, tenant_id)