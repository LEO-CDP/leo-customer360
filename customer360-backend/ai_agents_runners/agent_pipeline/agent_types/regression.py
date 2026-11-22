"""Regression pipeline and result contract.

The ``clv_prediction`` agent scores one profile per payload with the tenant's
champion CLV model, tiers it on the frozen population cut-offs and projects it
onto the master profile. Training and full-population ranking run in the
tenant-wide CLV Dagster jobs, not here.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import FiniteFloat

from ..contracts import AgentPipelineInput, AgentResultModel, ModelType
from .base import AgentTypePipeline

CLV_AGENT_CODE = "clv_prediction"


class RegressionResult(AgentResultModel):
    value: FiniteFloat | None
    valuation_id: UUID | None = None
    clv_segment: str | None = None
    clv_percentile: FiniteFloat | None = None
    calculation_status: Literal["COMPUTED", "NO_CHAMPION", "PROFILE_NOT_FOUND"] | None = None


def _default_scorer() -> Any:
    from ...clv.service import ClvService

    return ClvService()


class RegressionPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "regression"
    result_model = RegressionResult

    def __init__(self, scorer_factory: Callable[[], Any] = _default_scorer):
        self._scorer_factory = scorer_factory

    @staticmethod
    def _profile_id(payload: AgentPipelineInput) -> str:
        if payload.agent_code != CLV_AGENT_CODE:
            raise NotImplementedError(
                f"The regression agent pipeline has no handler for agent "
                f"'{payload.agent_code}'; only '{CLV_AGENT_CODE}' is implemented."
            )
        try:
            return str(UUID(str(payload.input_data.get("master_profile_id"))))
        except (TypeError, ValueError) as exc:
            raise ValueError("input_data.master_profile_id must be a UUID") from exc

    def process(self, payload: AgentPipelineInput) -> dict[str, Any]:
        return self.process_batch([payload])[0]

    def process_batch(self, payloads: Sequence[AgentPipelineInput]) -> list[dict[str, Any]]:
        """Score payloads grouped by tenant so each tenant runs one scoring call."""
        profile_ids = [self._profile_id(payload) for payload in payloads]
        scorer = self._scorer_factory()
        by_tenant: dict[str, list[int]] = {}
        for index, payload in enumerate(payloads):
            by_tenant.setdefault(str(payload.tenant_id), []).append(index)
        results: list[dict[str, Any]] = [{} for _ in payloads]
        for tenant_id, indexes in by_tenant.items():
            scored = scorer.score_profiles(
                tenant_id,
                [profile_ids[index] for index in indexes],
                configuration=payloads[indexes[0]].configuration,
            )
            for index in indexes:
                results[index] = scored[profile_ids[index]].as_result()
        return results
