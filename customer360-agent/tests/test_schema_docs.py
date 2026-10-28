"""Retrieval docs for Text-to-SQL come from the live schema: column names plus
the authors' own COMMENT ON TABLE text, so descriptions cannot drift away from
the database."""

import pytest

from use_cases.schema_docs import MAX_COMMENT_CHARS, build_schema_docs, load_schema_docs

# Synthetic columns and comments, test-only.
COLUMNS = {
    "cdp_segments": ["segment_id", "segment_name", "member_count"],
    "crm_flow": ["flow_id", "name", "status"],
}
COMMENTS = {"cdp_segments": "Saved audience groups and their member counts."}


def _description(doc: str, table: str) -> str:
    return doc.split(" Columns: ")[0].removeprefix(f"{table}: ")


def test_doc_has_the_table_name_the_description_and_the_columns():
    docs = build_schema_docs(COLUMNS, COMMENTS)

    assert docs["cdp_segments"] == (
        "cdp_segments: Saved audience groups and their member counts. Columns: segment_id, segment_name, member_count"
    )


def test_table_without_a_comment_still_gets_its_columns():
    docs = build_schema_docs(COLUMNS, COMMENTS)

    assert docs["crm_flow"] == "crm_flow. Columns: flow_id, name, status"


def test_long_comment_is_cut_at_a_word_boundary_within_the_limit():
    docs = build_schema_docs({"t": ["a"]}, {"t": "word " * 400})

    description = _description(docs["t"], "t")
    assert len(description) <= MAX_COMMENT_CHARS
    assert description.endswith("...")
    assert set(description.removesuffix("...").split()) == {"word"}


def test_whitespace_in_a_comment_is_collapsed():
    docs = build_schema_docs({"t": ["a"]}, {"t": "Line one\n   line two.\t"})

    assert _description(docs["t"], "t") == "Line one line two."


def test_a_comment_without_final_punctuation_gets_a_full_stop():
    docs = build_schema_docs({"t": ["a"]}, {"t": "Holds things"})

    assert _description(docs["t"], "t") == "Holds things."


def test_comment_for_an_unknown_table_is_ignored():
    docs = build_schema_docs(COLUMNS, {**COMMENTS, "ghost": "Not a table."})

    assert "ghost" not in docs


def test_loader_groups_rows_by_table_and_keeps_column_order():
    rows = [
        {"table_name": "cdp_segments", "column_name": "segment_id", "comment": "Saved audience groups."},
        {"table_name": "cdp_segments", "column_name": "member_count", "comment": "Saved audience groups."},
        {"table_name": "crm_flow", "column_name": "flow_id", "comment": None},
    ]

    docs = load_schema_docs(lambda sql: rows)

    assert docs["cdp_segments"] == "cdp_segments: Saved audience groups. Columns: segment_id, member_count"
    assert docs["crm_flow"] == "crm_flow. Columns: flow_id"


def test_loader_query_skips_partition_children():
    issued = []

    load_schema_docs(lambda sql: issued.append(sql) or [])

    assert "NOT c.relispartition" in issued[0]


def test_loader_rejects_a_schema_name_that_is_not_an_identifier():
    issued = []

    with pytest.raises(ValueError):
        load_schema_docs(lambda sql: issued.append(sql) or [], schema="x'; DROP TABLE y; --")

    assert issued == []
