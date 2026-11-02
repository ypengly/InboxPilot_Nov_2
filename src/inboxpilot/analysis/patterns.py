"""Shared regular expressions used by several analysers."""
from __future__ import annotations

import re

REQUEST_RE = re.compile(
    r"""\b(?:
        please\s+(?:\w+\s+){0,2}?(?:review|send|confirm|let\s+(?:me|us)\s+know|reply|respond|approve|sign|submit|
            complete|provide|share|update|check|take\s+a\s+look|call|schedule|advise|forward|fill|attach|verify|
            join|rsvp|follow\s+up|get\s+back|handle|prepare|book|pay|add|remove|fix|look\s+(?:at|into))
      | (?:can|could|would|will)\s+you\b
      | i\s+(?:need|want)\s+you\s+to
      | we\s+need\s+(?:you\s+)?(?:to|your)
      | you\s+(?:need|must|should|have)\s+to
      | action\s+(?:is\s+)?required
      | kindly\s+\w+
      | let\s+me\s+know
      | (?:waiting|awaiting)\s+(?:for\s+)?your
      | your\s+(?:approval|confirmation|feedback|input|response|reply|signature|sign-?off)
      | needs?\s+your
      | rsvp
    )""",
    re.I | re.X,
)

URGENT_RE = re.compile(
    r"\b(urgent(?:ly)?|asap|as soon as possible|immediately|time[- ]sensitive|action required|final notice|critical)\b", re.I)

PAYMENT_PROBLEM_RE = re.compile(
    r"(payment (?:failed|declined|was declined|unsuccessful)|past due|overdue|account (?:suspended|locked)|"
    r"will be (?:suspended|closed|cancelled|canceled)|unable to (?:process|charge)|card (?:declined|expired))", re.I)

RECEIPT_RE = re.compile(
    r"\b(receipt|invoice|order confirmation|your order|order #?\d|payment (?:received|confirmation|successful)|"
    r"you paid|thanks? for your (?:purchase|order|payment)|purchase confirmation|billing statement|e-?receipt)\b", re.I)

AUTOMATED_LOCAL_RE = re.compile(
    r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|donotreply|notifications?|notify|mailer-daemon|postmaster|alerts?|"
    r"bounces?|automated|newsletter|digest|marketing|news|updates?|noreply\+.*)", re.I)

BOILERPLATE_QUESTION_RE = re.compile(
    r"^(?:any |have any )?(?:questions|need help|need assistance|want to learn more|why am i getting this)\s*\?*$", re.I)


def is_automated_address(email: str) -> bool:
    local = (email or "").split("@")[0]
    return bool(AUTOMATED_LOCAL_RE.match(local))
