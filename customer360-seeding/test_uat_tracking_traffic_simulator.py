from datetime import datetime, timezone

from uat_tracking_traffic_simulator import (
	TRAFFIC_SOURCES,
	UatTrafficConfig,
	WebTrafficGenerator,
)


def test_traffic_sources_cover_requested_channels_and_attribution_types():
	sources = {source.utm_source for source in TRAFFIC_SOURCES}
	traffic_types = {source.traffic_type for source in TRAFFIC_SOURCES}

	assert {
		"google",
		"facebook",
		"linkedin",
		"tiktok",
		"instagram",
		"workshop_qr",
		"youtube",
	}.issubset(sources)
	assert {"organic", "paid", "offline"}.issubset(traffic_types)
	assert all(source.is_paid == (source.traffic_type == "paid") for source in TRAFFIC_SOURCES)


def test_seeded_buyer_journey_contains_attribution_profile_and_purchase_metrics():
	config = UatTrafficConfig(
		sessions=1,
		min_events=8,
		max_events=8,
		seed=7,
	)
	session = WebTrafficGenerator(
		config,
		clock=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
	).generate()[0]
	events = session.payload["events"]
	event_names = [event["event_name"] for event in events]

	assert event_names[0] == "page-view"
	assert "search" in event_names
	assert event_names.index("user-login") < event_names.index("purchase")
	assert event_names.index("add_to_cart") < event_names.index("purchase")
	assert all(event["event_type"] for event in events)
	assert all(event["metrics"] for event in events)
	assert events[0]["utm_source"]
	assert events[0]["event_data"]["utm"]["utm_campaign"] == events[0]["utm_campaign"]
	assert events[0]["traffic_type"] in {"organic", "paid", "direct", "offline"}
	assert events[0]["event_data"]["is_paid"] == events[0]["is_paid"]

	login_event = next(event for event in events if event["event_name"] == "user-login")
	assert login_event["profile_data"] == {
		"user_id": login_event["user_id"],
		"name": "Bao Vo",
		"full_name": "Bao Vo",
		"email": "bao.vo.1@example.test",
		"gender": "male",
	}

	purchase_event = next(event for event in events if event["event_name"] == "purchase")
	assert purchase_event["event_category"] == "COMMERCE"
	assert purchase_event["is_conversion"] is True
	assert purchase_event["currency"] == "VND"
	assert purchase_event["transaction_id"].startswith("order-")
	assert purchase_event["transaction_value"] > 0