"""Cleanup applied to every piece of model-written text."""

_EM_DASH = chr(0x2014)  # built from the code point so this file holds no literal em dash


def strip_em_dashes(text: str) -> str:
    """Trim the text and turn em dashes into commas (antislop R-02). En dashes,
    as in number ranges, are left alone."""
    return text.strip().replace(f" {_EM_DASH} ", ", ").replace(_EM_DASH, ", ")
