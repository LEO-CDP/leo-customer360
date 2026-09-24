"""API-layer facade for graph-edge persistence."""

from leo_customer360_dao.repositories.graph_repository import GraphRepository as DaoGraphRepository


class GraphRepository(DaoGraphRepository):
    """Expose the DAO graph repository through the API layer."""

    def __init__(self, session):
        """Create a graph repository for the request session."""
        super().__init__(session)