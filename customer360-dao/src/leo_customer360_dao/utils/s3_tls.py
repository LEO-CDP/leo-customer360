"""S3 TLS warning policy for explicitly configured insecure development endpoints."""

import warnings

from urllib3.exceptions import InsecureRequestWarning


def suppress_insecure_request_warning(verify_ssl: bool) -> None:
    """Suppress urllib3's warning when certificate verification is intentionally disabled."""
    if verify_ssl:
        return
    warnings.filterwarnings(
        "ignore",
        message="Unverified HTTPS request",
        category=InsecureRequestWarning,
    )
