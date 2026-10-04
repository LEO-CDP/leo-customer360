from datetime import datetime, timezone

from ai_agents_runners.runner import AgentWorkflowMasterTask


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params):
        self.executed = (query, params)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self, **_):
        return self.cursor_instance


def test_api_trigger_selects_active_steps_in_database_order():
    rows = [
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "segment_id": "22222222-2222-2222-2222-222222222222",
            "agent_code": "first",
            "model_type": "classification",
            "execution_order": 1,
            "configuration": {},
            "candidate_content_item_ids": [],
            "effective_schedule": None,
        }
    ]
    connection = FakeConnection(rows)
    summary = AgentWorkflowMasterTask(lambda: connection).run(
        trigger="api",
        tenant_id=rows[0]["tenant_id"],
        segment_id=rows[0]["segment_id"],
        trigger_event={"event_name": "profile.updated"},
    )
    assert summary.selected_steps == 1
    assert summary.plan[0]["trigger_event"]["event_name"] == "profile.updated"
    assert connection.cursor_instance.executed[1] == [
        rows[0]["tenant_id"],
        rows[0]["segment_id"],
    ]


def test_cron_trigger_skips_steps_that_are_not_due():
    rows = [
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "segment_id": "22222222-2222-2222-2222-222222222222",
            "agent_code": "daily",
            "model_type": "classification",
            "execution_order": 1,
            "configuration": {},
            "candidate_content_item_ids": [],
            "effective_schedule": "0 2 * * *",
        }
    ]
    summary = AgentWorkflowMasterTask(lambda: FakeConnection(rows)).run(
        trigger="cron",
        scheduled_at=datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc),
    )
    assert summary.selected_steps == 0
    assert summary.skipped_steps == 1
