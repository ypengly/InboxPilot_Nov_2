"""Secret storage in the operating system's credential vault (Windows Credential Manager, macOS Keychain,
Secret Service / KWallet). If no secure backend exists, secrets are kept in memory for this session only -
they are never written to disk in plain text."""
from __future__ import annotations

import logging
from typing import Protocol

from ..config import KEYRING_SERVICE

log = logging.getLogger(__name__)


class Backend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


class MemoryBackend:
    """Session-only storage (also used by tests)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._data.get((service, username))

    def set_password(self, service, username, password):
        self._data[(service, username)] = password

    def delete_password(self, service, username):
        self._data.pop((service, username), None)


class SecretStore:
    def __init__(self, backend: Backend | None = None, service: str = KEYRING_SERVICE):
        self.service = service
        if backend is not None:
            self._backend, self.secure = backend, not isinstance(backend, MemoryBackend)
            return
        try:
            import keyring
            from keyring.backends import fail
            kr = keyring.get_keyring()
            if not isinstance(kr, fail.Keyring) and getattr(kr, "priority", 1) > 0:
                self._backend, self.secure = keyring, True
                return
            log.warning("No secure credential backend available; secrets will be kept in memory only.")
        except Exception as exc:  # keyring missing or broken
            log.warning("keyring unavailable (%s); secrets will be kept in memory only.", exc)
        self._backend, self.secure = MemoryBackend(), False

    def get(self, key: str) -> str | None:
        try:
            return self._backend.get_password(self.service, key)
        except Exception as exc:
            log.warning("Could not read secret %s: %s", key, exc)
            return None

    def set(self, key: str, value: str) -> None:
        self._backend.set_password(self.service, key, value)

    def delete(self, key: str) -> None:
        try:
            self._backend.delete_password(self.service, key)
        except Exception:
            pass  # already absent
