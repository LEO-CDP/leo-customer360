import uuid

from campaign_activation.workflow_runner import (
    Connector,
    EmailWorkflowGateway,
    NotificationWorkflowGateway,
    WebhookWorkflowGateway,
)
from leo_customer360_dao.workflow.models import OutboundMessage


class FakeLedger:
    def __init__(self):
        self.started = []
        self.finished = []

    def begin(self, profile_id, node_id, action_type, channel):
        self.started.append((profile_id, node_id, action_type, channel))
        return True

    def finish(self, profile_id, node_id, status, **kwargs):
        self.finished.append((profile_id, node_id, status, kwargs))


def _message(channel="email"):
    return OutboundMessage(
        channel=channel,
        profile_id=uuid.uuid4(),
        to="person@example.com",
        node_id="n2",
        at_hours=0,
        subject="Hello",
        body="Welcome",
    )


def test_email_workflow_gateway_mock_records_success():
    ledger = FakeLedger()
    gateway = EmailWorkflowGateway(ledger, Connector("mock", {}, {}))

    result = gateway.send(_message())

    assert result.status == "sent"
    assert result.provider_id.startswith("workflow-email-mock-")
    assert ledger.started[0][1:] == ("n2", "email", "email")
    assert ledger.finished[0][2] == "Sent"


def test_sms_workflow_gateway_mock_records_success_without_network():
    ledger = FakeLedger()
    gateway = WebhookWorkflowGateway(ledger, "notification", Connector("mock", {}, {}), {})

    result = gateway.send(_message("notification"))

    assert result.status == "sent"
    assert result.provider_id.startswith("workflow-notification-mock-")
    assert ledger.finished[0][2] == "Sent"


def test_notification_gateway_preserves_provider_in_ledger():
    ledger = FakeLedger()
    gateway = NotificationWorkflowGateway(
        ledger,
        {"zalo_oa": Connector("mock", {}, {})},
        {},
    )
    message = _message("notification").model_copy(update={"provider": "zalo_oa"})

    result = gateway.send(message)

    assert result.status == "sent"
    assert result.provider_id.startswith("workflow-zalo_oa-mock-")
    assert ledger.started[0][2:] == ("notification", "notification")
