from __future__ import annotations

from src.retrieval import ReciprocalRankFusion


def test_rrf_promotes_chunks_found_by_both_vector_and_keyword_search():
    fusion = ReciprocalRankFusion(rank_constant=1)

    hits = fusion.fuse(
        [
            (
                "vector",
                [
                    {"id": "semantic", "text": "identity resolution"},
                    {"id": "shared", "text": "Customer Identity Resolution (CIR)"},
                ],
            ),
            (
                "keyword",
                [
                    {"id": "shared", "text": "Customer Identity Resolution (CIR)"},
                    {"id": "exact", "text": "CIR configuration"},
                ],
            ),
        ],
        limit=3,
    )

    assert [hit["id"] for hit in hits] == ["shared", "semantic", "exact"]
    assert hits[0]["vector_rank"] == 2
    assert hits[0]["keyword_rank"] == 1
    assert hits[0]["score"] == hits[0]["rrf_score"]


def test_rrf_rejects_invalid_rank_constant():
    try:
        ReciprocalRankFusion(rank_constant=0)
    except ValueError as exc:
        assert str(exc) == "rank_constant must be positive"
    else:
        raise AssertionError("invalid rank constant was accepted")