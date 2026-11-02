"""Text cleanup helpers: HTML -> text, quoted-reply stripping, sentence splitting."""
from __future__ import annotations

import re
from html.parser import HTMLParser

_SKIP_TAGS = {"script", "style", "head", "title"}
_BLOCK_TAGS = {"p", "div", "br", "tr", "li", "ul", "ol", "table", "blockquote", "section",
               "article", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "header", "footer"}


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_text(markup: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # malformed HTML: fall back to a crude tag strip
        return tidy(re.sub(r"<[^>]+>", " ", markup))
    return tidy("".join(parser.parts))


_REPLY_HEADER = re.compile(r"\n\s*On [^\n]{5,200}(?:\n[^\n]{0,100})?\bwrote:\s*(?:\n|$)", re.I)
_ORIGINAL = re.compile(r"\n\s*(?:-{2,}\s*(?:Original Message|Forwarded message)\s*-{2,}|_{10,}\s*\n\s*From:)", re.I)
_SIG_DELIM = re.compile(r"\n-- ?\n")
_SENT_FROM = re.compile(r"^\s*(?:Sent from my [^\n]+|Get Outlook for [^\n]+)\s*$", re.I | re.M)


def strip_quoted(text: str) -> str:
    """Remove quoted earlier messages, signature delimiter blocks and mobile footers."""
    text = "\n" + tidy(text)
    for rx in (_REPLY_HEADER, _ORIGINAL, _SIG_DELIM):
        m = rx.search(text)
        if m:
            text = text[: m.start()]
    lines = [ln for ln in text.split("\n") if not ln.lstrip().startswith(">")]
    text = "\n".join(lines)
    text = _SENT_FROM.sub("", text)
    return tidy(text)


_GREETING = re.compile(r"^\s*(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening)|greetings)\b[^\n]{0,60}[,:!]?\s*$", re.I)
_SIGNOFF = re.compile(r"^\s*(?:thanks|thank you|thanks again|many thanks|best|best regards|kind regards|regards|warm regards|"
                      r"cheers|sincerely|yours|yours sincerely|talk soon|take care|best wishes|all the best)\b[^\n]{0,40}[,!.]?\s*$", re.I)
_DISCLAIMER = re.compile(r"(this (?:e-?mail|message) (?:and any attachments )?(?:is|are|may be) (?:confidential|intended)|"
                         r"if you (?:are not|aren't) the intended recipient|you are receiving this (?:e-?mail|message) because|"
                         r"to unsubscribe|unsubscribe|view (?:this )?(?:e-?mail )?in (?:your )?browser|\u00a9|all rights reserved)", re.I)


def strip_boilerplate(text: str) -> str:
    """Drop greeting line, sign-off block and footer disclaimers (for summaries)."""
    lines = text.split("\n")
    out: list[str] = []
    for i, ln in enumerate(lines):
        if _DISCLAIMER.search(ln):
            break
        if i < 2 and _GREETING.match(ln):
            continue
        if _SIGNOFF.match(ln) and i > 0:
            break  # sign-off: everything after is signature
        out.append(ln)
    return tidy("\n".join(out))


_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*\u2022]\s+", "", line)
        for part in _SENT_SPLIT.split(line):
            part = part.strip()
            if len(part) >= 3:
                sentences.append(part)
    return sentences


def shorten(text: str, limit: int = 180) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def first_name(name_or_email: str) -> str:
    name = (name_or_email or "").strip()
    if "@" in name and " " not in name:
        name = name.split("@")[0]
        name = re.split(r"[._\-+]", name)[0]
    name = name.split(",")[-1].strip() if "," in name else name
    part = name.split()[0] if name.split() else ""
    return part[:1].upper() + part[1:] if part else ""
