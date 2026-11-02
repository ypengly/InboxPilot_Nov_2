"""Runs the analysis pipeline over stored messages."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable

from ..analysis.categorizer import categorize
from ..analysis.followup import assess_followup
from ..analysis.priority import score_priority
from ..analysis.signals import AnalysisContext, extract_signals
from ..config import ANALYSIS_VERSION
from ..errors import Cancelled
from ..models import EmailRecord
from ..storage.db import Repository, iso

Progress = Callable[[str, int, int], None]


def derive_name(email: str) -> str:
    local = email.split("@")[0]
    return " ".join(p.capitalize() for p in local.replace("_", ".").replace("-", ".").split(".") if p)


class Analyzer:
    def __init__(self, repo: Repository):
        self.repo = repo

    def build_context(self, account_id: int, user_email: str, user_name: str = "") -> AnalysisContext:
        return AnalysisContext(user_email=user_email, user_name=user_name or derive_name(user_email),
                               known_contacts=self.repo.known_contacts(account_id),
                               participated_threads=self.repo.participated_threads(account_id))

    def analyze_record(self, rec: EmailRecord, ctx: AnalysisContext) -> None:
        sig = extract_signals(rec, ctx)
        cat = categorize(rec, ctx)
        pri = score_priority(rec, ctx, sig, cat.category)
        fu = assess_followup(rec, ctx, sig, cat.category)
        self.repo.save_analysis(
            rec.id, ANALYSIS_VERSION, category=cat.category, category_reasons=cat.reasons, is_receipt=cat.is_receipt,
            priority=pri.level, score=pri.score, reasons=pri.reasons, needs_reply=fu.needs_reply,
            awaiting_reply=fu.awaiting_reply, followup_reason=fu.reason, followup_evidence=fu.evidence)

    def analyze_pending(self, account_id: int, user_email: str, user_name: str = "", progress: Progress | None = None,
                        cancel: threading.Event | None = None) -> int:
        pending = self.repo.unanalyzed(account_id, ANALYSIS_VERSION, iso(datetime.now(timezone.utc)))
        if not pending:
            return 0
        ctx = self.build_context(account_id, user_email, user_name)
        for i, rec in enumerate(pending, 1):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            self.analyze_record(rec, ctx)
            if progress and i % 25 == 0:
                progress("Analysing messages", i, len(pending))
        if progress:
            progress("Analysing messages", len(pending), len(pending))
        return len(pending)
