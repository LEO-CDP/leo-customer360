"""API-layer facade for personalized content persistence."""

from leo_customer360_dao.repositories.content_repository import ContentRepository as DaoContentRepository


class ContentRepository(DaoContentRepository):
    """Expose the DAO content repository through the API layer."""

    def __init__(self, session):
        """Create a content repository for the request session."""
        super().__init__(session)