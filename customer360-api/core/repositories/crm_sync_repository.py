"""API-layer facade for segment-to-CRM synchronization."""

from leo_customer360_dao.crud.crm_sync import sync_segment_to_crm
from leo_customer360_dao.repositories.segment_respository import SegmentRepository as DaoSegmentRepository


class CrmSyncRepository:
    """Coordinate segment lookup and the DAO CRM sync engine."""

    def __init__(self, session):
        """Create a sync repository for the request session."""
        self.session = session
        self.segment_repository = DaoSegmentRepository(session)

    def get_segment(self, segment_id):
        """Load a segment using the DAO repository."""
        return self.segment_repository.get_segment(segment_id)

    def sync_segment(self, segment, tenant_id, triggered_by=None, dry_run=False):
        """Run the DAO sync engine for one already-authorized segment."""
        return sync_segment_to_crm(
            self.session,
            segment,
            tenant_id=tenant_id,
            triggered_by=triggered_by,
            dry_run=dry_run,
        )

    def list_sync_runs(self, model, tenant_id, segment_id=None, limit=20):
        """Return recent tenant-scoped sync runs."""
        from sqlalchemy import select

        statement = select(model).where(model.tenant_id == tenant_id)
        if segment_id is not None:
            statement = statement.where(model.segment_id == segment_id)
        return self.session.execute(statement.order_by(model.started_at.desc()).limit(limit)).scalars().all()

    def get_sync_run(self, model, sync_run_id):
        """Load one sync run by primary key."""
        return self.session.get(model, sync_run_id)


SegmentRepository = CrmSyncRepository