import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from inboxpilot.analysis.signals import AnalysisContext  # noqa: E402
from inboxpilot.models import EmailMessage  # noqa: E402
from inboxpilot.services.context import AppContext  # noqa: E402
from inboxpilot.storage.secrets import MemoryBackend, SecretStore  # noqa: E402

ME = "me@work.example"
# Wednesday 7 Oct 2026, 09:00 UTC
REF = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
_counter = 0


def msg(subject="Hello", body="", *, sender="Pat Smith <pat@other.example>", to=None, cc=None, hours_ago=1.0, thread="t1",
        rid=None, unread=True, sent=False, labels=None, unsub=False, bulk=False):
    global _counter
    _counter += 1
    name, addr = parseaddr(sender)
    return EmailMessage(
        remote_id=rid or f"id-{_counter}", thread_id=thread, sender_name=name, sender_email=addr,
        to=to if to is not None else [("Me", ME)], cc=cc or [], subject=subject,
        date=datetime.now(timezone.utc) - timedelta(hours=hours_ago), snippet=body[:100], body_text=body,
        is_read=not unread, is_sent=sent, labels=(labels or []) + (["SENT"] if sent else []),
        list_unsubscribe=unsub, bulk=bulk or unsub)


def ctx(**kw):
    return AnalysisContext(user_email=ME, user_name="Me Myself", **kw)


def make_app(tmp: str | None = None) -> AppContext:
    d = tmp or tempfile.mkdtemp()
    return AppContext(secrets=SecretStore(MemoryBackend()), path=os.path.join(d, "t.db"))
