"""Parse and load IAB Audience Taxonomy 1.1 TSV files for ``customer360.cdp_audience_tags``."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from leo_customer360_dao.schemas.segmentation import AudienceTagCreate, build_audience_tag_name

MAX_AUDIENCE_TAXONOMY_TSV_BYTES = 10 * 1024 * 1024
MAX_AUDIENCE_TAXONOMY_TSV_ROWS = 20_000

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AUDIENCE_TAXONOMY_TSV_PATH = (
    _REPO_ROOT / "customer360-seeding" / "data-dictionary" / "audience_taxonomy1.1.tsv"
)

_UNIQUE_ID_HEADERS = ("Unique ID", "unique_id", "Unique_ID")
_PARENT_ID_HEADERS = ("Parent ID", "parent_id", "Parent_ID")
_CONDENSED_NAME_HEADERS = (
    "Condensed Name (1st, 2nd, Last Tier)",
    "Condensed Name",
    "condensed_name",
    "Condensed_Name",
)
_TIER_HEADERS = (
    ("Tier 1", "tier_1", "Tier_1"),
    ("Tier 2", "tier_2", "Tier_2"),
    ("Tier 3", "tier_3", "Tier_3"),
    ("Tier 4", "tier_4", "Tier_4"),
    ("Tier 5", "tier_5", "Tier_5"),
    ("Tier 6", "tier_6", "Tier_6"),
)
_EXTENSION_NOTES_HEADERS = ("*Extension Notes", "Extension Notes", "extension_notes", "Extension_Notes")
_TAG_NAME_HEADERS = ("tag_name", "Tag Name", "Tag_Name")


class AudienceTaxonomyTsvError(ValueError):
    """Raised when an uploaded or local IAB Audience Taxonomy TSV file is invalid."""


def _resolve_header_index(header_map: dict[str, int], candidates: tuple[str, ...]) -> Optional[int]:
    for candidate in candidates:
        idx = header_map.get(candidate.lower())
        if idx is not None:
            return idx
    return None


def _cell(row: list[str], index: Optional[int]) -> Optional[str]:
    if index is None or index >= len(row):
        return None
    value = row[index].strip()
    return value if value else None


def parse_audience_taxonomy_tsv(
    content: bytes | str,
    *,
    taxonomy_version: str = "1.1",
) -> list[AudienceTagCreate]:
    """Parse an IAB Audience Taxonomy TSV payload into validated ``AudienceTagCreate`` models.

    Handles both the official IAB ``audience_taxonomy1.1.tsv`` layout (which
    starts with an empty leading tab column) and standard TSVs with or without
    an explicit ``tag_name`` column.
    """
    if isinstance(content, bytes):
        if not content:
            raise AudienceTaxonomyTsvError("The uploaded TSV is empty")
        if len(content) > MAX_AUDIENCE_TAXONOMY_TSV_BYTES:
            raise AudienceTaxonomyTsvError(
                f"The uploaded TSV exceeds the {MAX_AUDIENCE_TAXONOMY_TSV_BYTES}-byte limit"
            )
        try:
            text_content = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AudienceTaxonomyTsvError("The uploaded TSV must be UTF-8 encoded") from exc
    else:
        if not content or not content.strip():
            raise AudienceTaxonomyTsvError("The uploaded TSV is empty")
        text_content = content

    try:
        reader = csv.reader(StringIO(text_content, newline=""), delimiter="\t")
        try:
            raw_headers = next(reader)
        except StopIteration as exc:
            raise AudienceTaxonomyTsvError("The uploaded TSV must include a header row") from exc

        header_map: dict[str, int] = {}
        for idx, raw_header in enumerate(raw_headers):
            cleaned = (raw_header or "").strip().lower()
            if cleaned and cleaned not in header_map:
                header_map[cleaned] = idx

        unique_id_idx = _resolve_header_index(header_map, _UNIQUE_ID_HEADERS)
        parent_id_idx = _resolve_header_index(header_map, _PARENT_ID_HEADERS)
        condensed_idx = _resolve_header_index(header_map, _CONDENSED_NAME_HEADERS)
        tier_indices = [_resolve_header_index(header_map, candidates) for candidates in _TIER_HEADERS]
        extension_idx = _resolve_header_index(header_map, _EXTENSION_NOTES_HEADERS)
        tag_name_idx = _resolve_header_index(header_map, _TAG_NAME_HEADERS)

        missing_required: list[str] = []
        if unique_id_idx is None:
            missing_required.append("Unique ID")
        if condensed_idx is None:
            missing_required.append("Condensed Name (1st, 2nd, Last Tier)")
        if tier_indices[0] is None:
            missing_required.append("Tier 1")
        if missing_required:
            raise AudienceTaxonomyTsvError(
                "Missing required TSV column(s): " + ", ".join(missing_required)
            )

        records: list[AudienceTagCreate] = []
        for row_number, raw_row in enumerate(reader, start=2):
            if row_number > MAX_AUDIENCE_TAXONOMY_TSV_ROWS + 1:
                raise AudienceTaxonomyTsvError(
                    f"The TSV exceeds the {MAX_AUDIENCE_TAXONOMY_TSV_ROWS}-row limit"
                )
            if not any((cell or "").strip() for cell in raw_row):
                continue

            unique_id = _cell(raw_row, unique_id_idx)
            parent_id = _cell(raw_row, parent_id_idx)
            condensed_name = _cell(raw_row, condensed_idx)
            tiers = [_cell(raw_row, idx) for idx in tier_indices]
            extension_notes = _cell(raw_row, extension_idx)
            explicit_tag_name = _cell(raw_row, tag_name_idx)

            if not unique_id:
                raise AudienceTaxonomyTsvError(f"Row {row_number}: Unique ID cannot be blank")
            if not tiers[0]:
                raise AudienceTaxonomyTsvError(f"Row {row_number}: Tier 1 cannot be blank")

            computed_tag_name = explicit_tag_name or build_audience_tag_name(tiers)
            effective_condensed = condensed_name or " | ".join([t for t in tiers if t])

            try:
                record = AudienceTagCreate(
                    tag_name=computed_tag_name,
                    unique_id=unique_id,
                    parent_id=parent_id,
                    taxonomy_version=taxonomy_version,
                    condensed_name=effective_condensed,
                    tier_1=tiers[0],
                    tier_2=tiers[1],
                    tier_3=tiers[2],
                    tier_4=tiers[3],
                    tier_5=tiers[4],
                    tier_6=tiers[5],
                    extension_notes=extension_notes,
                    status_code=1,
                )
            except (ValidationError, ValueError) as exc:
                raise AudienceTaxonomyTsvError(f"Row {row_number}: {exc}") from exc

            records.append(record)

        if not records:
            raise AudienceTaxonomyTsvError("The TSV does not contain any audience taxonomy rows")
        return records
    except csv.Error as exc:
        raise AudienceTaxonomyTsvError(f"Malformed TSV: {exc}") from exc


def load_audience_taxonomy_tsv_file(
    file_path: str | Path | None = None,
    *,
    taxonomy_version: str = "1.1",
) -> list[AudienceTagCreate]:
    """Read and parse an IAB Audience Taxonomy TSV file from disk."""
    target_path = Path(file_path) if file_path is not None else DEFAULT_AUDIENCE_TAXONOMY_TSV_PATH
    if not target_path.is_file():
        raise FileNotFoundError(f"Audience taxonomy TSV file not found: {target_path}")
    return parse_audience_taxonomy_tsv(
        target_path.read_bytes(),
        taxonomy_version=taxonomy_version,
    )
