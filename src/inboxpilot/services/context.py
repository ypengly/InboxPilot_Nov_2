"""Application service container: wires storage, providers, analysis and optional AI together.
The UI talks to this class only, which keeps Qt code thin and everything else testable."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..analysis.ai import AIAssistant
from ..analysis.drafts import generate_local_draft
from ..analysis.signals import AnalysisContext
from ..analysis.summarizer import summarize_local
from ..config import db_path
from ..errors import AIError, ConfigError
from ..models import Draft, EmailQuery, EmailRecord, Summary
from ..providers import available_providers, provider_class
from ..providers.base import EmailProvider
from ..storage.db import Database, Repository
from ..storage.secrets import SecretStore
from ..storage.settings import Settings
from .analyzer import Analyzer, derive_name
from .sync import SyncResult, SyncService

log = logging.getLogger(__name__)
AI_KEY = "ai:api_key"


class AppContext:
    def __init__(self, repo: Repository | None = None, secrets: SecretStore | None = None, path: str | Path | None = None):
        self.repo = repo or Repository(Database(path or db_path()))
        self.secrets = secrets or SecretStore()
        self.settings = Settings(self.repo)
        self.analyzer = Analyzer(self.repo)
        self._provider: EmailProvider | None = None

    # ---- account / provider ------------------------------------------------------------------------
    def _make(self, provider_id: str, account_email: str = "") -> EmailProvider:
        return provider_class(provider_id).create(
            secrets=self.secrets, client_secrets_path=self.settings.gmail_client_secrets_path, account_email=account_email)

    def provider(self) -> EmailProvider | None:
        if self._provider is None and self.settings.active_provider:
            try:
                self._provider = self._make(self.settings.active_provider, self.settings.active_account)
            except KeyError:
                return None
        return self._provider

    def reset_provider(self) -> None:
        """Drop the cached provider so it is rebuilt with current settings (e.g. a new OAuth client file)."""
        self._provider = None

    @property
    def has_account(self) -> bool:
        return bool(self.settings.active_provider and self.settings.active_account)

    @property
    def account_id(self) -> int | None:
        if not self.has_account:
            return None
        row = self.repo.get_account(self.settings.active_provider, self.settings.active_account)
        return row["id"] if row else None

    @property
    def user_email(self) -> str:
        return self.settings.active_account

    @property
    def user_name(self) -> str:
        return self.settings.user_name or derive_name(self.user_email)

    @property
    def is_demo(self) -> bool:
        p = self.provider()
        return bool(p and p.is_demo)

    def connect(self, provider_id: str) -> str:
        """Runs the provider's OAuth flow (blocking; call from a worker thread)."""
        provider = self._make(provider_id)
        email = provider.connect()
        self.repo.upsert_account(provider_id, email)
        self.settings.set_active(provider_id, email)
        self._provider = provider
        return email

    def disconnect(self, delete_local_data: bool) -> None:
        provider = self.provider()
        aid = self.account_id
        if provider:
            provider.disconnect()
        if delete_local_data and aid is not None:
            self.repo.delete_account_data(aid)
        self.settings.set_active("", "")
        self._provider = None

    def sync(self, progress=None, cancel: threading.Event | None = None) -> SyncResult:
        provider = self.provider()
        if provider is None:
            raise ConfigError("No account", user_message="No account is connected.")
        if not provider.is_connected():
            email = provider.connect()  # re-authorise (e.g. token was removed); same OAuth flow, no password
            if email.lower() != self.settings.active_account.lower():
                raise ConfigError("Different account", user_message=f"You signed in as {email}, but this workspace is for "
                                  f"{self.settings.active_account}. Disconnect first to switch accounts.")
        return SyncService(self.repo, provider, self.analyzer, self.settings).run(progress, cancel)

    @staticmethod
    def available_providers() -> dict[str, type[EmailProvider]]:
        return available_providers()

    # ---- queries -----------------------------------------------------------------------------------
    def search(self, q: EmailQuery) -> list[EmailRecord]:
        aid = self.account_id
        return self.repo.search(aid, q, self.settings.follow_up_days) if aid else []

    def stats(self) -> dict[str, int]:
        aid = self.account_id
        zero = {"unread": 0, "important": 0, "pending_replies": 0, "recent": 0, "total": 0}
        return self.repo.stats(aid, self.settings.follow_up_days) if aid else zero

    def folder_counts(self) -> dict[str, int]:
        aid = self.account_id
        return self.repo.folder_counts(aid, self.settings.follow_up_days) if aid else {}

    def get_email(self, email_id: int) -> EmailRecord | None:
        aid = self.account_id
        return self.repo.get_email(aid, email_id, self.settings.follow_up_days) if aid else None

    def _analysis_context(self) -> AnalysisContext:
        return self.analyzer.build_context(self.account_id or 0, self.user_email, self.user_name)

    # ---- AI / summaries / drafts ---------------------------------------------------------------------
    def ai_available(self) -> bool:
        return self.settings.ai_enabled and bool(self.secrets.get(AI_KEY))

    def set_ai_key(self, key: str) -> None:
        if key.strip():
            self.secrets.set(AI_KEY, key.strip())

    def clear_ai_key(self) -> None:
        self.secrets.delete(AI_KEY)

    def _ai(self) -> AIAssistant:
        return AIAssistant(self.secrets.get(AI_KEY) or "", self.settings.ai_model)

    def local_summary(self, rec: EmailRecord) -> Summary:
        cached = self.repo.get_summary(rec.id)
        return cached if cached else summarize_local(rec, self._analysis_context())

    def summarize(self, rec: EmailRecord, use_ai: bool = False) -> Summary:
        """Summary for one message. AI is used only when the person asked for it and enabled it in Settings."""
        if use_ai and self.ai_available():
            try:
                s = self._ai().summarize(rec)
                self.repo.save_summary(rec.id, s)
                return s
            except AIError as exc:
                log.warning("AI summary failed, using local summary: %s", exc)
                s = summarize_local(rec, self._analysis_context())
                s.engine = "local (AI failed)"
                return s
        s = summarize_local(rec, self._analysis_context())
        self.repo.save_summary(rec.id, s)
        return s

    def draft(self, rec: EmailRecord, tone: str, use_ai: bool = False) -> Draft:
        ctx = self._analysis_context()
        if use_ai and self.ai_available():
            try:
                return self._ai().draft(rec, tone, ctx.user_name)
            except AIError as exc:
                log.warning("AI draft failed, using template: %s", exc)
        return generate_local_draft(rec, ctx, tone)
