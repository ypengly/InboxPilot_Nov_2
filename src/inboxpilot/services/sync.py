"""Synchronise a provider's mailbox into the local database. Read-only with respect to the mail server."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..errors import ProviderError
from ..providers.base import EmailProvider
from ..storage.db import Repository
from ..storage.settings import Settings
from .analyzer import Analyzer

log = logging.getLogger(__name__)
Progress = Callable[[str, int, int], None]


@dataclass
class SyncResult:
    new_messages: int = 0
    analyzed: int = 0
    removed: int = 0
    warnings: list[str] = field(default_factory=list)


class SyncService:
    def __init__(self, repo: Repository, provider: EmailProvider, analyzer: Analyzer, settings: Settings):
        self.repo, self.provider, self.analyzer, self.settings = repo, provider, analyzer, settings

    def run(self, progress: Progress | None = None, cancel: threading.Event | None = None) -> SyncResult:
        emit = progress or (lambda *_: None)
        result = SyncResult()
        email = self.provider.account_email
        acct = self.repo.upsert_account(self.provider.id, email)
        since = datetime.now(timezone.utc) - timedelta(days=self.settings.sync_days)
        complete = True
        inbox: list[str] = []

        # AuthError and Cancelled propagate to the caller; other provider errors keep what was downloaded.
        try:
            emit("Listing messages", 0, 0)
            inbox = self.provider.list_ids("inbox", since, cancel)
            sent = self.provider.list_ids("sent", since, cancel)
            unread = self.provider.list_ids("unread", since, cancel)
            known = self.repo.known_remote_ids(acct)
            todo = [i for i in inbox if i not in known] + [i for i in sent if i not in known]
            for n, msg in enumerate(self.provider.fetch_messages(todo, cancel), 1):
                self.repo.upsert_email(acct, msg)
                result.new_messages += 1
                if n % 10 == 0:
                    emit("Downloading messages", n, len(todo))
            emit("Downloading messages", len(todo), len(todo))
            self.repo.set_read_states(acct, set(unread), set(inbox))
        except ProviderError as exc:
            complete = False
            log.warning("Sync stopped early: %s", exc)
            result.warnings.append(exc.user_message)

        if complete:
            result.removed = self.repo.prune_missing_inbox(acct, set(inbox), since)
        self.repo.resolve_threads(acct)
        result.analyzed = self.analyzer.analyze_pending(acct, email, self.settings.user_name, emit, cancel)
        self.repo.touch_sync(acct)
        return result
