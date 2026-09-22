"""
SQLAlchemy ORM models for the leo_ads schema.
"""

# Imported so every model is always registered on Base.metadata: models in
# this package reference each other (e.g. "leo_ads.tenant", "leo_ads.campaign")
# via string-based ForeignKey, which SQLAlchemy can only resolve once the
# referenced class has been imported somewhere.
from model.tenant import Tenant  # noqa: F401
from model.campaign import Campaign  # noqa: F401
from model.creative import Creative, CreativeRender, Destination  # noqa: F401
from model.placement import Placement, PlacementFormat  # noqa: F401
from model.ad import Ad  # noqa: F401
