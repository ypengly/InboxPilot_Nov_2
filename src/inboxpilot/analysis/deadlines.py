"""Deadline extraction. Only reports deadlines that are literally present in the email."""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta

from ..models import Deadline
from .textutils import shorten, split_sentences

NO_DEADLINE = "No deadline identified."

_WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|tues|thurs|thur|mon|tue|wed|thu|fri"
_MONTHS_LIST = ["january", "february", "march", "april", "may", "june", "july", "august",
                "september", "october", "november", "december"]
_MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december|"
           "sept|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec")
_TZ = r"(?:\s+(?-i:[A-Z]{2,4})\b)?"
_TIME = rf"(?:\s*(?:at|@)?\s*\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm|a\.m\.|p\.m\.){_TZ})?"

_WHEN = (
    rf"(?:(?:next\s+|this\s+)?(?:{_WEEKDAYS})\b{_TIME}"
    rf"|tomorrow{_TIME}|today{_TIME}|tonight"
    rf"|(?:the\s+)?end\s+of\s+(?:the\s+)?(?:day|week|month|quarter|year)"
    rf"|eod|eow|cob|close\s+of\s+business"
    rf"|next\s+(?:week|month)"
    rf"|(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?{_TIME}"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:{_MONTHS})\.?(?:,?\s+\d{{4}})?{_TIME}"
    rf"|\d{{4}}-\d{{2}}-\d{{2}}{_TIME}"
    rf"|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?{_TIME}"
    rf"|\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm|a\.m\.|p\.m\.){_TZ})"
)
_TRIGGER = (r"(?:by|before|due(?:\s+(?:on|by|date))?|deadline(?:\s+is|\s+of)?|no\s+later\s+than|until|"
            r"on\s+or\s+before|expires?(?:\s+on)?|closes?(?:\s+on)?|submit\s+by)\s*:?\s*")
DEADLINE_RE = re.compile(rf"\b{_TRIGGER}(?P<when>{_WHEN})", re.I)
WITHIN_RE = re.compile(r"\bwithin\s+(?:the\s+next\s+)?(?:\d+|one|two|three|four|five|a)\s+(?:business\s+)?(?:hours?|days?|weeks?)\b", re.I)


def _strip_time(when: str) -> str:
    return re.sub(r"\s*(?:at|@)?\s*\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)(?:\s+(?-i:[A-Z]{2,4})\b)?\s*$",
                  "", when, flags=re.I).strip()


def _month_num(token: str) -> int | None:
    token = token.lower().rstrip(".")
    for i, name in enumerate(_MONTHS_LIST, start=1):
        if name == token or (len(token) >= 3 and name.startswith(token[:3]) and token in (name[:3], name[:4], name)):
            return i
    return None


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _with_year(m: int, d: int, year: int | None, ref: date) -> date | None:
    if year is not None:
        return _safe_date(year, m, d)
    cand = _safe_date(ref.year, m, d)
    if cand and cand < ref - timedelta(days=90):
        cand = _safe_date(ref.year + 1, m, d)
    return cand


def resolve_when(when: str, ref: date) -> date | None:
    """Best-effort conversion of a phrase to a date, relative to the email's own date."""
    w = _strip_time(" ".join(when.lower().split()))
    if not w:
        return ref  # time-only like "5pm": same day as the email
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", w):
        return _safe_date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(rf"({_MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?", w):
        mon = _month_num(m[1])
        return _with_year(mon, int(m[2]), int(m[3]) if m[3] else None, ref) if mon else None
    if m := re.fullmatch(rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTHS})\.?(?:,?\s+(\d{{4}}))?", w):
        mon = _month_num(m[2])
        return _with_year(mon, int(m[1]), int(m[3]) if m[3] else None, ref) if mon else None
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", w):
        a, b = int(m[1]), int(m[2])
        year = int(m[3]) if m[3] else None
        if year is not None and year < 100:
            year += 2000
        if a > 12 >= b:
            return _with_year(b, a, year, ref)
        if b > 12 >= a:
            return _with_year(a, b, year, ref)
        return None  # ambiguous (e.g. 10/12): do not guess
    if w in ("today", "tonight", "eod", "cob", "close of business", "the end of the day", "end of day", "end of the day"):
        return ref
    if w == "tomorrow":
        return ref + timedelta(days=1)
    if w in ("eow", "end of week", "end of the week", "the end of the week", "the end of week"):
        return ref + timedelta(days=(4 - ref.weekday()) % 7)
    if w in ("end of month", "end of the month", "the end of the month", "the end of month"):
        return date(ref.year, ref.month, calendar.monthrange(ref.year, ref.month)[1])
    if m := re.fullmatch(rf"(?:(next|this)\s+)?({_WEEKDAYS})", w):
        target = next((i for i, n in enumerate(_WEEKDAY_NAMES) if n.startswith(m[2][:3])), None)
        if target is None:
            return None
        delta = (target - ref.weekday()) % 7
        if m[1] == "next" and delta < 7 - ref.weekday():
            delta += 7  # "next X": the occurrence in the following calendar week
        return ref + timedelta(days=delta)
    return None


def extract_deadline(text: str, ref: datetime | date, subject: str = "") -> Deadline | None:
    """Return the first deadline phrase stated in subject/body, else None."""
    ref_date = ref.date() if isinstance(ref, datetime) else ref
    hay = f"{subject}\n{text}" if subject else text
    best = None
    for rx in (DEADLINE_RE, WITHIN_RE):
        m = rx.search(hay)
        if m and (best is None or m.start() < best[0].start()):
            best = (m, rx)
    if not best:
        return None
    m, rx = best
    matched = " ".join(m.group(0).split())
    when = m.group("when") if rx is DEADLINE_RE else None
    resolved = resolve_when(when, ref_date) if when else None
    context = matched
    for s in split_sentences(hay):
        if matched.lower() in " ".join(s.lower().split()):
            context = shorten(s, 200)
            break
    return Deadline(matched=matched, context=context, resolved=resolved)


def describe_deadline(d: Deadline | None) -> str:
    if d is None:
        return NO_DEADLINE
    text = f"\u201c{d.matched}\u201d"
    if d.resolved:
        text += f" (interpreted as {d.resolved.strftime('%a %d %b %Y')}, relative to the date the email was sent)"
    return text
