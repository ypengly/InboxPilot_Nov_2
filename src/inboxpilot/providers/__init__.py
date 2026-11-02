"""Importing this package registers all built-in providers."""
from . import demo, gmail  # noqa: F401
from .base import EmailProvider, available_providers, provider_class, register  # noqa: F401
