"""Unit tests for event schema contracts."""

import unittest
import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from core.schemas.events import EventRead


class EventSchemaTests(unittest.TestCase):
    def test_event_read_requires_raw_profile_id(self):
        with self.assertRaises(ValidationError):
            EventRead(
                event_id=uuid.uuid4(),
                event_time=datetime.now(timezone.utc),
                tenant_id=uuid.uuid4(),
                domain="retail",
                master_profile_id=None,
                # raw_profile_id intentionally omitted
                source_system="WebTracking",
                event_category="GENERAL",
                event_name="page_view",
                is_conversion=False,
            )

    def test_event_read_accepts_required_minimum_fields_with_raw_profile_id(self):
        obj = EventRead(
            event_id=uuid.uuid4(),
            event_time=datetime.now(timezone.utc),
            tenant_id=uuid.uuid4(),
            domain="retail",
            master_profile_id=None,
            raw_profile_id=uuid.uuid4(),
            source_system="WebTracking",
            event_category="GENERAL",
            event_name="page_view",
            is_conversion=False,
        )
        self.assertIsNotNone(obj.raw_profile_id)


if __name__ == "__main__":
    unittest.main()
