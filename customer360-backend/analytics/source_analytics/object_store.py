"""S3/MinIO access for hourly tracking-log objects."""

import re
from typing import Any, Iterator, Optional

from .config import AnalyticsSettings


HOURLY_FOLDER_PATTERN = re.compile(
    r"^(?:events/)?(\d{4}-\d{2}-\d{2}-\d{2})/(.+\.jsonl(?:\.gz)?)$"
)


class S3EventStore:
    """List and retrieve immutable hourly event objects."""

    def __init__(self, client: Any, settings: AnalyticsSettings) -> None:
        self.client = client
        self.settings = settings

    def iter_hourly_objects(
        self,
        bucket: str,
        start_after: Optional[str] = None,
        prefix: Optional[str] = None,
    ) -> Iterator[tuple[str, str]]:
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            paginate_kwargs: dict[str, str] = {"Bucket": bucket}
            if prefix:
                paginate_kwargs["Prefix"] = prefix
            if start_after:
                paginate_kwargs["StartAfter"] = start_after
            for page in paginator.paginate(**paginate_kwargs):
                for item in page.get("Contents", []):
                    key = str(item.get("Key", ""))
                    match = HOURLY_FOLDER_PATTERN.match(key)
                    if match:
                        yield match.group(1), key
        except Exception as exc:
            error_code = str(
                getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            )
            if error_code in {"404", "NoSuchBucket", "NotFound"}:
                return
            raise

    def get_object(self, bucket: str, object_key: str) -> Any:
        return self.client.get_object(Bucket=bucket, Key=object_key)