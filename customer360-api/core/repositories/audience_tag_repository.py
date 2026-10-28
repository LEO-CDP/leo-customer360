"""API-layer repository for IAB Audience Taxonomy tags (``customer360.cdp_audience_tags``)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from core.utils.audience_taxonomy_utils import (
    DEFAULT_AUDIENCE_TAXONOMY_TSV_PATH,
    load_audience_taxonomy_tsv_file,
    parse_audience_taxonomy_tsv,
)
from leo_customer360_dao.repositories.audience_tag_repository import (
    AudienceTagRepository as DaoAudienceTagRepository,
)
from leo_customer360_dao.schemas.segmentation import AudienceTagImportResult


class AudienceTagRepository(DaoAudienceTagRepository):
    """Extends the DAO AudienceTagRepository with TSV upload/file upsert helpers."""

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    def import_from_tsv_bytes(
        self,
        content: bytes,
        *,
        taxonomy_version: str = "1.1",
    ) -> AudienceTagImportResult:
        """Parse TSV bytes and upsert all rows into ``cdp_audience_tags`` by ``tag_name``."""
        records = parse_audience_taxonomy_tsv(content, taxonomy_version=taxonomy_version)
        upserted_count = self.bulk_upsert_tags(records, commit=True)
        return AudienceTagImportResult(
            taxonomy_version=taxonomy_version,
            total_rows_parsed=len(records),
            upserted_count=upserted_count,
            sample_tag_names=[record.tag_name for record in records[:10] if record.tag_name],
        )

    def import_from_tsv_file(
        self,
        file_path: Optional[str | Path] = None,
        *,
        taxonomy_version: str = "1.1",
    ) -> AudienceTagImportResult:
        """Load an IAB Audience Taxonomy TSV file from disk and upsert all rows by ``tag_name``."""
        target_path = Path(file_path) if file_path is not None else DEFAULT_AUDIENCE_TAXONOMY_TSV_PATH
        records = load_audience_taxonomy_tsv_file(target_path, taxonomy_version=taxonomy_version)
        upserted_count = self.bulk_upsert_tags(records, commit=True)
        return AudienceTagImportResult(
            taxonomy_version=taxonomy_version,
            total_rows_parsed=len(records),
            upserted_count=upserted_count,
            sample_tag_names=[record.tag_name for record in records[:10] if record.tag_name],
        )
