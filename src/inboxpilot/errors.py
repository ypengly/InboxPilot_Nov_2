"""Exception hierarchy. Every InboxPilotError carries a message safe to show a user."""
from __future__ import annotations


class InboxPilotError(Exception):
    def __init__(self, message: str, *, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message or message


class ConfigError(InboxPilotError):
    """Missing or invalid configuration (e.g. OAuth client file not found)."""


class AuthError(InboxPilotError):
    """Sign-in failed, was cancelled, or access was revoked."""


class ProviderError(InboxPilotError):
    """The email provider's API returned an error or could not be reached."""


class StorageError(InboxPilotError):
    """Local database problem."""


class AIError(InboxPilotError):
    """Optional AI service failed. Local analysis is always available as a fallback."""


class Cancelled(Exception):
    """Raised inside a background task when the user cancels it."""
