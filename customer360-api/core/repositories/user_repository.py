"""API-layer user repository extensions."""

from uuid import UUID

from sqlalchemy import func

from leo_customer360_dao.models.system import SysUser
from leo_customer360_dao.repositories.user_repository import UserRepository as DaoUserRepository


class UserRepository(DaoUserRepository):
    """Adds tenant-scoped aggregate and identity queries to the DAO repository."""

    def count_users(self, tenant_id: UUID, status: str | None = None) -> int:
        """Count users in a tenant, optionally restricted to one status."""
        query = self.db.query(func.count(SysUser.user_id)).filter(SysUser.tenant_id == tenant_id)
        if status:
            query = query.filter(SysUser.status == status)
        return query.scalar() or 0

    def list_sso_identities(self, user: SysUser) -> list:
        """Return the already-loaded SSO identities for a user."""
        return user.sso_identities or []