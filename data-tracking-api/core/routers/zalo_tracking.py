"""Zalo OA (ZNS) engagement webhook -- S3-first, built from the shared factory.

Zalo posts delivery/seen/click/failure/opt-out callbacks; each is recorded to the
S3 event lake (opt-out carries a suppression_reason for the downstream consent
projection). Signature verification is supplied by the deployment's webhook
security provider; this tracking process does not read tenant configuration.

⚠️ Confirm Zalo's signature scheme + event_name values against current Zalo OA docs.
"""

from core.routers.channel_webhook import make_webhook_router

ZNS_EVENT_TO_NAME = {
    "delivered": "zalo-delivered",
    "user_received_message": "zalo-delivered",
    "user_seen_message": "zalo-seen",
    "user_click_message": "zalo-clicked",
    "user_click": "zalo-clicked",
    "failed": "zalo-failed",
    "user_unfollow": "zalo-opt-out",
}
ZNS_SUPPRESSION_EVENTS = {
    "zalo-opt-out": "opt_out",
    "zalo-failed": "permanent_failure",
}

router = make_webhook_router(
    prefix="/track/zalo",
    secret_attr="email_webhook_signing_secret",
    event_map=ZNS_EVENT_TO_NAME,
    suppress_reasons=ZNS_SUPPRESSION_EVENTS,
    channel="zalo",
    sig_header="X-ZEvent-Signature",
    id_field="msg_id",
)

all_zalo_tracking_routers = [router]
