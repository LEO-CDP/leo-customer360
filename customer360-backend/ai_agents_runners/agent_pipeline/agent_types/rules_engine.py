"""Rules-engine pipeline and result contract."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import StrictBool, StrictStr

from ..contracts import AgentPipelineInput, AgentResultModel, ModelType
from .base import AgentTypePipeline


CHANNEL_CONSENT_KEYS = {
    "email": ("email_opt_in", "EMAIL"),
    "sms": ("sms_opt_in", "SMS"),
    "push": ("push_opt_in", "PUSH"),
    "zalo": ("zalo_opt_in", "ZALO"),
    "whatsapp": ("whatsapp_opt_in", "WHATSAPP"),
    "voice": ("voice_opt_in", "VOICE"),
    "meta_ads": ("meta_ads_opt_in", None),
    "google_ads": ("google_ads_opt_in", None),
    "tiktok_ads": ("tiktok_ads_opt_in", None),
}


class ChannelEligibility(AgentResultModel):
    eligible: StrictBool
    reason_codes: list[StrictStr]


class RulesEngineResult(AgentResultModel):
    decision: Literal["allow", "deny", "review"]
    eligible_channels: list[StrictStr]
    not_ready_channels: list[StrictStr]
    channels: dict[str, ChannelEligibility]
    decision_trace: list[dict[str, Any]]


class RulesEnginePipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "rules_engine"
    result_model = RulesEngineResult

    def process(self, payload: AgentPipelineInput) -> dict[str, Any]:
        """Evaluate explicit channel consent and resolved suppression state."""
        preferences = payload.input_data.get("communication_preferences")
        preferences = preferences if isinstance(preferences, dict) else {}
        raw_suppressions = payload.input_data.get("active_suppression_channels")
        if raw_suppressions is None:
            suppression_state_available = False
            suppressed_channels: set[str] = set()
        elif isinstance(raw_suppressions, list) and all(
            isinstance(channel, str) for channel in raw_suppressions
        ):
            suppression_state_available = True
            suppressed_channels = {channel.strip().upper() for channel in raw_suppressions}
        else:
            raise ValueError(
                "input_data.active_suppression_channels must be a list of strings"
            )

        if any(not channel for channel in suppressed_channels):
            raise ValueError(
                "input_data.active_suppression_channels must not contain blank values"
            )
        known_suppression_channels = {
            "EMAIL",
            "SMS",
            "PUSH",
            "WHATSAPP",
            "ZALO",
            "VOICE",
            "GLOBAL",
        }
        unknown_suppression_channels = suppressed_channels - known_suppression_channels
        if unknown_suppression_channels:
            raise ValueError(
                "input_data.active_suppression_channels contains unsupported values: "
                + ", ".join(sorted(unknown_suppression_channels))
            )

        eligible_channels: list[str] = []
        not_ready_channels: list[str] = []
        channels: dict[str, dict[str, Any]] = {}
        decision_trace: list[dict[str, Any]] = []

        for channel, (consent_key, suppression_channel) in CHANNEL_CONSENT_KEYS.items():
            reason_codes: list[str] = []
            ad_audience_suppressed = (
                channel in {"meta_ads", "google_ads", "tiktok_ads"}
                and bool(suppressed_channels)
            )
            if not suppression_state_available:
                reason_codes.append("suppression_state_missing")
            elif "GLOBAL" in suppressed_channels or (
                suppression_channel is not None
                and suppression_channel in suppressed_channels
            ) or ad_audience_suppressed:
                reason_codes.append("active_suppression")

            consent_value = preferences.get(consent_key)
            if consent_value is not True:
                reason_codes.append(
                    "consent_missing"
                    if consent_key not in preferences
                    else "consent_not_granted"
                    if consent_value is False
                    else "consent_invalid"
                )
            elif payload.input_data.get(f"profile_{consent_key}") is not True:
                reason_codes.append("consent_feature_missing_or_mismatch")

            eligible = not reason_codes
            if eligible:
                eligible_channels.append(channel)
            else:
                not_ready_channels.append(channel)

            channels[channel] = {
                "eligible": eligible,
                "reason_codes": reason_codes,
            }
            decision_trace.append(
                {
                    "channel": channel,
                    "eligible": eligible,
                    "consent_key": consent_key,
                    "reason_codes": reason_codes,
                }
            )

        if len(eligible_channels) == len(CHANNEL_CONSENT_KEYS):
            decision = "allow"
        elif not eligible_channels:
            decision = "deny"
        else:
            decision = "review"

        return {
            "decision": decision,
            "eligible_channels": eligible_channels,
            "not_ready_channels": not_ready_channels,
            "channels": channels,
            "decision_trace": decision_trace,
        }
