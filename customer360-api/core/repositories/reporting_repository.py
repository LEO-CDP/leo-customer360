"""API-layer facade for CIR reporting queries."""

from leo_customer360_dao.repositories.reporting_respository import ReportingRepository as DaoReportingRepository


class ReportingRepository(DaoReportingRepository):
    """Expose the DAO reporting repository through the API layer."""

    def __init__(self, session):
        """Create a reporting repository for the request session."""
        super().__init__(session)