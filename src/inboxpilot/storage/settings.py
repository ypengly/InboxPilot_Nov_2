"""Typed access to persisted (non-secret) settings."""
from __future__ import annotations

from .db import Repository


class Settings:
    def __init__(self, repo: Repository):
        self._repo = repo

    def _int(self, key: str, lo: int, hi: int, default: int) -> int:
        try:
            return max(lo, min(hi, int(self._repo.get_setting(key))))
        except ValueError:
            return default

    @property
    def sync_days(self) -> int:
        return self._int("sync_days", 7, 365, 90)

    @sync_days.setter
    def sync_days(self, v: int) -> None:
        self._repo.set_setting("sync_days", str(int(v)))

    @property
    def follow_up_days(self) -> int:
        return self._int("follow_up_days", 1, 30, 3)

    @follow_up_days.setter
    def follow_up_days(self, v: int) -> None:
        self._repo.set_setting("follow_up_days", str(int(v)))

    @property
    def gmail_client_secrets_path(self) -> str:
        return self._repo.get_setting("gmail_client_secrets_path")

    @gmail_client_secrets_path.setter
    def gmail_client_secrets_path(self, v: str) -> None:
        self._repo.set_setting("gmail_client_secrets_path", v)

    @property
    def user_name(self) -> str:
        return self._repo.get_setting("user_name")

    @user_name.setter
    def user_name(self, v: str) -> None:
        self._repo.set_setting("user_name", v.strip())

    @property
    def ai_enabled(self) -> bool:
        return self._repo.get_setting("ai_enabled") == "1"

    @ai_enabled.setter
    def ai_enabled(self, v: bool) -> None:
        self._repo.set_setting("ai_enabled", "1" if v else "0")

    @property
    def ai_model(self) -> str:
        return self._repo.get_setting("ai_model") or "claude-sonnet-5-5"

    @ai_model.setter
    def ai_model(self, v: str) -> None:
        self._repo.set_setting("ai_model", v.strip())

    @property
    def active_provider(self) -> str:
        return self._repo.get_setting("active_provider")

    @property
    def active_account(self) -> str:
        return self._repo.get_setting("active_account")

    def set_active(self, provider: str, account: str) -> None:
        self._repo.set_setting("active_provider", provider)
        self._repo.set_setting("active_account", account)
