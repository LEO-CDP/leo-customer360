"""Tenant-scoped content ranking pipeline and result contract."""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, ClassVar, Literal, Mapping, Sequence
from uuid import UUID

from psycopg2.extras import RealDictCursor
from pydantic import Field, FiniteFloat

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
    strategy: Literal["tags", "semantic", "hybrid"]
    top_k: int
    semantic_weight: float
    tag_weight: float
    minimum_score: float
    embedding_model: str
    dimensions: int


class RankedContentItem(AgentResultModel):
    item_id: UUID
    rank: int = Field(gt=0, strict=True)
    score: FiniteFloat = Field(ge=0, le=1)
    strategy: Literal["tags", "semantic", "hybrid"] = "tags"
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


class RankingRecommendationPipeline(AgentTypePipeline):
    model_type: ClassVar[ModelType] = "ranking_recommendation"
    result_model = RankingRecommendationResult

    def __init__(self, connection_factory: Callable[[], Any] = _connect) -> None:
        self._connection_factory = connection_factory

    def process(self, payload: AgentPipelineInput) -> dict:
        """Rank the selected candidates for a single profile."""
        return self.process_batch([payload])[0]

    def process_batch(
        self, payloads: Sequence[AgentPipelineInput]
    ) -> list[dict[str, Any]]:
        """Rank a shared candidate set for a bounded batch of profiles."""
        if not payloads:
            return []
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
        if not candidate_ids:
            return [{"ranked_items": []} for _ in payloads]

        strategy, top_k, semantic_weight, tag_weight, minimum_score = (
            self._ranking_options(first.configuration)
        )
        profiles = []
        for index, payload in enumerate(payloads):
            try:
                profiles.append(
                    self._profile_input(index, payload.input_data, strategy)
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
        embedding_model = embedding_models.pop()
        dimensions = (
            len(profiles[0].profile_embedding)
            if profiles[0].profile_embedding is not None
            else min(SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS)
        )
        settings = _RankingSettings(
            strategy=strategy,
            top_k=top_k,
            semantic_weight=semantic_weight,
            tag_weight=tag_weight,
            minimum_score=minimum_score,
            embedding_model=embedding_model,
            dimensions=dimensions,
        )

        rows = self._load_candidates(
            tenant_id=first.tenant_id,
            candidate_ids=candidate_ids,
            profiles=profiles,
            settings=settings,
        )
        ranked_by_profile: list[list[dict[str, Any]]] = [[] for _ in profiles]
        for row in rows:
            profile_index = row["profile_index"]
            if (
                isinstance(profile_index, bool)
                or not isinstance(profile_index, int)
                or not 0 <= profile_index < len(profiles)
            ):
                raise RuntimeError(
                    "Ranking query returned an invalid profile_index"
                )
            ranked_by_profile[profile_index].append(
                self._serialize_candidate(row, int(row["rank"]))
            )

        for profile, ranked_items in zip(profiles, ranked_by_profile, strict=True):
            if not ranked_items:
                self._raise_no_candidates(
                    tenant_id=first.tenant_id,
                    candidate_ids=candidate_ids,
                    profile=profile,
                    settings=settings,
                )

        return [{"ranked_items": items} for items in ranked_by_profile]

    @staticmethod
    def _ranking_options(
        configuration: dict[str, Any],
    ) -> tuple[Literal["tags", "semantic", "hybrid"], int, float, float, float]:
        strategy_value = configuration.get("strategy", "tags")
        if strategy_value == "tags":
            strategy: Literal["tags", "semantic", "hybrid"] = "tags"
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
        if top_k > 100:
            raise ValueError(
                "ranking_recommendation configuration.top_k must not exceed 100"
            )

        semantic_weight = _weight(configuration, "semantic_weight", 0.7)
        tag_weight = _weight(configuration, "tag_weight", 0.3)
        if strategy == "hybrid":
            if semantic_weight + tag_weight <= 0:
                raise ValueError(
                    "hybrid semantic_weight and tag_weight must sum to more than zero"
                )
        elif strategy == "semantic":
            semantic_weight, tag_weight = 1.0, 0.0
        else:
            semantic_weight, tag_weight = 0.0, 1.0

        minimum_score = _finite_number(configuration.get("minimum_score", 0.0))
        if minimum_score is None or not 0 <= minimum_score <= 1:
            raise ValueError("configuration.minimum_score must be between 0 and 1")

        return strategy, top_k, semantic_weight, tag_weight, minimum_score

    @staticmethod
    def _profile_input(
        profile_index: int,
        input_data: dict[str, Any],
        strategy: Literal["tags", "semantic", "hybrid"],
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
            provider = model_key.partition(":")[0]
            if provider not in SUPPORTED_RECOMMENDATION_EMBEDDING_PROVIDERS:
                raise ValueError(
                    "Recommendation embeddings require a supported provider: "
                    "'openai' or 'gemini'"
                )
            try:
                model_dimensions = int(model_key.rsplit(":", 1)[-1])
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
            validated_model_key = model_key
        else:
            validated_embedding = None
            validated_model_key = ""

        profile_id = input_data.get("master_profile_id")
        return _ProfileRankingInput(
            profile_index=profile_index,
            domain=domain.strip(),
            segmentation_tags=segmentation_tags,
            profile_embedding=validated_embedding,
            embedding_model=validated_model_key,
            profile_id=str(profile_id) if profile_id is not None else None,
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
    ) -> None:
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

        if counts["tenant_candidates"] == 0:
            reason = "selected candidates are unavailable for this tenant"
        elif counts["active_candidates"] == 0:
            reason = "selected candidates are inactive"
        elif counts["domain_candidates"] == 0:
            reason = "selected candidates do not match the profile domain"
        elif counts["rankable_candidates"] == 0:
            reason = "selected candidates lack embeddings for the configured model"
        else:
            reason = "no candidate score meets minimum_score"
        profile_context = f"profile={profile.profile_id}, " if profile.profile_id else ""
        raise ValueError(
            f"Recommendation ranking failed: {reason}; "
            f"{profile_context}domain={profile.domain}, "
            f"strategy={settings.strategy}, minimum_score={settings.minimum_score}, "
            f"selected={len(candidate_ids)}, tenant={counts['tenant_candidates']}, "
            f"active={counts['active_candidates']}, "
            f"domain_matching={counts['domain_candidates']}, "
            f"rankable={counts['rankable_candidates']}"
        )

    @staticmethod
    def _serialize_candidate(row: Mapping[str, Any], rank: int) -> dict[str, Any]:
        matched_tags = list(row["matched_tags"] or [])
        semantic_score = row.get("semantic_score")
        tag_score = row.get("tag_score", 0.0)
        strategy = row.get("strategy", "tags")
        published_at: datetime | None = row["published_at"]
        return {
            "item_id": str(row["content_item_id"]),
            "rank": rank,
            "score": row["score"],
            "semantic_score": semantic_score,
            "tag_score": tag_score,
            "strategy": strategy,
            "matched_tags": matched_tags,
            "reason": (
                "semantic_and_segment_tag_match"
                if semantic_score is not None and semantic_score > 0 and matched_tags
                else "semantic_similarity"
                if semantic_score is not None and semantic_score > 0
                else "segment_tag_overlap"
                if matched_tags
                else "no_segment_tag_overlap"
            ),
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
