"""Reusable retrieval ranking strategies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ReciprocalRankFusion:
    """Merge ranked result lists without comparing incompatible score scales."""

    rank_constant: int = 60

    def __post_init__(self) -> None:
        if self.rank_constant < 1:
            raise ValueError("rank_constant must be positive")

    def fuse(self, result_lists: Iterable[tuple[str, list[dict]]], limit: int) -> list[dict]:
        if limit <= 0:
            return []

        merged: dict[str, dict] = {}
        for source, hits in result_lists:
            for rank, hit in enumerate(hits, start=1):
                chunk_id = hit["id"]
                item = merged.setdefault(chunk_id, dict(hit))
                item["rrf_score"] = item.get("rrf_score", 0.0) + 1 / (
                    self.rank_constant + rank
                )
                item[f"{source}_rank"] = rank
                source_score = hit.get("score", hit.get(f"{source}_score"))
                if source_score is not None:
                    item[f"{source}_score"] = float(source_score)

        ranked = sorted(
            merged.values(),
            key=lambda item: (-item["rrf_score"], item.get("vector_rank", 10**9), item["id"]),
        )
        for item in ranked:
            item["score"] = item["rrf_score"]
        return ranked[:limit]