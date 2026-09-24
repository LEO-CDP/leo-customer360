"""API-layer facade for S3 event-lake queries."""

from leo_customer360_dao.repositories.event_query_repository import (
    EventDataSourceError,
    EventQueryError,
    EventQueryRepository as DaoEventQueryRepository,
)


class EventsS3Repository(DaoEventQueryRepository):
    """Expose event-lake queries through the API layer."""

    def __init__(self, settings, s3_client=None):
        """Create an event repository with application settings and optional S3 client."""
        super().__init__(settings, s3_client=s3_client)


EventQueryRepository = EventsS3Repository