"""Priority scoring that explains itself instead of claiming to know what is 'objectively' important."""
from __future__ import annotations

from ..models import PriorityResult, Reason
from .signals import AnalysisContext, MessageLike, Signals, extract_signals

HIGH_AT = 4.0
MEDIUM_AT = 2.0


def score_priority(msg: MessageLike, ctx: AnalysisContext, sig: Signals | None = None,
                   category: str = "") -> PriorityResult:
    if msg.is_sent:
        return PriorityResult("Low", 0.0, [Reason("You sent this message", False)])
    sig = sig or extract_signals(msg, ctx)
    score = 0.0
    reasons: list[Reason] = []

    def add(points: float, text: str, positive: bool = True) -> None:
        nonlocal score
        score += points
        reasons.append(Reason(text, positive))

    if sig.directly_addressed and sig.recipient_count <= 5:
        add(2, "Directly addressed to you")
    if sig.requests and not sig.bulk:
        add(2 if sig.directly_addressed else 1, "Contains a requested action")
    if sig.deadline:
        add(2 if sig.directly_addressed else 1, "Mentions a deadline")
    if sig.questions and not sig.bulk:
        add(1, "Asks a question")
    if sig.urgent and not sig.bulk:
        add(2, "Uses urgent wording")
    if sig.payment_problem:
        add(2, "Mentions a payment or account problem")
    if sig.known_sender:
        add(1, "You have written to this sender before")
    if sig.participated:
        add(1, "Part of a conversation you replied in")
    if sig.bulk:
        add(-2, "Looks like a bulk mailing or newsletter", False)
    if sig.automated_sender:
        add(-1, "Sent from an automated address", False)
    if sig.cc_only:
        add(-1, "You are only CC\u2019d", False)
    elif sig.recipient_count > 10:
        add(-1, "Sent to a large group", False)

    level = "High" if score >= HIGH_AT else "Medium" if score >= MEDIUM_AT else "Low"
    if not any(r.positive for r in reasons):
        reasons.insert(0, Reason("No strong signals found", False))
    return PriorityResult(level, score, reasons)


def format_priority(level: str, reasons: list[Reason], overridden: bool = False) -> str:
    head = f"Priority: {level}" + (" (set by you)" if overridden else "")
    lines = [head, "", "Reasons:"]
    lines += [("\u2713 " if r.positive else "\u2022 ") + r.text for r in reasons]
    return "\n".join(lines)
