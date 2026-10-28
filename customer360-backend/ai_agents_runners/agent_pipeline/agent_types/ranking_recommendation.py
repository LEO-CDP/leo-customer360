"""Tenant-scoped content ranking pipeline, result contract, and state observability."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import json
import logging
import math
import time
from typing import Any, ClassVar, Literal
from uuid import UUID

from psycopg2.extras import RealDictCursor
from pydantic import Field, FiniteFloat, model_validator

from leo_customer360_dao.schemas.agent_workflow import MAX_CANDIDATE_CONTENT_ITEMS

from ...runner import (
    DB_SCHEMA,
    MAX_RANKING_PROFILES_PER_BATCH,
    SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS,
    SUPPORTED_RECOMMENDATION_EMBEDDING_PROVIDERS,
    _connect,
)
from ..contracts import (
    AgentPipelineInput,
    AgentResultModel,
    ModelType,
)
from .base import AgentTypePipeline

DEFAULT_RESULT_LIMIT = 8
MAX_TOP_K = 100
DEFAULT_SEMANTIC_WEIGHT = 0.7
DEFAULT_TAG_WEIGHT = 0.3
DEFAULT_MINIMUM_SCORE = 0.0

RankingStrategy = Literal["tags", "semantic", "hybrid"]
RankingReason = Literal[
    "semantic_and_segment_tag_match",
    "semantic_similarity",
    "segment_tag_overlap",
    "no_segment_tag_overlap",
]
RankingPipelinePhase = Literal["short_circuited", "completed", "failed"]
ProfileOutcomeStatus = Literal[
    "ranked",
    "skipped_domain_mismatch",
    "short_circuited",
    "failed",
]

logger = logging.getLogger(__name__)
CANDIDATE_DIAGNOSTICS_SQL = f"""
    SELECT
        COUNT(*) AS tenant_candidates,
        COUNT(*) FILTER (WHERE status_code = 1) AS active_candidates,
        COUNT(*) FILTER (
            WHERE status_code = 1 AND (domain = 'all' OR domain = %s)
        ) AS domain_candidates,
        COUNT(*) FILTER (
            WHERE status_code = 1 AND (domain = 'all' OR domain = %s)
              AND (
                  %s = 'tags'
                  OR (
                      embedding IS NOT NULL
                      AND vector_dims(embedding) = %s
                      AND embedding_model = %s
                  )
              )
        ) AS rankable_candidates
    FROM {DB_SCHEMA}.cdp_content_items
    WHERE tenant_id = %s AND content_item_id = ANY(%s::uuid[])
"""
RANK_CANDIDATE_CONTENT_SQL = f"""
    WITH criteria AS (
        SELECT
            %s::text AS strategy,
            %s::double precision AS semantic_weight,
            %s::double precision AS tag_weight,
            %s::text AS embedding_model,
            %s::double precision AS minimum_score,
            %s::uuid AS tenant_id,
            %s::uuid[] AS candidate_ids,
            %s::integer AS top_k
    ),
    profile_inputs AS (
        SELECT
            profile_index,
            domain,
            COALESCE(segmentation_tags, ARRAY[]::text[]) AS profile_tags,
            profile_embedding::vector(__VECTOR_DIMENSIONS__) AS profile_embedding
        FROM jsonb_to_recordset(%s::jsonb) AS profile_input(
            profile_index integer,
            domain text,
            segmentation_tags text[],
            profile_embedding text
        )
    ),
    candidate_scores AS (
        SELECT
            profile_inputs.profile_index,
            content.content_item_id,
            content.item_type,
            content.title,
            content.summary,
            content.image_url,
            content.cta_label,
            content.cta_url,
            content.published_at,
            overlap.matched_tags,
            CASE
                WHEN cardinality(profile_inputs.profile_tags) = 0 THEN 0.0
                ELSE cardinality(overlap.matched_tags)::double precision
                    / cardinality(profile_inputs.profile_tags)
            END AS tag_score,
            CASE
                WHEN criteria.strategy = 'tags' THEN 0.0
                ELSE GREATEST(
                    0.0, LEAST(
                        1.0,
                        1.0 - (
                            content.embedding::vector(__VECTOR_DIMENSIONS__)
                            <=> profile_inputs.profile_embedding
                        ) / 2.0
                    )
                )
            END AS semantic_score,
            profile_inputs.profile_tags,
            criteria.*
        FROM {DB_SCHEMA}.cdp_content_items AS content
        CROSS JOIN criteria
        JOIN profile_inputs
          ON content.domain = 'all' OR content.domain = profile_inputs.domain
        CROSS JOIN LATERAL (
            SELECT COALESCE(array_agg(tag ORDER BY tag), ARRAY[]::text[]) AS matched_tags
            FROM (
                SELECT DISTINCT candidate_tag AS tag
                FROM unnest(COALESCE(content.segment_tags, ARRAY[]::text[]))
                    AS candidate_tags(candidate_tag)
                WHERE candidate_tag = ANY(profile_inputs.profile_tags)
            ) AS matching_tags
        ) AS overlap
        WHERE content.tenant_id = criteria.tenant_id
          AND content.status_code = 1
          AND content.content_item_id = ANY(criteria.candidate_ids)
          AND (
                criteria.strategy = 'tags'
                OR (
                    content.embedding IS NOT NULL
                    AND vector_dims(content.embedding) = __VECTOR_DIMENSIONS__
                    AND content.embedding_model = criteria.embedding_model
                )
          )
    ),
    scored AS (
        SELECT
            candidate_scores.*,
            CASE strategy
                WHEN 'tags' THEN tag_score
                WHEN 'semantic' THEN semantic_score
                WHEN 'hybrid' THEN
                    CASE
                        WHEN cardinality(profile_tags) = 0 THEN
                            CASE WHEN semantic_weight > 0 THEN semantic_score ELSE 0.0 END
                        ELSE (
                            semantic_score * semantic_weight + tag_score * tag_weight
                        ) / NULLIF(semantic_weight + tag_weight, 0)
                    END
            END AS score
        FROM candidate_scores
    ),
    ranked AS (
        SELECT
            scored.*,
            ROW_NUMBER() OVER (
                PARTITION BY profile_index
                ORDER BY score DESC, published_at DESC NULLS LAST, content_item_id
            ) AS rank
        FROM scored
        WHERE score >= minimum_score
    )
    SELECT
        profile_index,
        content_item_id,
        item_type,
        title,
        summary,
        image_url,
        cta_label,
        cta_url,
        published_at,
        matched_tags,
        tag_score,
        semantic_score,
        score,
        strategy,
        rank
    FROM ranked
    WHERE rank <= top_k
    ORDER BY profile_index, rank
"""


@dataclass(frozen=True)
class _ProfileRankingInput:
    profile_index: int
    domain: str
    segmentation_tags: list[str]
    profile_embedding: list[float] | None
    embedding_model: str
    profile_id: str | None


@dataclass(frozen=True)
class _RankingSettings:
    strategy: RankingStrategy
    top_k: int
    semantic_weight: float
    tag_weight: float
    minimum_score: float
    embedding_model: str
    dimensions: int


@dataclass(frozen=True)
class CandidateDiagnostics(Mapping[str, int]):
    """Filter-stage candidate counts used to diagnose empty profile rankings."""

    tenant_candidates: int
    active_candidates: int
    domain_candidates: int
    rankable_candidates: int

    @classmethod
    def from_mapping(cls, counts: Mapping[str, Any]) -> CandidateDiagnostics:
        if isinstance(counts, cls):
            return counts
        return cls(
            tenant_candidates=int(counts["tenant_candidates"]),
            active_candidates=int(counts["active_candidates"]),
            domain_candidates=int(counts["domain_candidates"]),
            rankable_candidates=int(counts["rankable_candidates"]),
        )

    @property
    def is_domain_mismatch_only(self) -> bool:
        """Return True when tenant/active candidates exist but none match the profile domain."""
        return (
            self.tenant_candidates > 0
            and self.active_candidates > 0
            and self.domain_candidates == 0
        )

    def failure_reason(self) -> str:
        """Identify the earliest filter stage that excluded all selected candidates."""
        if self.tenant_candidates == 0:
            return "selected candidates are unavailable for this tenant"
        if self.active_candidates == 0:
            return "selected candidates are inactive"
        if self.domain_candidates == 0:
            return "selected candidates do not match the profile domain"
        if self.rankable_candidates == 0:
            return "selected candidates lack embeddings for the configured model"
        return "no candidate score meets minimum_score"

    def as_dict(self) -> dict[str, int]:
        return {
            "tenant_candidates": self.tenant_candidates,
            "active_candidates": self.active_candidates,
            "domain_candidates": self.domain_candidates,
            "rankable_candidates": self.rankable_candidates,
        }

    def __getitem__(self, key: str) -> int:
        return self.as_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.as_dict())

    def __len__(self) -> int:
        return 4


@dataclass(frozen=True)
class ProfileRankingOutcome:
    """Observable per-profile ranking outcome within a batch execution."""

    profile_index: int
    profile_id: str | None
    domain: str
    tag_count: int
    status: ProfileOutcomeStatus
    ranked_count: int
    top_score: float | None = None
    reasons: tuple[str, ...] = ()
    diagnostics: CandidateDiagnostics | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile_index": self.profile_index,
            "profile_id": self.profile_id,
            "domain": self.domain,
            "tag_count": self.tag_count,
            "status": self.status,
            "ranked_count": self.ranked_count,
            "top_score": self.top_score,
            "reasons": list(self.reasons),
            "diagnostics": (
                self.diagnostics.as_dict() if self.diagnostics is not None else None
            ),
        }


@dataclass(frozen=True)
class RankingExecutionState:
    """Structured state observability snapshot for a ranking pipeline batch run."""

    phase: RankingPipelinePhase
    tenant_id: UUID
    segment_id: UUID
    agent_code: str
    strategy: RankingStrategy
    top_k: int
    semantic_weight: float
    tag_weight: float
    minimum_score: float
    embedding_model: str
    dimensions: int
    candidate_count: int
    profile_count: int
    sql_rows_returned: int
    profiles_ranked_count: int
    profiles_skipped_domain_count: int
    total_ranked_items: int
    reason_counts: dict[str, int]
    domain_counts: dict[str, int]
    profile_outcomes: tuple[ProfileRankingOutcome, ...]
    duration_ms: float
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "tenant_id": str(self.tenant_id),
            "segment_id": str(self.segment_id),
            "agent_code": self.agent_code,
            "strategy": self.strategy,
            "top_k": self.top_k,
            "semantic_weight": self.semantic_weight,
            "tag_weight": self.tag_weight,
            "minimum_score": self.minimum_score,
            "embedding_model": self.embedding_model,
            "dimensions": self.dimensions,
            "candidate_count": self.candidate_count,
            "profile_count": self.profile_count,
            "sql_rows_returned": self.sql_rows_returned,
            "profiles_ranked_count": self.profiles_ranked_count,
            "profiles_skipped_domain_count": self.profiles_skipped_domain_count,
            "total_ranked_items": self.total_ranked_items,
            "reason_counts": dict(self.reason_counts),
            "domain_counts": dict(self.domain_counts),
            "profile_outcomes": [
                outcome.as_dict() for outcome in self.profile_outcomes
            ],
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class RankedContentItem(AgentResultModel):
    item_id: UUID
    rank: int = Field(gt=0, strict=True)
    score: FiniteFloat = Field(ge=0, le=1)
    strategy: RankingStrategy = "tags"
    semantic_score: FiniteFloat | None = Field(default=None, ge=0, le=1)
    tag_score: FiniteFloat = Field(ge=0, le=1)
    matched_tags: list[str]
    reason: str
    item_type: Literal["news", "video", "product", "article"]
    title: str
    summary: str | None
    image_url: str | None
    cta_label: str | None
    cta_url: str | None
    published_at: str | None


class RankingRecommendationResult(AgentResultModel):
    ranked_items: list[RankedContentItem]

    @model_validator(mode="after")
    def validate_unique_items_and_ranks(self) -> RankingRecommendationResult:
        item_ids = [item.item_id for item in self.ranked_items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("ranked_items must not contain duplicate item_id values")
        ranks = [item.rank for item in self.ranked_items]
        if len(ranks) != len(set(ranks)):
            raise ValueError("ranked_items must not contain duplicate rank values")
        return self


class RankingRecommendationPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "ranking_recommendation"
    result_model = RankingRecommendationResult

    def __init__(
        self,
        connection_factory: Callable[[], Any] = _connect,
        *,
        state_observer: Callable[[RankingExecutionState], None] | None = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._state_observer = state_observer
        self._last_state: RankingExecutionState | None = None

    @property
    def last_state(self) -> RankingExecutionState | None:
        """Return the state snapshot of the most recent pipeline execution."""
        return self._last_state

    def _record_state(self, state: RankingExecutionState) -> None:
        self._last_state = state
        if self._state_observer is not None:
            self._state_observer(state)

    def process(self, payload: AgentPipelineInput) -> dict[str, Any]:
        """Rank the selected candidates for a single profile."""
        return self.process_batch([payload])[0]

    def process_batch(
        self, payloads: Sequence[AgentPipelineInput]
    ) -> list[dict[str, Any]]:
        """Rank a shared candidate set for a bounded batch of profiles."""
        if not payloads:
            return []

        started_at = time.perf_counter()
        first, candidate_ids = self._validate_batch_payloads(payloads)
        if not candidate_ids:
            self._record_short_circuited_state(first, payloads, started_at)
            return [{"ranked_items": []} for _ in payloads]

        strategy, top_k, semantic_weight, tag_weight, minimum_score = (
            self._ranking_options(first.configuration)
        )
        profiles = self._parse_profile_inputs(payloads, strategy)
        settings = self._build_ranking_settings(
            strategy=strategy,
            top_k=top_k,
            semantic_weight=semantic_weight,
            tag_weight=tag_weight,
            minimum_score=minimum_score,
            profiles=profiles,
        )

        rows = self._load_candidates(
            tenant_id=first.tenant_id,
            candidate_ids=candidate_ids,
            profiles=profiles,
            settings=settings,
        )
        ranked_by_profile = self._group_ranked_candidates(
            rows=rows,
            profile_count=len(profiles),
            default_strategy=settings.strategy,
        )

        self._evaluate_and_record_outcomes(
            first=first,
            candidate_ids=candidate_ids,
            profiles=profiles,
            settings=settings,
            ranked_by_profile=ranked_by_profile,
            sql_rows_returned=len(rows),
            started_at=started_at,
        )
        return [{"ranked_items": items} for items in ranked_by_profile]

    def _validate_batch_payloads(
        self, payloads: Sequence[AgentPipelineInput]
    ) -> tuple[AgentPipelineInput, list[UUID]]:
        if len(payloads) > MAX_RANKING_PROFILES_PER_BATCH:
            raise ValueError(
                "Ranking batches must not exceed "
                f"{MAX_RANKING_PROFILES_PER_BATCH} profiles"
            )

        first = payloads[0]
        candidate_ids = first.candidate_content_item_ids
        if len(candidate_ids) > MAX_CANDIDATE_CONTENT_ITEMS:
            raise ValueError(
                "ranking_recommendation candidate_content_item_ids must not exceed "
                f"{MAX_CANDIDATE_CONTENT_ITEMS}"
            )

        for payload in payloads:
            if payload.model_type != self.model_type:
                raise ValueError(
                    f"{self.__class__.__name__} cannot handle {payload.model_type}"
                )
            if (
                payload.tenant_id != first.tenant_id
                or payload.segment_id != first.segment_id
                or payload.candidate_content_item_ids != candidate_ids
                or payload.configuration != first.configuration
            ):
                raise ValueError(
                    "Ranking batches must share tenant, segment, configuration, "
                    "and candidate content IDs"
                )
        return first, candidate_ids

    @classmethod
    def _parse_profile_inputs(
        cls,
        payloads: Sequence[AgentPipelineInput],
        strategy: RankingStrategy,
    ) -> list[_ProfileRankingInput]:
        profiles: list[_ProfileRankingInput] = []
        for index, payload in enumerate(payloads):
            try:
                profiles.append(
                    cls._profile_input(index, payload.input_data, strategy)
                )
            except ValueError as exc:
                profile_id = payload.input_data.get("master_profile_id")
                profile_context = (
                    f"profile={profile_id}, " if profile_id is not None else ""
                )
                raise ValueError(f"{profile_context}{exc}") from exc
        embedding_models = {profile.embedding_model for profile in profiles}
        if len(embedding_models) != 1:
            raise ValueError("Ranking batches must use one embedding model")
        return profiles

    @staticmethod
    def _build_ranking_settings(
        *,
        strategy: RankingStrategy,
        top_k: int,
        semantic_weight: float,
        tag_weight: float,
        minimum_score: float,
        profiles: Sequence[_ProfileRankingInput],
    ) -> _RankingSettings:
        embedding_model = profiles[0].embedding_model
        dimensions = (
            len(profiles[0].profile_embedding)
            if profiles[0].profile_embedding is not None
            else min(SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS)
        )
        return _RankingSettings(
            strategy=strategy,
            top_k=top_k,
            semantic_weight=semantic_weight,
            tag_weight=tag_weight,
            minimum_score=minimum_score,
            embedding_model=embedding_model,
            dimensions=dimensions,
        )

    @classmethod
    def _group_ranked_candidates(
        cls,
        *,
        rows: Sequence[Mapping[str, Any]],
        profile_count: int,
        default_strategy: RankingStrategy = "tags",
    ) -> list[list[dict[str, Any]]]:
        ranked_by_profile: list[list[dict[str, Any]]] = [
            [] for _ in range(profile_count)
        ]
        for row in rows:
            profile_index = row["profile_index"]
            if (
                isinstance(profile_index, bool)
                or not isinstance(profile_index, int)
                or not 0 <= profile_index < profile_count
            ):
                raise RuntimeError(
                    "Ranking query returned an invalid profile_index"
                )
            raw_rank = row["rank"]
            if isinstance(raw_rank, bool) or not isinstance(raw_rank, int) or raw_rank < 1:
                raise RuntimeError("Ranking query returned an invalid rank")
            ranked_by_profile[profile_index].append(
                cls._serialize_candidate(row, raw_rank, default_strategy=default_strategy)
            )
        return ranked_by_profile

    def _record_short_circuited_state(
        self,
        first: AgentPipelineInput,
        payloads: Sequence[AgentPipelineInput],
        started_at: float,
    ) -> None:
        raw_strategy = first.configuration.get("strategy", "tags")
        strategy: RankingStrategy = (
            raw_strategy
            if raw_strategy in {"tags", "semantic", "hybrid"}
            else "tags"
        )
        outcomes: list[ProfileRankingOutcome] = []
        domain_counts: Counter[str] = Counter()
        for index, payload in enumerate(payloads):
            raw_domain = payload.input_data.get("domain")
            domain = (
                raw_domain.strip()
                if isinstance(raw_domain, str) and raw_domain.strip()
                else "unknown"
            )
            domain_counts[domain] += 1
            raw_tags = payload.input_data.get("segmentation_tags")
            tag_count = len(raw_tags) if isinstance(raw_tags, list) else 0
            raw_profile_id = payload.input_data.get("master_profile_id")
            outcomes.append(
                ProfileRankingOutcome(
                    profile_index=index,
                    profile_id=str(raw_profile_id) if raw_profile_id is not None else None,
                    domain=domain,
                    tag_count=tag_count,
                    status="short_circuited",
                    ranked_count=0,
                )
            )
        duration_ms = round((time.perf_counter() - started_at) * 1000.0, 3)
        state = RankingExecutionState(
            phase="short_circuited",
            tenant_id=first.tenant_id,
            segment_id=first.segment_id,
            agent_code=first.agent_code,
            strategy=strategy,
            top_k=DEFAULT_RESULT_LIMIT,
            semantic_weight=DEFAULT_SEMANTIC_WEIGHT,
            tag_weight=DEFAULT_TAG_WEIGHT,
            minimum_score=DEFAULT_MINIMUM_SCORE,
            embedding_model="",
            dimensions=min(SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS),
            candidate_count=0,
            profile_count=len(payloads),
            sql_rows_returned=0,
            profiles_ranked_count=0,
            profiles_skipped_domain_count=0,
            total_ranked_items=0,
            reason_counts={},
            domain_counts=dict(sorted(domain_counts.items())),
            profile_outcomes=tuple(outcomes),
            duration_ms=duration_ms,
        )
        self._record_state(state)
        logger.debug(
            "Ranking batch short-circuited with empty candidate list "
            "(tenant_id=%s segment_id=%s agent_code=%s profiles=%d duration_ms=%.3f)",
            first.tenant_id,
            first.segment_id,
            first.agent_code,
            len(payloads),
            duration_ms,
        )

    def _evaluate_and_record_outcomes(
        self,
        *,
        first: AgentPipelineInput,
        candidate_ids: Sequence[UUID],
        profiles: Sequence[_ProfileRankingInput],
        settings: _RankingSettings,
        ranked_by_profile: Sequence[list[dict[str, Any]]],
        sql_rows_returned: int,
        started_at: float,
    ) -> None:
        outcomes: list[ProfileRankingOutcome] = []
        reason_counts: Counter[str] = Counter()
        domain_counts = Counter(profile.domain for profile in profiles)
        profiles_ranked_count = 0
        profiles_skipped_domain_count = 0
        total_ranked_items = 0

        for profile, ranked_items in zip(profiles, ranked_by_profile, strict=True):
            if ranked_items:
                profiles_ranked_count += 1
                total_ranked_items += len(ranked_items)
                item_reasons = tuple(str(item["reason"]) for item in ranked_items)
                reason_counts.update(item_reasons)
                outcomes.append(
                    ProfileRankingOutcome(
                        profile_index=profile.profile_index,
                        profile_id=profile.profile_id,
                        domain=profile.domain,
                        tag_count=len(profile.segmentation_tags),
                        status="ranked",
                        ranked_count=len(ranked_items),
                        top_score=float(ranked_items[0]["score"]),
                        reasons=item_reasons,
                    )
                )
                continue

            counts = self._candidate_diagnostics(
                tenant_id=first.tenant_id,
                candidate_ids=candidate_ids,
                profile=profile,
                settings=settings,
            )
            if counts.is_domain_mismatch_only:
                profiles_skipped_domain_count += 1
                outcomes.append(
                    ProfileRankingOutcome(
                        profile_index=profile.profile_index,
                        profile_id=profile.profile_id,
                        domain=profile.domain,
                        tag_count=len(profile.segmentation_tags),
                        status="skipped_domain_mismatch",
                        ranked_count=0,
                        diagnostics=counts,
                    )
                )
                logger.warning(
                    "No selected recommendation candidates match profile domain; "
                    "returning no recommendations "
                    "(tenant_id=%s profile_id=%s domain=%s selected=%s "
                    "tenant=%s active=%s domain_matching=%s candidate_sample=%s)",
                    first.tenant_id,
                    profile.profile_id,
                    profile.domain,
                    len(candidate_ids),
                    counts["tenant_candidates"],
                    counts["active_candidates"],
                    counts["domain_candidates"],
                    candidate_ids[:5],
                )
                continue

            outcomes.append(
                ProfileRankingOutcome(
                    profile_index=profile.profile_index,
                    profile_id=profile.profile_id,
                    domain=profile.domain,
                    tag_count=len(profile.segmentation_tags),
                    status="failed",
                    ranked_count=0,
                    diagnostics=counts,
                )
            )
            logger.error(
                "Ranking produced no candidates for profile "
                "(tenant_id=%s profile_id=%s domain=%s profile_tag_count=%d "
                "strategy=%s minimum_score=%s selected=%d tenant=%d active=%d "
                "domain_matching=%d rankable=%d)",
                first.tenant_id,
                profile.profile_id,
                profile.domain,
                len(profile.segmentation_tags),
                settings.strategy,
                settings.minimum_score,
                len(candidate_ids),
                counts["tenant_candidates"],
                counts["active_candidates"],
                counts["domain_candidates"],
                counts["rankable_candidates"],
            )
            duration_ms = round((time.perf_counter() - started_at) * 1000.0, 3)
            try:
                self._raise_no_candidates(
                    tenant_id=first.tenant_id,
                    candidate_ids=candidate_ids,
                    profile=profile,
                    settings=settings,
                    counts=counts,
                )
            except ValueError as exc:
                self._record_state(
                    RankingExecutionState(
                        phase="failed",
                        tenant_id=first.tenant_id,
                        segment_id=first.segment_id,
                        agent_code=first.agent_code,
                        strategy=settings.strategy,
                        top_k=settings.top_k,
                        semantic_weight=settings.semantic_weight,
                        tag_weight=settings.tag_weight,
                        minimum_score=settings.minimum_score,
                        embedding_model=settings.embedding_model,
                        dimensions=settings.dimensions,
                        candidate_count=len(candidate_ids),
                        profile_count=len(profiles),
                        sql_rows_returned=sql_rows_returned,
                        profiles_ranked_count=profiles_ranked_count,
                        profiles_skipped_domain_count=profiles_skipped_domain_count,
                        total_ranked_items=total_ranked_items,
                        reason_counts=dict(sorted(reason_counts.items())),
                        domain_counts=dict(sorted(domain_counts.items())),
                        profile_outcomes=tuple(outcomes),
                        duration_ms=duration_ms,
                        error=str(exc),
                    )
                )
                raise

        duration_ms = round((time.perf_counter() - started_at) * 1000.0, 3)
        state = RankingExecutionState(
            phase="completed",
            tenant_id=first.tenant_id,
            segment_id=first.segment_id,
            agent_code=first.agent_code,
            strategy=settings.strategy,
            top_k=settings.top_k,
            semantic_weight=settings.semantic_weight,
            tag_weight=settings.tag_weight,
            minimum_score=settings.minimum_score,
            embedding_model=settings.embedding_model,
            dimensions=settings.dimensions,
            candidate_count=len(candidate_ids),
            profile_count=len(profiles),
            sql_rows_returned=sql_rows_returned,
            profiles_ranked_count=profiles_ranked_count,
            profiles_skipped_domain_count=profiles_skipped_domain_count,
            total_ranked_items=total_ranked_items,
            reason_counts=dict(sorted(reason_counts.items())),
            domain_counts=dict(sorted(domain_counts.items())),
            profile_outcomes=tuple(outcomes),
            duration_ms=duration_ms,
        )
        self._record_state(state)
        logger.debug(
            "Ranking batch completed "
            "(tenant_id=%s segment_id=%s agent_code=%s strategy=%s "
            "profiles=%d ranked_profiles=%d domain_skips=%d items=%d "
            "reasons=%s duration_ms=%.3f)",
            first.tenant_id,
            first.segment_id,
            first.agent_code,
            settings.strategy,
            len(profiles),
            profiles_ranked_count,
            profiles_skipped_domain_count,
            total_ranked_items,
            state.reason_counts,
            duration_ms,
        )

    @staticmethod
    def _ranking_options(
        configuration: dict[str, Any],
    ) -> tuple[RankingStrategy, int, float, float, float]:
        strategy_value = configuration.get("strategy", "tags")
        if strategy_value == "tags":
            strategy: RankingStrategy = "tags"
        elif strategy_value == "semantic":
            strategy = "semantic"
        elif strategy_value == "hybrid":
            strategy = "hybrid"
        else:
            raise ValueError("configuration.strategy must be tags, semantic, or hybrid")

        top_k = configuration.get(
            "top_k",
            configuration.get("limit", DEFAULT_RESULT_LIMIT),
        )
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
            raise ValueError(
                "ranking_recommendation configuration.top_k must be a positive integer"
            )
        if top_k > MAX_TOP_K:
            raise ValueError(
                f"ranking_recommendation configuration.top_k must not exceed {MAX_TOP_K}"
            )

        semantic_weight = _weight(
            configuration, "semantic_weight", DEFAULT_SEMANTIC_WEIGHT
        )
        tag_weight = _weight(configuration, "tag_weight", DEFAULT_TAG_WEIGHT)
        if strategy == "hybrid":
            if semantic_weight + tag_weight <= 0:
                raise ValueError(
                    "hybrid semantic_weight and tag_weight must sum to more than zero"
                )
        elif strategy == "semantic":
            semantic_weight, tag_weight = 1.0, 0.0
        else:
            semantic_weight, tag_weight = 0.0, 1.0

        minimum_score = _finite_number(
            configuration.get("minimum_score", DEFAULT_MINIMUM_SCORE)
        )
        if minimum_score is None or not 0 <= minimum_score <= 1:
            raise ValueError("configuration.minimum_score must be between 0 and 1")

        return strategy, top_k, semantic_weight, tag_weight, minimum_score

    @staticmethod
    def _profile_input(
        profile_index: int,
        input_data: dict[str, Any],
        strategy: RankingStrategy,
    ) -> _ProfileRankingInput:
        domain = input_data.get("domain")
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError(
                "ranking_recommendation input_data.domain must be a non-blank string"
            )

        segmentation_tags = input_data.get("segmentation_tags", [])
        if not isinstance(segmentation_tags, list) or any(
            not isinstance(tag, str) or not tag.strip()
            for tag in segmentation_tags
        ):
            raise ValueError(
                "ranking_recommendation input_data.segmentation_tags must be a list "
                "of non-blank strings"
            )

        profile_embedding = input_data.get("profile_embedding")
        model_key = input_data.get("embedding_model")
        if strategy in {"semantic", "hybrid"}:
            if not isinstance(profile_embedding, list) or not profile_embedding:
                raise ValueError(
                    f"{strategy} recommendation requires input_data.profile_embedding"
                )
            if len(profile_embedding) not in SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS:
                raise ValueError(
                    "input_data.profile_embedding must have 384 or 768 dimensions"
                )
            finite_embedding = []
            for value in profile_embedding:
                finite_value = _finite_number(value)
                if finite_value is None:
                    raise ValueError(
                        "input_data.profile_embedding must contain finite numbers"
                    )
                finite_embedding.append(finite_value)
            if not isinstance(model_key, str) or not model_key.strip():
                raise ValueError(
                    "semantic recommendation requires input_data.embedding_model"
                )
            normalized_model_key = model_key.strip()
            provider, _, remainder = normalized_model_key.partition(":")
            if provider not in SUPPORTED_RECOMMENDATION_EMBEDDING_PROVIDERS:
                raise ValueError(
                    "Recommendation embeddings require a supported provider: "
                    "'openai' or 'gemini'"
                )
            model_name, separator, dimension_text = remainder.rpartition(":")
            if not separator or not model_name.strip():
                raise ValueError(
                    "input_data.embedding_model must follow 'provider:model:dimensions'"
                )
            try:
                model_dimensions = int(dimension_text)
            except ValueError as exc:
                raise ValueError(
                    "input_data.embedding_model must end with its vector dimension"
                ) from exc
            if len(profile_embedding) != model_dimensions:
                raise ValueError(
                    "input_data.profile_embedding dimension does not match "
                    "input_data.embedding_model"
                )
            validated_embedding = finite_embedding
            validated_model_key = normalized_model_key
        else:
            validated_embedding = None
            validated_model_key = ""

        profile_id = input_data.get("master_profile_id")
        normalized_profile_id = (
            str(profile_id).strip() if profile_id is not None else ""
        )
        return _ProfileRankingInput(
            profile_index=profile_index,
            domain=domain.strip(),
            segmentation_tags=segmentation_tags,
            profile_embedding=validated_embedding,
            embedding_model=validated_model_key,
            profile_id=normalized_profile_id or None,
        )

    def _load_candidates(
        self,
        *,
        tenant_id: UUID,
        candidate_ids: Sequence[UUID],
        profiles: Sequence[_ProfileRankingInput],
        settings: _RankingSettings,
    ) -> list[Mapping[str, Any]]:
        profile_inputs = json.dumps(
            [
                {
                    "profile_index": profile.profile_index,
                    "domain": profile.domain,
                    "segmentation_tags": profile.segmentation_tags,
                    "profile_embedding": (
                        _vector_literal(profile.profile_embedding)
                        if profile.profile_embedding is not None
                        else None
                    ),
                }
                for profile in profiles
            ],
            separators=(",", ":"),
            allow_nan=False,
        )
        params = (
            settings.strategy,
            settings.semantic_weight,
            settings.tag_weight,
            settings.embedding_model,
            settings.minimum_score,
            str(tenant_id),
            [str(candidate_id) for candidate_id in candidate_ids],
            settings.top_k,
            profile_inputs,
        )
        query = RANK_CANDIDATE_CONTENT_SQL.replace(
            "__VECTOR_DIMENSIONS__", str(settings.dimensions)
        )
        with self._connection_factory() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (str(tenant_id),),
                )
                cursor.execute(query, params)
                return list(cursor.fetchall())

    def _raise_no_candidates(
        self,
        *,
        tenant_id: UUID,
        candidate_ids: Sequence[UUID],
        profile: _ProfileRankingInput,
        settings: _RankingSettings,
        counts: Mapping[str, int] | None = None,
    ) -> None:
        diagnostics = (
            CandidateDiagnostics.from_mapping(counts)
            if counts is not None
            else self._candidate_diagnostics(
                tenant_id=tenant_id,
                candidate_ids=candidate_ids,
                profile=profile,
                settings=settings,
            )
        )
        reason = diagnostics.failure_reason()
        profile_context = f"profile={profile.profile_id}, " if profile.profile_id else ""
        raise ValueError(
            f"Recommendation ranking failed: {reason}; "
            f"{profile_context}domain={profile.domain}, "
            f"strategy={settings.strategy}, minimum_score={settings.minimum_score}, "
            f"selected={len(candidate_ids)}, tenant={diagnostics.tenant_candidates}, "
            f"active={diagnostics.active_candidates}, "
            f"domain_matching={diagnostics.domain_candidates}, "
            f"rankable={diagnostics.rankable_candidates}"
        )

    def _candidate_diagnostics(
        self,
        *,
        tenant_id: UUID,
        candidate_ids: Sequence[UUID],
        profile: _ProfileRankingInput,
        settings: _RankingSettings,
    ) -> CandidateDiagnostics:
        with self._connection_factory() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (str(tenant_id),),
                )
                cursor.execute(
                    CANDIDATE_DIAGNOSTICS_SQL,
                    (
                        profile.domain,
                        profile.domain,
                        settings.strategy,
                        settings.dimensions,
                        settings.embedding_model,
                        str(tenant_id),
                        [str(candidate_id) for candidate_id in candidate_ids],
                    ),
                )
                counts = cursor.fetchone()
        if counts is None:
            raise RuntimeError("Recommendation diagnostics returned no row")
        return CandidateDiagnostics.from_mapping(counts)

    @staticmethod
    def _serialize_candidate(
        row: Mapping[str, Any],
        rank: int,
        *,
        default_strategy: RankingStrategy = "tags",
    ) -> dict[str, Any]:
        matched_tags = list(row["matched_tags"] or [])
        semantic_score = row.get("semantic_score")
        tag_score = row.get("tag_score", 0.0)
        strategy = row.get("strategy") or default_strategy
        published_at: datetime | None = row["published_at"]
        reason: RankingReason = (
            "semantic_and_segment_tag_match"
            if semantic_score is not None and semantic_score > 0 and matched_tags
            else "semantic_similarity"
            if semantic_score is not None and semantic_score > 0
            else "segment_tag_overlap"
            if matched_tags
            else "no_segment_tag_overlap"
        )
        return {
            "item_id": str(row["content_item_id"]),
            "rank": rank,
            "score": row["score"],
            "semantic_score": semantic_score,
            "tag_score": tag_score,
            "strategy": strategy,
            "matched_tags": matched_tags,
            "reason": reason,
            "item_type": row["item_type"],
            "title": row["title"],
            "summary": row["summary"],
            "image_url": row["image_url"],
            "cta_label": row["cta_label"],
            "cta_url": row["cta_url"],
            "published_at": published_at.isoformat() if published_at else None,
        }


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _weight(configuration: dict[str, Any], name: str, default: float) -> float:
    value = _finite_number(configuration.get(name, default))
    if value is None or not 0 <= value <= 1:
        raise ValueError(f"configuration.{name} must be between 0 and 1")
    return value


def _vector_literal(vector: list[float] | None) -> str | None:
    if vector is None:
        return None
    return "[" + ",".join(str(float(value)) for value in vector) + "]"

