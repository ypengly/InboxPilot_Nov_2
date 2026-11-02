"""Detect messages that probably need a reply, and sent messages you may be waiting on."""
from __future__ import annotations

from ..models import FollowUpResult
from .signals import AnalysisContext, MessageLike, Signals, evidence, extract_signals

_PASSIVE_CATEGORIES = {"Newsletter", "Notifications", "Social"}
_TRANSACTIONAL = {"Finance", "Shopping", "Travel"}


def assess_followup(msg: MessageLike, ctx: AnalysisContext, sig: Signals | None = None,
                    category: str = "") -> FollowUpResult:
    sig = sig or extract_signals(msg, ctx)
    if msg.is_sent:
        waiting = bool(sig.questions or sig.requests) and not all(
            e.lower().split("@")[0].startswith(("noreply", "no-reply")) for _, e in msg.to)
        reason = ("You asked a question." if sig.questions else "You made a request.") if waiting else ""
        ev = evidence((sig.questions or sig.requests or [""])[0]) if waiting else ""
        return FollowUpResult(False, waiting, reason, ev)

    if sig.bulk or sig.automated_sender or (category in _PASSIVE_CATEGORIES and not sig.directly_addressed):
        return FollowUpResult(False, False)
    if sig.cc_only and not sig.participated:
        return FollowUpResult(False, False)
    if not (sig.directly_addressed or sig.participated or sig.known_sender):
        return FollowUpResult(False, False)

    if sig.questions:
        return FollowUpResult(True, False, "Question detected in message.", evidence(sig.questions[0]))
    if sig.requests and category not in _TRANSACTIONAL:
        return FollowUpResult(True, False, "Requested action detected.", evidence(sig.requests[0]))
    return FollowUpResult(False, False)
