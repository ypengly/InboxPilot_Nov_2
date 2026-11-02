"""Plain data classes shared across layers. No Qt / network imports here."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

CATEGORIES = (
    "Work", "Personal", "Finance", "Shopping", "Newsletter",
    "Social", "Travel", "Notifications", "Important", "Needs Reply",
)
# Categories the automatic categoriser can assign. "Important" and "Needs Reply"
# are available as manual categories; the app also derives them from priority/follow-up.
AUTO_CATEGORIES = CATEGORIES[:8]
PRIORITIES = ("High", "Medium", "Low")

Address = tuple[str, str]  # (display name, email)


@dataclass
class EmailMessage:
    """Provider-neutral message as fetched from a mail API."""
    remote_id: str
    thread_id: str
    sender_name: str
    sender_email: str
    to: list[Address]
    cc: list[Address]
    subject: str
    date: datetime  # timezone-aware UTC
    snippet: str = ""
    body_text: str = ""
    is_read: bool = True
    is_sent: bool = False
    has_attachments: bool = False
    labels: list[str] = field(default_factory=list)
    list_unsubscribe: bool = False
    bulk: bool = False  # Precedence: bulk / Auto-Submitted / List-Id style headers
    in_reply_to: str = ""


@dataclass
class Reason:
    text: str
    positive: bool = True


@dataclass
class CategoryResult:
    category: str
    reasons: list[str]
    is_receipt: bool = False


@dataclass
class PriorityResult:
    level: str
    score: float
    reasons: list[Reason]


@dataclass
class FollowUpResult:
    needs_reply: bool
    awaiting_reply: bool  # for messages YOU sent: you asked something and may be waiting
    reason: str = ""
    evidence: str = ""


@dataclass
class Deadline:
    matched: str            # exact text from the email, e.g. "by Friday 5pm"
    context: str            # the sentence it appeared in
    resolved: date | None   # best-effort calendar date, None when ambiguous


@dataclass
class Summary:
    summary: str
    key_points: list[str]
    action_items: list[str]
    deadline: str
    people: list[str]
    suggested_response: str
    engine: str = "local"   # "local" or "ai"


@dataclass
class Draft:
    subject: str
    body: str
    engine: str = "local"


@dataclass
class EmailRecord:
    """A message as stored in SQLite, including analysis and user overrides."""
    id: int
    account_id: int
    remote_id: str
    thread_id: str
    sender_name: str
    sender_email: str
    to: list[Address]
    cc: list[Address]
    subject: str
    date: datetime
    snippet: str
    body_text: str
    is_read: bool
    is_sent: bool
    has_attachments: bool
    labels: list[str]
    list_unsubscribe: bool
    bulk: bool
    in_reply_to: str
    category: str
    auto_category: str
    user_category: str | None
    priority: str
    auto_priority: str
    user_priority: str | None
    priority_score: float
    priority_reasons: list[Reason]
    category_reasons: list[str]
    needs_reply: bool
    user_needs_reply: bool | None
    follow_up: bool
    user_follow_up: bool | None
    awaiting_reply: bool
    followup_reason: str
    followup_evidence: str
    is_receipt: bool
    answered: bool
    analyzed: bool

    @property
    def sender_display(self) -> str:
        return self.sender_name or self.sender_email or "(unknown sender)"

    @property
    def counterparty_display(self) -> str:
        """Who the message is from, or for sent mail 'To: recipient'."""
        if self.is_sent and self.to:
            n, e = self.to[0]
            more = f" +{len(self.to) - 1}" if len(self.to) > 1 else ""
            return f"To: {n or e}{more}"
        return self.sender_display

    @property
    def display_subject(self) -> str:
        return self.subject or "(no subject)"

    @classmethod
    def from_row(cls, r) -> "EmailRecord":
        def j(v, default):
            try:
                return json.loads(v) if v else default
            except (TypeError, ValueError):
                return default

        def addrs(v):
            return [(a[0], a[1]) for a in j(v, []) if isinstance(a, list) and len(a) == 2]

        def tri(v):
            return None if v is None else bool(v)

        dt = datetime.fromisoformat(r["date_utc"]).replace(tzinfo=timezone.utc)
        reasons = [Reason(x[0], bool(x[1])) for x in j(r["priority_reasons_json"], []) if len(x) == 2]
        return cls(
            id=r["id"], account_id=r["account_id"], remote_id=r["remote_id"], thread_id=r["thread_id"] or "",
            sender_name=r["sender_name"] or "", sender_email=r["sender_email"] or "",
            to=addrs(r["to_json"]), cc=addrs(r["cc_json"]), subject=r["subject"] or "", date=dt,
            snippet=r["snippet"] or "", body_text=r["body_text"] or "", is_read=bool(r["is_read"]),
            is_sent=bool(r["is_sent"]), has_attachments=bool(r["has_attachments"]),
            labels=j(r["labels_json"], []), list_unsubscribe=bool(r["list_unsubscribe"]),
            bulk=bool(r["bulk"]), in_reply_to=r["in_reply_to"] or "",
            category=r["category"], auto_category=r["auto_category"], user_category=r["user_category"],
            priority=r["priority"], auto_priority=r["auto_priority"], user_priority=r["user_priority"],
            priority_score=r["priority_score"] or 0.0, priority_reasons=reasons,
            category_reasons=j(r["category_reasons_json"], []),
            needs_reply=bool(r["needs_reply"]), user_needs_reply=tri(r["user_needs_reply"]),
            follow_up=bool(r["follow_up"]), user_follow_up=tri(r["user_follow_up"]),
            awaiting_reply=bool(r["awaiting_reply"]), followup_reason=r["followup_reason"] or "",
            followup_evidence=r["followup_evidence"] or "", is_receipt=bool(r["is_receipt"]),
            answered=bool(r["answered"]), analyzed=bool(r["analyzed_version"]),
        )


@dataclass
class EmailQuery:
    folder: str = "inbox"   # inbox|important|needs_reply|follow_up|newsletters|receipts|unread
    text: str = ""
    sender: str = ""
    category: str = ""
    priority: str = ""
    unread_only: bool = False
    date_from: date | None = None
    date_to: date | None = None
    sort_by: str = "date"   # date|sender|subject|priority|category|unread
    descending: bool = True
    limit: int = 2000


FOLDERS = (
    ("inbox", "Inbox"),
    ("important", "Important"),
    ("needs_reply", "Needs Reply"),
    ("follow_up", "Follow Up"),
    ("newsletters", "Newsletters"),
    ("receipts", "Receipts"),
    ("unread", "Unread"),
)
