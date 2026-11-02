"""Template-based reply drafts. Anything only the user can know is left as a [placeholder]."""
from __future__ import annotations

from ..models import Draft
from .signals import AnalysisContext, MessageLike, Signals, extract_signals
from .textutils import first_name, shorten

TONES = ("Professional", "Friendly", "Brief")
_OPEN = {"Professional": "Thank you for your email.", "Friendly": "Thanks so much for your message!", "Brief": "Thanks for your message."}
_CLOSE = {"Professional": "Kind regards,", "Friendly": "Cheers,", "Brief": "Thanks,"}


def reply_subject(subject: str) -> str:
    s = (subject or "").strip()
    return s if s.lower().startswith("re:") else f"Re: {s}" if s else "Re:"


def generate_local_draft(msg: MessageLike, ctx: AnalysisContext, tone: str = "Professional",
                         sig: Signals | None = None) -> Draft:
    tone = tone if tone in TONES else "Professional"
    sig = sig or extract_signals(msg, ctx)
    who = first_name(msg.sender_name or msg.sender_email)
    lines = [f"Hi {who}," if who else "Hello,", "", _OPEN[tone], ""]
    if sig.questions:
        for q in sig.questions[:3]:
            lines.append(f"On your question \u2014 \u201c{shorten(q, 140)}\u201d \u2014 [add your answer here].")
    elif sig.requests:
        lines.append(f"About \u201c{shorten(sig.requests[0], 140)}\u201d: [say what you can do and when].")
    else:
        lines.append("[Write your reply here.]")
    if sig.deadline:
        lines += ["", f"I noted the timing you mentioned (\u201c{sig.deadline.matched}\u201d). [Confirm whether you can meet it.]"]
    lines += ["", _CLOSE[tone], ctx.user_name or "[your name]"]
    return Draft(subject=reply_subject(msg.subject), body="\n".join(lines), engine="local")
