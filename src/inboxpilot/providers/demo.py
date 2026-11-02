"""Offline demo mailbox so the app can be explored without connecting a real account.
All names, companies and addresses are fictional (.example domains)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Iterator, Sequence

from ..models import EmailMessage
from ..storage.secrets import SecretStore
from .base import EmailProvider, check_cancel, register

DEMO_USER = "alex.morgan@northwind.example"
DEMO_NAME = "Alex Morgan"
_ME = (DEMO_NAME, DEMO_USER)


def _build(now: datetime) -> list[EmailMessage]:
    def d(hours: float) -> datetime:
        return now - timedelta(hours=hours)

    soon = (now + timedelta(days=5)).strftime("%B %d").replace(" 0", " ")
    msgs: list[EmailMessage] = []

    def inc(i, hrs, name, addr, subject, body, *, unread=True, to=None, cc=None, thread=None, labels=None, unsub=False, bulk=False):
        msgs.append(EmailMessage(
            remote_id=f"demo-{i:02d}", thread_id=thread or f"thr-{i:02d}", sender_name=name, sender_email=addr,
            to=to if to is not None else [_ME], cc=cc or [], subject=subject, date=d(hrs), snippet=body[:120].replace("\n", " "),
            body_text=body, is_read=not unread, is_sent=False, labels=(labels or []) + (["UNREAD"] if unread else []),
            list_unsubscribe=unsub, bulk=bulk or unsub))

    def sent(i, hrs, to, subject, body, thread):
        msgs.append(EmailMessage(
            remote_id=f"demo-{i:02d}", thread_id=thread, sender_name=DEMO_NAME, sender_email=DEMO_USER, to=[to], cc=[],
            subject=subject, date=d(hrs), snippet=body[:120].replace("\n", " "), body_text=body, is_read=True,
            is_sent=True, labels=["SENT"]))

    inc(1, 2, "Dana Whitfield", "dana.whitfield@northwind.example", "Project Atlas: can you review the launch plan?",
        "Hi Alex,\n\nI've attached the latest launch plan for Project Atlas. Could you review sections 3 and 4 and let me know "
        "if the budget numbers look right? We need sign-off by Friday 5pm so Priya can book the vendor.\n\n"
        "Also, are you free for a 15-minute sync on Thursday?\n\nThanks,\nDana")
    inc(2, 5, "Priya Nair", "priya.nair@northwind.example", "Re: Atlas vendor booking",
        "FYI - the vendor call is booked for next Tuesday at 10am. Dana is handling the contract. No action needed from you.\n\nBest,\nPriya",
        to=[("Dana Whitfield", "dana.whitfield@northwind.example")], cc=[_ME], unread=False)
    inc(3, 20, "Marcus Lee", "marcus.lee@clientcorp.example", "Contract renewal - question about pricing",
        "Hello Alex,\n\nWe're reviewing the renewal terms with our finance team. Does the 10% volume discount still apply if we add "
        "two more seats? Please get back to me by next Wednesday so we can finalise the paperwork.\n\nRegards,\nMarcus Lee")
    inc(4, 8, "The Weekly Byte", "news@weeklybyte.example", "The Weekly Byte #212: Why small teams ship faster",
        "This week: five habits of fast-shipping teams, a deep dive into SQLite, and the best tools we tried.\n\n"
        "Read online | Unsubscribe | Manage preferences", unsub=True, labels=["CATEGORY_PROMOTIONS"])
    inc(5, 30, "Morning Brief", "digest@morningbrief.example", "Your Morning Brief for Friday",
        "Top stories today: markets open flat, new transit plan unveiled, and weather outlook for the weekend.\n\n"
        "To unsubscribe from this digest, click here.", unsub=True, unread=False)
    inc(6, 26, "Amazon", "no-reply@amazon.example", "Your order #112-4839201 has shipped",
        "Hello Alex, your package is on its way. Tracking number: TBA1234567890. Estimated delivery: in 2 days.\n"
        "Order total: $38.99. Thank you for shopping with us.", unread=False)
    inc(7, 50, "Stripe", "receipts@stripe.example", "Your receipt from Cloudflare Inc. [#2381-4471]",
        "Receipt #2381-4471\nAmount paid: $20.00\nDate paid: this week\nPayment method: Visa ending in 4417\n"
        "Thanks for your payment.", unread=False)
    inc(8, 70, "Chase Alerts", "alerts@chase.example", "Your monthly statement is ready",
        "Your statement for the period is now available. Sign in to view your balance and transactions.", unread=False)
    inc(9, 12, "Utility Power Billing", "billing@utilitypower.example", "Payment failed for your invoice",
        f"Your payment of $84.20 was declined. Your account will be suspended unless payment is received by {soon}. "
        "Please update your payment method.")
    inc(10, 100, "Skyway Airlines", "bookings@skyway-airlines.example", "Your itinerary: SFO to NRT",
        "Booking confirmation ABC123. Departure: Nov 14, 11:20. Arrival: Nov 15, 15:05. Check-in opens 24 hours before departure.",
        unread=False)
    inc(11, 6, "Airbnb", "automated@airbnb.example", "Reservation reminder: check-in is tomorrow",
        "Your stay in Kyoto starts tomorrow. Check-in is after 3pm. Your host has sent directions.")
    inc(12, 40, "LinkedIn", "notifications@linkedin.example", "Sam Rivera mentioned you in a post",
        "Sam Rivera mentioned you in a post about open-source maintainers. View on LinkedIn.", unread=False, bulk=True)
    inc(13, 9, "GitHub", "notifications@github.example", "[northwind/atlas] Pull request #482: Fix CSV export",
        "jkim opened a pull request. Review requested. Reply to this email directly or view it on GitHub.", bulk=True)
    inc(14, 3, "Google", "no-reply@accounts.google.example", "Security alert: new sign-in on Windows",
        "Your account was just signed in to from a new Windows device. If this was you, you don't need to do anything.")
    inc(15, 28, "Jenny Morgan", "jenny.morgan@mail.example", "Dinner on Sunday?",
        "Hi sweetheart,\n\nAre you coming for dinner on Sunday? Dad wants to know if he should make the lasagna. Let me know!\n\nLove,\nMum")
    inc(16, 60, "Tom Becker", "tom.becker@mail.example", "Photos from the trip",
        "Here are the photos from last weekend. Had such a great time!\n\nTom", unread=False)
    inc(17, 10, "HR Team", "hr@northwind.example", "Reminder: submit your timesheet",
        "Hello everyone,\n\nPlease submit your timesheet by end of day Friday so payroll can run on time.\n\nThanks,\nHR",
        to=[("All Staff", "all-staff@northwind.example")])
    inc(18, 4, "Priya Nair", "priya.nair@northwind.example", "Quick question on the Q4 budget",
        "Hi Alex,\n\nDo you have the Q4 budget spreadsheet handy? Can you send it over when you get a chance?\n\nThanks,\nPriya")
    inc(19, 150, "Jordan Kim", "jordan.kim@northwind.example", "Interview feedback needed",
        "Hi Alex,\n\nCould you share your feedback on Tuesday's candidate? HR is waiting on our decision.\n\nJordan",
        thread="thr-19", unread=False)
    sent(20, 140, ("Jordan Kim", "jordan.kim@northwind.example"), "Re: Interview feedback needed",
         "Hi Jordan,\n\nStrong on systems design, a bit light on testing. I'd recommend moving forward.\n\nAlex", "thr-19")
    sent(21, 24 * 6, ("Elena Rossi", "elena.rossi@designstudio.example"), "Logo files for the Atlas launch",
         "Hi Elena,\n\nCould you send me the final logo files in SVG format? I need them for the launch page.\n\nThanks,\nAlex", "thr-21")
    sent(22, 20, ("Samira Haddad", "samira.haddad@northwind.example"), "Design review on Tuesday?",
         "Hi Samira,\n\nAre we still on for Tuesday's design review?\n\nAlex", "thr-22")
    inc(23, 14, "Nimbus Outdoor", "deals@nimbusoutdoor.example", "48 hours only: 30% off everything",
        "Our biggest sale of the year. Free shipping on orders over $50. Shop now! Unsubscribe", unsub=True, unread=False)
    inc(24, 1, "Ravi Patel", "ravi.patel@clientcorp.example", "URGENT: nightly export failing",
        "Hi Alex,\n\nOur nightly export has failed three nights in a row and we need this fixed immediately. "
        "Can you tell me who is working on it? We have a board report due tomorrow.\n\nRavi",
        cc=[("Marcus Lee", "marcus.lee@clientcorp.example")])
    return msgs


@register
class DemoProvider(EmailProvider):
    id = "demo"
    display_name = "Demo mailbox"
    is_demo = True

    def __init__(self) -> None:
        self._connected = False
        self._messages = {m.remote_id: m for m in _build(datetime.now(timezone.utc))}

    @classmethod
    def create(cls, *, secrets: SecretStore, client_secrets_path: str = "", account_email: str = "") -> "DemoProvider":
        p = cls()
        p._connected = bool(account_email)
        return p

    @property
    def account_email(self) -> str:
        return DEMO_USER if self._connected else ""

    def connect(self) -> str:
        self._connected = True
        return DEMO_USER

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def list_ids(self, scope: str, since: datetime, cancel: threading.Event | None = None) -> list[str]:
        def keep(m: EmailMessage) -> bool:
            if m.date < since:
                return False
            return {"inbox": not m.is_sent, "sent": m.is_sent, "unread": (not m.is_sent and not m.is_read)}[scope]
        return [m.remote_id for m in sorted(self._messages.values(), key=lambda m: m.date, reverse=True) if keep(m)]

    def fetch_messages(self, ids: Sequence[str], cancel: threading.Event | None = None) -> Iterator[EmailMessage]:
        for mid in ids:
            check_cancel(cancel)
            if mid in self._messages:
                yield self._messages[mid]
