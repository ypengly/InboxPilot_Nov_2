"""One-pass extraction of the evidence the categoriser, priority and follow-up logic share."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..models import Address, Deadline
from .deadlines import extract_deadline
from .patterns import (BOILERPLATE_QUESTION_RE, PAYMENT_PROBLEM_RE, REQUEST_RE, URGENT_RE,
                       is_automated_address)
from .textutils import shorten, split_sentences, strip_quoted


class MessageLike(Protocol):
    thread_id: str
    sender_name: str
    sender_email: str
    to: list[Address]
    cc: list[Address]
    subject: str
    date: object
    snippet: str
    body_text: str
    is_sent: bool
    labels: list[str]
    list_unsubscribe: bool
    bulk: bool


@dataclass
class AnalysisContext:
    user_email: str = ""
    user_name: str = ""
    known_contacts: set[str] = field(default_factory=set)       # people the user has written to
    participated_threads: set[str] = field(default_factory=set)  # threads containing a sent message

    @property
    def user_domain(self) -> str:
        return self.user_email.split("@")[-1].lower() if "@" in self.user_email else ""


@dataclass
class Signals:
    text: str
    questions: list[str]
    requests: list[str]
    deadline: Deadline | None
    urgent: bool
    payment_problem: bool
    directly_addressed: bool
    cc_only: bool
    recipient_count: int
    automated_sender: bool
    bulk: bool
    known_sender: bool
    participated: bool


def body_text_for_analysis(msg: MessageLike) -> str:
    return strip_quoted(msg.body_text or msg.snippet or "")


def find_questions(text: str) -> list[str]:
    return [s for s in split_sentences(text)
            if s.endswith("?") and len(s) >= 8 and not BOILERPLATE_QUESTION_RE.match(s)]


def find_requests(text: str) -> list[str]:
    return [s for s in split_sentences(text) if REQUEST_RE.search(s)]


def extract_signals(msg: MessageLike, ctx: AnalysisContext) -> Signals:
    text = body_text_for_analysis(msg)
    me = ctx.user_email.lower()
    to_emails = [e.lower() for _, e in msg.to]
    cc_emails = [e.lower() for _, e in msg.cc]
    direct = bool(me) and me in to_emails
    cc_only = bool(me) and me in cc_emails and me not in to_emails
    hay = f"{msg.subject}\n{text}"
    return Signals(
        text=text,
        questions=find_questions(text),
        requests=find_requests(text),
        deadline=extract_deadline(text, msg.date, msg.subject),
        urgent=bool(URGENT_RE.search(hay)),
        payment_problem=bool(PAYMENT_PROBLEM_RE.search(hay)),
        directly_addressed=direct,
        cc_only=cc_only,
        recipient_count=len(set(to_emails + cc_emails)),
        automated_sender=is_automated_address(msg.sender_email),
        bulk=bool(msg.bulk or msg.list_unsubscribe),
        known_sender=msg.sender_email.lower() in ctx.known_contacts,
        participated=bool(msg.thread_id) and msg.thread_id in ctx.participated_threads,
    )


def evidence(s: str) -> str:
    return shorten(s, 160)
