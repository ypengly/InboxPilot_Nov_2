"""Gmail provider using the official Gmail API and OAuth 2.0 (installed-app flow with PKCE).

Scope is gmail.readonly: the access token InboxPilot receives cannot send, delete, archive, label or edit mail.
Only the refresh token is stored, in the OS credential vault; the OAuth client file comes from the user (or the
packager). No passwords are ever requested.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..analysis.textutils import html_to_text, tidy
from ..config import GMAIL_SCOPES, app_home
from ..errors import AuthError, ConfigError, ProviderError
from ..models import EmailMessage
from ..storage.secrets import SecretStore
from .base import EmailProvider, check_cancel, register

log = logging.getLogger(__name__)
TOKEN_URI = "https://oauth2.googleapis.com/token"
BATCH_SIZE = 20
_RETRY_STATUS = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------------------------------------------
# Pure parsing helpers (unit-tested without network access)
# ---------------------------------------------------------------------------------------------------
def _decode_header_value(value: str) -> str:
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return (value or "").strip()


def _addresses(value: str) -> list[tuple[str, str]]:
    return [(_decode_header_value(name), addr.strip()) for name, addr in getaddresses([value or ""]) if addr]


def _b64(data: str, charset: str = "utf-8") -> str:
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    try:
        return raw.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def _charset(part: dict[str, Any]) -> str:
    for h in part.get("headers", []) or []:
        if h.get("name", "").lower() == "content-type" and "charset=" in h.get("value", "").lower():
            return h["value"].lower().split("charset=")[1].split(";")[0].strip(" \"'")
    return "utf-8"


def _walk(part: dict[str, Any], plain: list[str], html: list[str], flags: dict[str, bool]) -> None:
    mime = part.get("mimeType", "")
    if part.get("filename"):
        flags["attachment"] = True
        return
    body = part.get("body", {}) or {}
    if mime == "text/plain" and body.get("data"):
        plain.append(_b64(body["data"], _charset(part)))
    elif mime == "text/html" and body.get("data"):
        html.append(_b64(body["data"], _charset(part)))
    for sub in part.get("parts", []) or []:
        _walk(sub, plain, html, flags)


def parse_gmail_message(msg: dict[str, Any]) -> EmailMessage:
    payload = msg.get("payload", {}) or {}
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", []) if "name" in h}
    labels = list(msg.get("labelIds", []) or [])
    plain: list[str] = []
    html: list[str] = []
    flags = {"attachment": False}
    _walk(payload, plain, html, flags)
    body = tidy("\n\n".join(plain)) if plain else (html_to_text("\n".join(html)) if html else "")

    if msg.get("internalDate"):
        date = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
    else:
        try:
            date = parsedate_to_datetime(headers.get("date", "")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            date = datetime.now(timezone.utc)

    sender = _addresses(headers.get("from", ""))
    name, addr = sender[0] if sender else ("", "")
    auto = headers.get("auto-submitted", "no").strip().lower() not in ("", "no")
    bulk = headers.get("precedence", "").strip().lower() in ("bulk", "list", "junk") or auto or "list-id" in headers
    return EmailMessage(
        remote_id=msg["id"], thread_id=msg.get("threadId", ""), sender_name=name, sender_email=addr,
        to=_addresses(headers.get("to", "")), cc=_addresses(headers.get("cc", "")),
        subject=_decode_header_value(headers.get("subject", "")), date=date, snippet=msg.get("snippet", ""),
        body_text=body, is_read="UNREAD" not in labels, is_sent="SENT" in labels,
        has_attachments=flags["attachment"], labels=labels, list_unsubscribe="list-unsubscribe" in headers,
        bulk=bool(bulk), in_reply_to=headers.get("in-reply-to", ""))


def load_client_config(path: Path) -> dict[str, str]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read OAuth client file: {exc}",
                          user_message=f"The Google OAuth client file could not be read ({path}).") from exc
    block = data.get("installed") or data.get("web")
    if not block or "client_id" not in block:
        raise ConfigError("Not an OAuth client file",
                          user_message="That file is not a Google OAuth client JSON. Create a 'Desktop app' client "
                                       "in Google Cloud Console and download its JSON.")
    return {"client_id": block["client_id"], "client_secret": block.get("client_secret", "")}


def find_client_secrets(configured: str = "") -> Path | None:
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    if os.environ.get("INBOXPILOT_GMAIL_CLIENT_SECRETS"):
        candidates.append(Path(os.environ["INBOXPILOT_GMAIL_CLIENT_SECRETS"]))
    candidates.append(app_home() / "client_secret.json")
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path.cwd()
    candidates.append(exe_dir / "client_secret.json")
    return next((c for c in candidates if c.is_file()), None)


# ---------------------------------------------------------------------------------------------------
@register
class GmailProvider(EmailProvider):
    id = "gmail"
    display_name = "Gmail"

    def __init__(self, secrets: SecretStore, client_secrets_path: str = "", account_email: str = ""):
        self._secrets = secrets
        self._client_path = client_secrets_path
        self._email = account_email
        self._creds = None

    @classmethod
    def create(cls, *, secrets: SecretStore, client_secrets_path: str = "", account_email: str = "") -> "GmailProvider":
        return cls(secrets, client_secrets_path, account_email)

    @property
    def account_email(self) -> str:
        return self._email

    def _token_key(self, email: str | None = None) -> str:
        return f"gmail:{(email or self._email).lower()}:refresh_token"

    def _client_file(self) -> Path:
        path = find_client_secrets(self._client_path)
        if path is None:
            raise ConfigError(
                "No Google OAuth client file",
                user_message="Gmail needs a Google OAuth client file (client_secret.json). Open Settings and choose it, "
                             "or see the README section 'Connecting Gmail'.")
        return path

    def is_connected(self) -> bool:
        return bool(self._email) and bool(self._secrets.get(self._token_key()))

    # ---- OAuth -----------------------------------------------------------------------------------
    def connect(self) -> str:
        path = self._client_file()
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as exc:
            raise ConfigError("google-auth-oauthlib missing", user_message="Google sign-in libraries are not installed.") from exc
        flow = InstalledAppFlow.from_client_secrets_file(str(path), list(GMAIL_SCOPES))
        try:
            creds = flow.run_local_server(
                host="localhost", port=0, prompt="consent", timeout_seconds=300, open_browser=True,
                authorization_prompt_message="", success_message="InboxPilot is connected. You can close this tab.")
        except Exception as exc:
            raise AuthError(f"OAuth flow failed: {exc}",
                            user_message="Sign-in was cancelled or timed out. Nothing was changed.") from exc
        if not creds or not creds.refresh_token:
            raise AuthError("No refresh token returned",
                            user_message="Google did not return a long-lived sign-in. Remove InboxPilot at "
                                         "myaccount.google.com/permissions and try again.")
        self._creds = creds
        service = self._build(creds)
        profile = self._with_retries(lambda: service.users().getProfile(userId="me").execute(), None)
        email = profile.get("emailAddress", "")
        if not email:
            raise ProviderError("getProfile returned no email")
        self._secrets.set(self._token_key(email), creds.refresh_token)
        self._email = email
        return email

    def disconnect(self) -> None:
        token = self._secrets.get(self._token_key()) if self._email else None
        if token:  # best-effort revoke so the grant disappears from the Google account too
            try:
                req = urllib.request.Request(
                    "https://oauth2.googleapis.com/revoke", method="POST",
                    data=urllib.parse.urlencode({"token": token}).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                urllib.request.urlopen(req, timeout=10).close()
            except Exception as exc:
                log.info("Token revocation request failed (%s); local credentials are removed anyway.", exc)
        if self._email:
            self._secrets.delete(self._token_key())
        self._creds = None

    # ---- API plumbing ------------------------------------------------------------------------------
    def _credentials(self):
        if self._creds is not None and getattr(self._creds, "valid", False):
            return self._creds
        token = self._secrets.get(self._token_key()) if self._email else None
        if not token:
            raise AuthError("No stored token", user_message="You are signed out. Choose Account \u2192 Connect Gmail.")
        cfg = load_client_config(self._client_file())
        try:
            from google.auth.exceptions import RefreshError, TransportError
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
        except ImportError as exc:
            raise ConfigError("google-auth missing", user_message="Google libraries are not installed.") from exc
        creds = Credentials(token=None, refresh_token=token, token_uri=TOKEN_URI, client_id=cfg["client_id"],
                            client_secret=cfg["client_secret"], scopes=list(GMAIL_SCOPES))
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise AuthError(f"Refresh rejected: {exc}",
                            user_message="Google no longer accepts the saved sign-in (it may have been revoked). "
                                         "Please connect again.") from exc
        except TransportError as exc:
            raise ProviderError(f"Network error: {exc}", user_message="Could not reach Google. Check your connection.") from exc
        self._creds = creds
        return creds

    @staticmethod
    def _build(creds):
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise ConfigError("google-api-python-client missing", user_message="Gmail API library is not installed.") from exc
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    def _service(self):
        return self._build(self._credentials())  # service objects are not thread-safe: build per use

    @staticmethod
    def _reason(exc: Any) -> str:
        try:
            return json.loads(exc.content.decode())["error"]["message"]
        except Exception:
            return str(exc)

    def _with_retries(self, fn, cancel: threading.Event | None):
        try:
            import httplib2
            from googleapiclient.errors import HttpError
        except ImportError as exc:
            raise ConfigError("Gmail libraries missing", user_message="Gmail API libraries are not installed.") from exc
        net_errors: tuple = (OSError, httplib2.HttpLib2Error)
        delay = 1.0
        for _attempt in range(6):
            check_cancel(cancel)
            try:
                return fn()
            except HttpError as exc:
                status = getattr(exc.resp, "status", 0)
                reason = self._reason(exc)
                if status == 401:
                    raise AuthError(reason, user_message="Google rejected the sign-in. Please connect again.") from exc
                limited = status == 403 and any(w in reason.lower() for w in ("rate", "quota"))
                if status in _RETRY_STATUS or limited:
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                if status == 403 and "has not been used" in reason:
                    raise ProviderError(reason, user_message="The Gmail API is not enabled for your Google Cloud project. "
                                                             "Enable it in Google Cloud Console and try again.") from exc
                raise ProviderError(f"Gmail API error {status}: {reason}",
                                    user_message=f"Gmail returned an error ({status}): {reason}") from exc
            except net_errors:
                time.sleep(delay)
                delay = min(delay * 2, 30)
        raise ProviderError("Gmail kept failing", user_message="Gmail could not be reached after several attempts. Try again later.")

    # ---- EmailProvider API ---------------------------------------------------------------------------
    def list_ids(self, scope: str, since: datetime, cancel: threading.Event | None = None) -> list[str]:
        labels = {"inbox": ["INBOX"], "sent": ["SENT"], "unread": ["INBOX", "UNREAD"]}[scope]
        svc = self._service()
        ids: list[str] = []
        token = None
        while True:
            def call(token=token):
                return svc.users().messages().list(
                    userId="me", labelIds=labels, q=f"after:{int(since.timestamp())}", maxResults=500,
                    pageToken=token).execute()
            resp = self._with_retries(call, cancel)
            ids += [m["id"] for m in resp.get("messages", [])]
            token = resp.get("nextPageToken")
            if not token:
                return ids

    def fetch_messages(self, ids: Sequence[str], cancel: threading.Event | None = None) -> Iterator[EmailMessage]:
        svc = self._service()
        ids = list(ids)
        for i in range(0, len(ids), BATCH_SIZE):
            check_cancel(cancel)
            chunk = ids[i:i + BATCH_SIZE]
            results: dict[str, dict] = {}
            failed: list[str] = []

            def run_batch():
                results.clear()
                failed.clear()

                def cb(request_id, response, exception):
                    if exception is None:
                        results[request_id] = response
                    elif getattr(getattr(exception, "resp", None), "status", 0) != 404:  # 404: deleted meanwhile
                        failed.append(request_id)

                batch = svc.new_batch_http_request(callback=cb)
                for mid in chunk:
                    batch.add(svc.users().messages().get(userId="me", id=mid, format="full"), request_id=mid)
                batch.execute()

            self._with_retries(run_batch, cancel)
            for mid in failed:  # retry individually with backoff
                results[mid] = self._with_retries(
                    lambda mid=mid: svc.users().messages().get(userId="me", id=mid, format="full").execute(), cancel)
            for mid in chunk:
                if mid in results:
                    try:
                        yield parse_gmail_message(results[mid])
                    except Exception:
                        log.exception("Skipping message %s that could not be parsed", mid)
