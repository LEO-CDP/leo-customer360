
from datetime import datetime, timedelta, timezone
from typing import Optional

def cutoff_for_days(days: Optional[int]) -> Optional[datetime]:
    """Return a UTC cutoff for a recent-only filter, or None when unset."""
    if days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=days)

