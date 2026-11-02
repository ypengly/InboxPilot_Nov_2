"""Rule-based categoriser. Transparent on purpose: every decision comes with reasons."""
from __future__ import annotations

import re

from ..models import CategoryResult
from .patterns import RECEIPT_RE, is_automated_address
from .signals import AnalysisContext, MessageLike

FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "me.com",
             "proton.me", "protonmail.com", "aol.com", "live.com", "msn.com", "gmx.com", "yandex.com"}

_RULES: dict[str, dict] = {
    "Finance": dict(
        domains=("paypal", "stripe", "chase", "bankofamerica", "wellsfargo", "citi", "americanexpress", "amex", "venmo",
                 "wise", "revolut", "intuit", "quickbooks", "irs", "fidelity", "schwab", "vanguard", "coinbase", "bank"),
        subject=r"invoice|receipt|payment|statement|\bbill\b|billing|\btax\b|transaction|refund|\bbank\b|balance|payroll|"
                r"credit card|overdue|expense|wire transfer",
        body=r"invoice|payment|account balance|statement|transaction|billing|amount due|refund"),
    "Shopping": dict(
        domains=("amazon", "ebay", "etsy", "shopify", "walmart", "target", "aliexpress", "bestbuy", "zalando", "lazada",
                 "shopee", "ikea"),
        subject=r"\border\b|shipped|shipping|delivery|delivered|tracking|package|\bsale\b|% off|discount|coupon|\bcart\b|\bdeal",
        body=r"tracking number|your order|add to cart|shop now|free shipping"),
    "Travel": dict(
        domains=("airbnb", "booking", "expedia", "airlines", "airways", "delta", "klm", "lufthansa", "emirates", "qatar",
                 "agoda", "tripadvisor", "kayak", "skyscanner", "hotels", "marriott", "hilton"),
        subject=r"flight|itinerary|booking|reservation|boarding|check-?in|\bhotel|e-?ticket|\bvisa\b",
        body=r"confirmation (?:code|number)|departure|arrival|boarding pass|check-?in"),
    "Social": dict(
        domains=("linkedin", "facebook", "facebookmail", "twitter", "x", "instagram", "tiktok", "pinterest", "reddit",
                 "discord", "meetup", "quora"),
        subject=r"connection|mentioned you|followed you|new follower|friend request|tagged you|invited you to connect|"
                r"commented on|liked your|birthday",
        body=r"view (?:this )?(?:on|in) (?:the )?(?:app|linkedin|facebook)"),
    "Notifications": dict(
        domains=("github", "gitlab", "atlassian", "jira", "slack", "notion", "figma", "dropbox", "zoom", "google",
                 "microsoft", "apple", "vercel", "heroku", "sentry", "pagerduty", "docusign"),
        subject=r"alert|notification|security|verify|verification|password|sign-?in|log-?in|your account|reminder|"
                r"confirm your|\bcode\b|build (?:failed|passed)|pull request|issue #|new comment",
        body=r"verification code|one-time|security alert|do not reply|automated message|this is an automated"),
    "Work": dict(
        domains=(),
        subject=r"meeting|project|proposal|contract|client|agenda|sprint|stand-?up|roadmap|budget|\bq[1-4]\b|report|"
                r"review|deadline|schedule|\bsync\b|presentation|quarterly|launch|deliverable|status update|follow-?up|"
                r"1:1|hand-?off|onboarding|offsite|outage|incident|production",
        body=r"\b(?:meeting|project|deadline|deliverable|stakeholder|client|timeline|sprint|roadmap|quarterly)\b"),
    "Newsletter": dict(
        domains=(),
        subject=r"newsletter|digest|weekly|monthly|round-?up|issue #|\bedition\b|this week in|top stories|what's new|bulletin",
        body=r"$^"),
}
_COMPILED = {k: (re.compile(v["subject"], re.I), re.compile(v["body"], re.I)) for k, v in _RULES.items()}
_NEWSLETTER_LOCAL = re.compile(r"news|newsletter|digest|weekly|updates|marketing|offers|deals", re.I)
_GMAIL_LABEL_HINTS = {"CATEGORY_SOCIAL": ("Social", 3), "CATEGORY_UPDATES": ("Notifications", 1),
                      "CATEGORY_PROMOTIONS": ("Newsletter", 1), "CATEGORY_FORUMS": ("Social", 1),
                      "CATEGORY_PERSONAL": ("Personal", 1)}


def _domain_labels(email: str) -> set[str]:
    return set((email.split("@")[-1] if "@" in email else "").lower().split("."))


def _domain_hit(email: str, tokens: tuple[str, ...]) -> str | None:
    labels = _domain_labels(email)
    for t in tokens:
        if t in labels or (len(t) >= 6 and any(t in lab for lab in labels)):
            return t
    return None


def categorize(msg: MessageLike, ctx: AnalysisContext) -> CategoryResult:
    subject = msg.subject or ""
    body = (msg.body_text or msg.snippet or "")[:1500]
    sender = msg.sender_email or ""
    sent = bool(msg.is_sent)
    scores: dict[str, float] = {}
    why: dict[str, list[str]] = {}

    def add(cat: str, pts: float, reason: str) -> None:
        scores[cat] = scores.get(cat, 0) + pts
        why.setdefault(cat, []).append(reason)

    for cat, rule in _RULES.items():
        subj_rx, body_rx = _COMPILED[cat]
        if not sent and rule["domains"] and (hit := _domain_hit(sender, rule["domains"])):
            add(cat, 3, f"Sender domain looks like a {cat.lower()} sender ({hit})")
        if m := subj_rx.search(subject):
            add(cat, 2, f"Subject mentions \u201c{m.group(0)}\u201d")
        if cat != "Newsletter" and (m := body_rx.search(body)):
            add(cat, 1, f"Message text mentions \u201c{m.group(0)}\u201d")

    if not sent:
        if msg.list_unsubscribe:
            add("Newsletter", 3, "Has an unsubscribe header (typical of bulk mail)")
        elif msg.bulk:
            add("Newsletter", 2, "Marked as bulk/automated mail by the sender")
        if re.search(r"\bunsubscribe\b", body, re.I):
            add("Newsletter", 2, "Contains an unsubscribe link or notice")
        if _NEWSLETTER_LOCAL.search(sender.split("@")[0]):
            add("Newsletter", 1, "Sent from a newsletter-style address")
        if is_automated_address(sender):
            add("Notifications", 2, "Sent from an automated address")
        for label in msg.labels:
            if label in _GMAIL_LABEL_HINTS:
                cat, pts = _GMAIL_LABEL_HINTS[label]
                add(cat, pts, f"Gmail already filed it under {label.replace('CATEGORY_', '').title()}")
        udom, sdom = ctx.user_domain, sender.split("@")[-1].lower()
        if udom and udom not in FREE_MAIL and sdom == udom:
            add("Work", 3, "Sent from your organisation's domain")

    best, best_score = None, 0.0
    for cat in ("Finance", "Shopping", "Travel", "Social", "Notifications", "Work", "Newsletter", "Personal"):
        if scores.get(cat, 0) > best_score:
            best, best_score = cat, scores[cat]

    if best is None or best_score < 2:
        if sent:
            udom = ctx.user_domain
            rec_doms = {e.split("@")[-1].lower() for _, e in msg.to}
            best = "Work" if udom and udom not in FREE_MAIL and rec_doms == {udom} else "Personal"
            reasons = ["Written by you; no stronger signals found"]
        elif is_automated_address(sender) or msg.bulk or msg.list_unsubscribe:
            best, reasons = "Notifications", ["Automated sender with no stronger category signal"]
        else:
            best, reasons = "Personal", ["Written by a person and no other category matched"]
    else:
        reasons = why[best][:3]

    is_receipt = False
    if not sent:
        is_receipt = bool(RECEIPT_RE.search(subject)) or (
            best in ("Finance", "Shopping", "Travel") and bool(RECEIPT_RE.search(body[:600])))
    if is_receipt:
        reasons = reasons + ["Looks like a receipt, invoice or order confirmation"]
    return CategoryResult(category=best, reasons=reasons, is_receipt=is_receipt)
