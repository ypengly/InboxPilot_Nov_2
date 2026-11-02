"""Local, extractive summariser. It only re-uses sentences that exist in the email."""
from __future__ import annotations

import re

from ..models import Summary
from .deadlines import describe_deadline
from .patterns import REQUEST_RE
from .signals import AnalysisContext, MessageLike, Signals, extract_signals
from .textutils import first_name, shorten, split_sentences, strip_boilerplate

_STOP_NAMES = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october", "november", "december", "please", "thanks",
    "thank", "hello", "regards", "best", "dear", "the", "this", "that", "team", "hi", "hey", "also", "and", "but",
    "for", "from", "with", "here", "there", "our", "your", "let", "see", "just", "following", "attached", "cheers",
}
_NAME = r"[A-Z][a-z]{1,20}(?:\s[A-Z][a-z]{1,20})?"
_MENTION_RE = re.compile(rf"(?:\bcc'?ing\b|\bcc:?|\bwith\b|\bask\b|\basked\b|\bthanks to\b|\bintroduc(?:e|ing)\b|"
                         rf"\bloop(?:ing)? in\b|\bfrom\b|\bto\b)\s+({_NAME})")


def _clean_name(n: str) -> str:
    return " ".join(p for p in n.split() if p.lower() not in _STOP_NAMES)


def find_people(msg: MessageLike, ctx: AnalysisContext, text: str) -> list[str]:
    seen: dict[str, str] = {}

    def add(name: str, role: str) -> None:
        name = _clean_name(name.strip())
        if not name or name.lower() in _STOP_NAMES:
            return
        seen.setdefault(name.lower(), f"{name} ({role})")

    me = ctx.user_email.lower()
    if msg.sender_name or msg.sender_email:
        add(msg.sender_name or msg.sender_email, "sender")
    for n, e in msg.to:
        if e.lower() != me:
            add(n or e, "recipient")
    for n, e in msg.cc:
        if e.lower() != me:
            add(n or e, "cc")
    header_firsts = {first_name(k).lower() for k in seen}
    for m in _MENTION_RE.finditer(text):
        nm = _clean_name(m.group(1))
        if nm and nm.split()[0].lower() not in header_firsts and nm.lower() not in _STOP_NAMES:
            add(nm, "mentioned in message")
    return list(seen.values())


def _score(sentence: str, idx: int) -> float:
    s = 0.0
    if 7 <= len(sentence.split()) <= 32:
        s += 1
    if re.search(r"\d", sentence):
        s += 1.5
    if REQUEST_RE.search(sentence):
        s += 1.5
    if len(re.findall(r"\b[A-Z][a-z]+", sentence)) >= 2:
        s += 0.5
    if idx < 3:
        s += 0.5
    return s


def suggest_response(msg: MessageLike, sig: Signals) -> str:
    if msg.is_sent:
        return "This is a message you sent; no response is needed from you unless the recipient replies."
    if sig.bulk or sig.automated_sender:
        return "No response appears to be needed (automated or bulk message)."
    who = first_name(msg.sender_name or msg.sender_email) or "the sender"
    if sig.questions:
        return f"Reply to {who} answering their question: \u201c{shorten(sig.questions[0], 120)}\u201d"
    if sig.requests:
        return f"Reply to {who} confirming whether you can do what they asked: \u201c{shorten(sig.requests[0], 120)}\u201d"
    return "No response appears to be needed; no question or request was detected."


def summarize_local(msg: MessageLike, ctx: AnalysisContext, sig: Signals | None = None) -> Summary:
    sig = sig or extract_signals(msg, ctx)
    sentences = split_sentences(strip_boilerplate(sig.text))
    if not sentences:
        summary = (f"The message has no readable text; its subject is \u201c{msg.subject}\u201d."
                   if msg.subject else "The message has no readable text.")
        key_points = ["No key points identified."]
    else:
        lead = [s for s in sentences if not s.endswith("?")][:2] or sentences[:1]
        summary = shorten(" ".join(lead), 320)
        rest = [(s, i) for i, s in enumerate(sentences) if s not in lead]
        ranked = sorted(rest, key=lambda t: (-_score(*t), t[1]))[:4]
        ranked.sort(key=lambda t: t[1])
        key_points = [shorten(s, 200) for s, _ in ranked if _score(s, 99) >= 1.0] or ["No additional key points identified."]

    actions = [shorten(s, 200) for s in sig.requests[:5]]
    for q in sig.questions:
        if len(actions) >= 6:
            break
        if shorten(q, 200) not in actions:
            actions.append("Answer: " + shorten(q, 190))
    if not actions:
        actions = ["No action items identified."]

    return Summary(summary=summary, key_points=key_points, action_items=actions,
                   deadline=describe_deadline(sig.deadline), people=find_people(msg, ctx, sig.text),
                   suggested_response=suggest_response(msg, sig), engine="local")
