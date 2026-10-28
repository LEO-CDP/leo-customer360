"""API-layer facade for relation and interaction persistence."""

from leo_customer360_dao.repositories.relations_repository import RelationsRepository as DaoRelationsRepository


class RelationsRepository(DaoRelationsRepository):
    """Expose the DAO relations repository through the API layer."""

    def __init__(self, session):
        """Create a relations repository for the request session."""
        super().__init__(session)