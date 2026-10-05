"""Tenant-scoped content ranking pipeline and result contract."""

import math
from datetime import datetime
from typing import Any, Callable, ClassVar, Literal, Mapping, Sequence
from uuid import UUID

from psycopg2.extras import RealDictCursor
from pydantic import Field, FiniteFloat

from ...runner import (
    DB_SCHEMA,
    SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS,
    _connect,
)
from ..contracts import AgentPipelineInput, AgentResultModel, ModelType
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
              AND (%s = 'tags' OR (embedding IS NOT NULL AND embedding_model = %s))
        ) AS rankable_candidates
    FROM {DB_SCHEMA}.cdp_content_items
    WHERE tenant_id = %s AND content_item_id = ANY(%s::uuid[])
"""
RANK_CANDIDATE_CONTENT_SQL = f"""
    WITH criteria AS (
        SELECT
            %s::text[] AS profile_tags,
            %s::vector(__VECTOR_DIMENSIONS__) AS profile_embedding,
            %s::text AS strategy,
            %s::double precision AS semantic_weight,
            %s::double precision AS tag_weight,
            %s::text AS embedding_model,
            %s::double precision AS minimum_score,
            %s::uuid AS tenant_id,
            %s::text AS domain,
            %s::uuid[] AS candidate_ids,
            %s::integer AS top_k
    ),
    candidate_scores AS (
        SELECT
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
                WHEN cardinality(criteria.profile_tags) = 0 THEN 0.0
                ELSE cardinality(overlap.matched_tags)::double precision
                    / cardinality(criteria.profile_tags)
            END AS tag_score,
            CASE
                WHEN criteria.strategy = 'tags' THEN 0.0
                ELSE GREATEST(
                    0.0, LEAST(
                        1.0,
                        (
                            1.0 - (
                                content.embedding::vector(__VECTOR_DIMENSIONS__)
                                <=> criteria.profile_embedding
                            )
                        ) / 2.0
                    )
                )
            END AS semantic_score,
            criteria.*
        FROM {DB_SCHEMA}.cdp_content_items AS content
        CROSS JOIN criteria
        CROSS JOIN LATERAL (
            SELECT COALESCE(array_agg(tag ORDER BY tag), ARRAY[]::text[]) AS matched_tags
            FROM (
                SELECT DISTINCT candidate_tag AS tag
                FROM unnest(COALESCE(content.segment_tags, ARRAY[]::text[]))
                    AS candidate_tags(candidate_tag)
                WHERE candidate_tag = ANY(criteria.profile_tags)
            ) AS matching_tags
        ) AS overlap
        WHERE content.tenant_id = criteria.tenant_id
          AND content.status_code = 1
          AND (content.domain = 'all' OR content.domain = criteria.domain)
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
    )
    SELECT
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
        score
    FROM scored
    WHERE score >= minimum_score
    ORDER BY score DESC, published_at DESC NULLS LAST, content_item_id
    LIMIT (SELECT top_k FROM criteria)
"""


class RankedContentItem(AgentResultModel):
    item_id: UUID
    rank: int = Field(gt=0, strict=True)
    score: FiniteFloat = Field(ge=0, le=1)
    semantic_score: FiniteFloat | None = Field(default=None, ge=0, le=1)
    tag_score: FiniteFloat = Field(default=0.0, ge=0, le=1)
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
        candidate_ids = payload.candidate_content_item_ids
        if not candidate_ids:
            return {"ranked_items": []}

        domain = payload.input_data.get("domain")
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError(
                "ranking_recommendation input_data.domain must be a non-blank string"
            )

        segmentation_tags = payload.input_data.get("segmentation_tags", [])
        if not isinstance(segmentation_tags, list) or any(
            not isinstance(tag, str) or not tag.strip() for tag in segmentation_tags
        ):
            raise ValueError(
                "ranking_recommendation input_data.segmentation_tags must be a list "
                "of non-blank strings"
            )

        strategy = payload.configuration.get("strategy", "tags")
        if strategy not in {"tags", "semantic", "hybrid"}:
            raise ValueError("configuration.strategy must be tags, semantic, or hybrid")

        limit = payload.configuration.get(
            "top_k",
            payload.configuration.get("limit", DEFAULT_RESULT_LIMIT),
        )
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(
                "ranking_recommendation configuration.top_k must be a positive integer"
            )
        if limit > 100:
            raise ValueError("ranking_recommendation configuration.top_k must not exceed 100")

        semantic_weight = _weight(payload.configuration, "semantic_weight", 0.7)
        tag_weight = _weight(payload.configuration, "tag_weight", 0.3)
        if strategy == "hybrid" and semantic_weight + tag_weight <= 0:
            raise ValueError("hybrid semantic_weight and tag_weight must sum to more than zero")
        minimum_score = payload.configuration.get("minimum_score", 0.0)
        if (
            isinstance(minimum_score, bool)
            or not isinstance(minimum_score, (int, float))
            or not 0 <= minimum_score <= 1
        ):
            raise ValueError("configuration.minimum_score must be between 0 and 1")

        profile_embedding = payload.input_data.get("profile_embedding")
        model_key = payload.input_data.get("embedding_model")
        if strategy in {"semantic", "hybrid"}:
            if not isinstance(profile_embedding, list) or not profile_embedding:
                raise ValueError(
                    f"{strategy} recommendation requires input_data.profile_embedding"
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in profile_embedding
            ):
                raise ValueError("input_data.profile_embedding must contain finite numbers")
            if not isinstance(model_key, str) or not model_key.strip():
                raise ValueError("semantic recommendation requires input_data.embedding_model")
            if len(profile_embedding) not in SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS:
                raise ValueError(
                    "input_data.profile_embedding must have 384 or 768 dimensions"
                )
            if len(profile_embedding) != int(model_key.rsplit(":", 1)[-1]):
                raise ValueError(
                    "input_data.profile_embedding dimension does not match "
                    "input_data.embedding_model"
                )
        else:
            profile_embedding = None
            model_key = ""

        if strategy == "semantic":
            semantic_weight, tag_weight = 1.0, 0.0
        elif strategy == "tags":
            semantic_weight, tag_weight = 0.0, 1.0

        rows = self._load_candidates(
            tenant_id=payload.tenant_id,
            domain=domain.strip(),
            segmentation_tags=segmentation_tags,
            candidate_ids=candidate_ids,
            profile_embedding=profile_embedding,
            strategy=strategy,
            semantic_weight=semantic_weight,
            tag_weight=tag_weight,
            embedding_model=model_key,
            minimum_score=float(minimum_score),
            top_k=limit,
        )
        return {
            "ranked_items": [
                self._serialize_candidate(row, rank)
                for rank, row in enumerate(rows[:limit], start=1)
            ]
        }

    def _load_candidates(
        self,
        *,
        tenant_id: UUID,
        domain: str,
        segmentation_tags: Sequence[str],
        candidate_ids: Sequence[UUID],
        profile_embedding: list[float] | None,
        strategy: str,
        semantic_weight: float,
        tag_weight: float,
        embedding_model: str,
        minimum_score: float,
        top_k: int,
    ) -> list[Mapping[str, Any]]:
        params = (
            list(segmentation_tags),
            _vector_literal(profile_embedding) if profile_embedding is not None else None,
            strategy,
            semantic_weight,
            tag_weight,
            embedding_model,
            minimum_score,
            str(tenant_id),
            domain,
            [str(candidate_id) for candidate_id in candidate_ids],
            top_k,
        )
        dimensions = (
            len(profile_embedding)
            if profile_embedding is not None
            else min(SUPPORTED_RECOMMENDATION_EMBEDDING_DIMENSIONS)
        )
        query = RANK_CANDIDATE_CONTENT_SQL.replace(
            "__VECTOR_DIMENSIONS__", str(dimensions)
        )
        with self._connection_factory() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (str(tenant_id),),
                )
                cursor.execute(query, params)
                rows = list(cursor.fetchall())
                if rows:
                    return rows

                cursor.execute(
                    CANDIDATE_DIAGNOSTICS_SQL,
                    (
                        domain,
                        domain,
                        strategy,
                        embedding_model,
                        str(tenant_id),
                        [str(candidate_id) for candidate_id in candidate_ids],
                    ),
                )
                counts = cursor.fetchone()
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
                raise ValueError(
                    f"Recommendation ranking failed: {reason}; "
                    f"domain={domain}, strategy={strategy}, minimum_score={minimum_score}, "
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


def _weight(configuration: dict[str, Any], name: str, default: float) -> float:
    value = configuration.get(name, default)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"configuration.{name} must be between 0 and 1")
    return float(value)


def _vector_literal(vector: list[float] | None) -> str | None:
    if vector is None:
        return None
    return "[" + ",".join(str(float(value)) for value in vector) + "]"
