"""Tenant-scoped content ranking pipeline and result contract."""

from datetime import datetime
from typing import Any, Callable, ClassVar, Literal, Mapping, Sequence
from uuid import UUID

from psycopg2.extras import RealDictCursor
from pydantic import Field, FiniteFloat

from ...runner import DB_SCHEMA, _connect
from ..contracts import AgentPipelineInput, AgentResultModel, ModelType
from .base import AgentTypePipeline

DEFAULT_RESULT_LIMIT = 8
RANK_CANDIDATE_CONTENT_SQL = f"""
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
        cardinality(overlap.matched_tags) AS score
    FROM {DB_SCHEMA}.cdp_content_items AS content
    CROSS JOIN LATERAL (
        SELECT COALESCE(array_agg(tag ORDER BY tag), ARRAY[]::text[]) AS matched_tags
        FROM (
            SELECT DISTINCT candidate_tag AS tag
            FROM unnest(COALESCE(content.segment_tags, ARRAY[]::text[]))
                AS candidate_tags(candidate_tag)
            WHERE candidate_tag = ANY(%s::text[])
        ) AS matching_tags
    ) AS overlap
    WHERE content.tenant_id = %s
      AND content.status_code = 1
      AND (content.domain = 'all' OR content.domain = %s)
      AND content.content_item_id = ANY(%s::uuid[])
    ORDER BY score DESC, content.published_at DESC NULLS LAST, content.content_item_id
"""


class RankedContentItem(AgentResultModel):
    item_id: UUID
    rank: int = Field(gt=0, strict=True)
    score: FiniteFloat = Field(ge=0)
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

        limit = payload.configuration.get("limit", DEFAULT_RESULT_LIMIT)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(
                "ranking_recommendation configuration.limit must be a positive integer"
            )

        rows = self._load_candidates(
            tenant_id=payload.tenant_id,
            domain=domain.strip(),
            segmentation_tags=segmentation_tags,
            candidate_ids=candidate_ids,
        )
        if not rows:
            raise ValueError(
                "Selected recommendation candidates are unavailable for this tenant, "
                "active status, or profile domain"
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
    ) -> list[Mapping[str, Any]]:
        params = (
            list(segmentation_tags),
            str(tenant_id),
            domain,
            [str(candidate_id) for candidate_id in candidate_ids],
        )
        with self._connection_factory() as connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, true)",
                    (str(tenant_id),),
                )
                cursor.execute(RANK_CANDIDATE_CONTENT_SQL, params)
                return list(cursor.fetchall())

    @staticmethod
    def _serialize_candidate(row: Mapping[str, Any], rank: int) -> dict[str, Any]:
        matched_tags = list(row["matched_tags"] or [])
        published_at: datetime | None = row["published_at"]
        return {
            "item_id": str(row["content_item_id"]),
            "rank": rank,
            "score": row["score"],
            "matched_tags": matched_tags,
            "reason": "segment_tag_overlap" if matched_tags else "no_segment_tag_overlap",
            "item_type": row["item_type"],
            "title": row["title"],
            "summary": row["summary"],
            "image_url": row["image_url"],
            "cta_label": row["cta_label"],
            "cta_url": row["cta_url"],
            "published_at": published_at.isoformat() if published_at else None,
        }
