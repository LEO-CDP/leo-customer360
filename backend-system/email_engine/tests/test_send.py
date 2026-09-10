"""Eligibility-routing + idempotency tests for the send pipeline, driven by a
mocked psycopg2 cursor (no real PostgreSQL). The SQL itself is exercised
manually via scripts/seed_email_campaign.py against a real DB."""

from unittest.mock import MagicMock

from email_engine.adapters import DispatchResult
from email_engine.send import _display_name, _is_opted_out, _process_batch


def test_is_opted_out_only_on_explicit_false():
    assert _is_opted_out({"communication_preferences": {"email_opt_in": False}}) is True
    assert _is_opted_out({"communication_preferences": {"email_opt_in": True}}) is False
    assert _is_opted_out({"communication_preferences": {}}) is False
    assert _is_opted_out({}) is False


def test_display_name_joins_first_last():
    assert _display_name({"first_name": "Ada", "last_name": "Lovelace"}) == "Ada Lovelace"
    assert _display_name({"first_name": "Ada"}) == "Ada"
    assert _display_name({}) == ""


def _summary():
    s = {"total": 0, "sent": 0, "failed": 0, "skipped": 0, "suppressed": 0, "already_sent": 0}
    return s


def test_process_batch_routes_each_recipient_by_eligibility(mock_conn, mock_cursor):
    # Suppression list contains supp@x.io; per-recipient current-status:
    #   r1 -> new, r2 -> new, r3 -> new, r4 -> already 'Sent'.
    mock_cursor.fetchall.return_value = [{"email": "supp@x.io"}]
    mock_cursor.fetchone.side_effect = [None, None, None, {"status": "Sent"}]

    adapter = MagicMock()
    adapter.provider_name = "mock"
    adapter.send.return_value = DispatchResult(ok=True, provider_message_id="m1")

    template = {"subject": "Hi {{ first_name }}", "html_body": "<p>{{ first_name }}</p>", "text_body": "hi"}
    batch = [
        {"master_profile_id": "p1", "email": "ok@x.io", "first_name": "A", "communication_preferences": {}},
        {"master_profile_id": "p2", "email": None, "first_name": "B", "communication_preferences": {}},
        {"master_profile_id": "p3", "email": "supp@x.io", "first_name": "C", "communication_preferences": {}},
        {"master_profile_id": "p4", "email": "done@x.io", "first_name": "D", "communication_preferences": {}},
    ]
    summary = _summary()

    _process_batch(mock_conn, adapter, "t1", "c1", "tpl1", template, batch, "run1", summary)

    assert summary["sent"] == 1        # p1
    assert summary["skipped"] == 1     # p2 (no email)
    assert summary["suppressed"] == 1  # p3 (on suppression list)
    assert summary["already_sent"] == 1  # p4 (already Sent -> not re-sent)
    assert adapter.send.call_count == 1  # only the one eligible recipient was dispatched


def test_opted_out_recipient_is_skipped_not_sent(mock_conn, mock_cursor):
    mock_cursor.fetchall.return_value = []
    mock_cursor.fetchone.side_effect = [None]
    adapter = MagicMock()
    adapter.provider_name = "mock"

    batch = [{"master_profile_id": "p1", "email": "x@x.io", "first_name": "X",
              "communication_preferences": {"email_opt_in": False}}]
    summary = _summary()

    _process_batch(mock_conn, adapter, "t1", "c1", "tpl1", template={"subject": "s"}, batch=batch,
                   run_id="r", summary=summary)

    assert summary["skipped"] == 1
    assert adapter.send.call_count == 0
