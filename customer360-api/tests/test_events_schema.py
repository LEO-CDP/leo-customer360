"""Unit tests for event schema contracts."""

import unittest
import uuid
from datetime import datetime, timezone

from pydantic import ValidationError

from core.schemas.events import EventRead


class EventSchemaTests(unittest.TestCase):
    # Core contract tests for EventRead: required linkages, optional fields,
    # and basic type-validation behavior.

    def test_event_read_requires_raw_profile_id(self):
        with self.assertRaises(ValidationError):
            EventRead(
                event_id=uuid.uuid4(),
                event_time=datetime.now(timezone.utc),
                tenant_id=uuid.uuid4(),
                domain="retail",
                master_profile_id=None,
                raw_profile_id=None, # type: ignore
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

    def test_event_read_accepts_optional_master_profile_id(self):
        raw_id = uuid.uuid4()
        master_id = uuid.uuid4()
        obj = EventRead(
            event_id=uuid.uuid4(),
            event_time=datetime.now(timezone.utc),
            tenant_id=uuid.uuid4(),
            domain="retail",
            master_profile_id=master_id,
            raw_profile_id=raw_id,
            source_system="WebTracking",
            event_category="GENERAL",
            event_name="purchase",
            is_conversion=True,
        )
        self.assertEqual(obj.raw_profile_id, raw_id)
        self.assertEqual(obj.master_profile_id, master_id)

    def test_event_read_rejects_invalid_event_id_type(self):
        with self.assertRaises(ValidationError):
            EventRead(
                event_id="not-a-uuid", # type: ignore
                event_time=datetime.now(timezone.utc),
                tenant_id=uuid.uuid4(),
                domain="retail",
                raw_profile_id=uuid.uuid4(),
                source_system="WebTracking",
                event_category="GENERAL",
                event_name="page_view",
                is_conversion=False,
            )


if __name__ == "__main__":
    unittest.main()
