"""Provider abstraction. To add a mail service (Microsoft Graph, IMAP+OAuth, ...), subclass EmailProvider,
implement the abstract methods and decorate the class with @register. Nothing else in the app changes."""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar, Iterator, Sequence

from ..errors import Cancelled
from ..models import EmailMessage
from ..storage.secrets import SecretStore

_REGISTRY: dict[str, type["EmailProvider"]] = {}


def register(cls: type["EmailProvider"]) -> type["EmailProvider"]:
    _REGISTRY[cls.id] = cls
    return cls


def provider_class(provider_id: str) -> type["EmailProvider"]:
    try:
        return _REGISTRY[provider_id]
    except KeyError:
        raise KeyError(f"Unknown email provider: {provider_id}") from None


def available_providers() -> dict[str, type["EmailProvider"]]:
    return dict(_REGISTRY)


def check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


class EmailProvider(ABC):
    id: ClassVar[str]
    display_name: ClassVar[str]
    is_demo: ClassVar[bool] = False

    @classmethod
    @abstractmethod
    def create(cls, *, secrets: SecretStore, client_secrets_path: str = "", account_email: str = "") -> "EmailProvider":
        ...

    @property
    @abstractmethod
    def account_email(self) -> str:
        """Address of the connected account ('' if not connected)."""

    @abstractmethod
    def connect(self) -> str:
        """Run the sign-in flow (OAuth). Returns the account's email address. Never asks for a password."""

    @abstractmethod
    def disconnect(self) -> None:
        """Forget stored credentials and revoke access where the provider supports it."""

    @abstractmethod
    def is_connected(self) -> bool:
        ...

    @abstractmethod
    def list_ids(self, scope: str, since: datetime, cancel: threading.Event | None = None) -> list[str]:
        """IDs (newest first) for scope 'inbox', 'sent' or 'unread' since a date."""

    @abstractmethod
    def fetch_messages(self, ids: Sequence[str], cancel: threading.Event | None = None) -> Iterator[EmailMessage]:
        """Yield full messages for the given IDs. Missing/deleted IDs are skipped."""
