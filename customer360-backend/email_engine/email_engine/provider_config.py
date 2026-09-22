"""Compatibility imports for the former email-specific resolver module."""

from .connector_config import _db_config, _env_config, load_email_config

__all__ = ["_db_config", "_env_config", "load_email_config"]
