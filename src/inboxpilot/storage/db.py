"""SQLite storage: schema, per-thread connections, repository queries."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from ..config import DEFAULT_SETTINGS
from ..errors import StorageError
from ..models import Draft, EmailMessage, EmailQuery, EmailRecord, Reason, Summary

SCHEMA_VERSION = 1
MAX_BODY_CHARS = 100_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts(
    id INTEGER PRIMARY KEY, provider TEXT NOT NULL, email TEXT NOT NULL,
    connected_at TEXT NOT NULL, last_sync_at TEXT, UNIQUE(provider, email));
CREATE TABLE IF NOT EXISTS emails(
    id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    remote_id TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '',
    sender_name TEXT, sender_email TEXT, to_json TEXT, cc_json TEXT, subject TEXT,
    date_utc TEXT NOT NULL, snippet TEXT, body_text TEXT,
    is_read INTEGER NOT NULL DEFAULT 1, is_sent INTEGER NOT NULL DEFAULT 0, has_attachments INTEGER NOT NULL DEFAULT 0,
    labels_json TEXT, list_unsubscribe INTEGER NOT NULL DEFAULT 0, bulk INTEGER NOT NULL DEFAULT 0, in_reply_to TEXT,
    analyzed_version INTEGER NOT NULL DEFAULT 0,
    auto_category TEXT NOT NULL DEFAULT '', category_reasons_json TEXT, is_receipt INTEGER NOT NULL DEFAULT 0,
    auto_priority TEXT NOT NULL DEFAULT 'Low', priority_score REAL NOT NULL DEFAULT 0, priority_reasons_json TEXT,
    auto_needs_reply INTEGER NOT NULL DEFAULT 0, awaiting_reply INTEGER NOT NULL DEFAULT 0,
    followup_reason TEXT, followup_evidence TEXT, answered INTEGER NOT NULL DEFAULT 0,
    user_category TEXT, user_priority TEXT, user_needs_reply INTEGER, user_follow_up INTEGER,
    UNIQUE(account_id, remote_id));
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(account_id, date_utc);
CREATE INDEX IF NOT EXISTS idx_emails_thread ON emails(account_id, thread_id);
CREATE TABLE IF NOT EXISTS summaries(
    email_id INTEGER PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE, engine TEXT NOT NULL,
    json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS drafts(
    email_id INTEGER PRIMARY KEY REFERENCES emails(id) ON DELETE CASCADE,
    subject TEXT, body TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_NEEDS_REPLY = "COALESCE(e.user_needs_reply, CASE WHEN e.auto_needs_reply=1 AND e.answered=0 THEN 1 ELSE 0 END)"
_FOLLOW_UP = ("COALESCE(e.user_follow_up, CASE WHEN e.is_sent=1 AND e.awaiting_reply=1 AND e.date_utc < :cutoff "
              "AND NOT EXISTS (SELECT 1 FROM emails o WHERE o.account_id=e.account_id AND o.thread_id<>'' "
              "AND o.thread_id=e.thread_id AND o.date_utc>e.date_utc) THEN 1 ELSE 0 END)")
_BASE = (f"SELECT e.*, COALESCE(e.user_category, e.auto_category) AS category, "
         f"COALESCE(e.user_priority, e.auto_priority) AS priority, "
         f"{_NEEDS_REPLY} AS needs_reply, {_FOLLOW_UP} AS follow_up FROM emails e WHERE e.account_id=:acct")

_FOLDER_WHERE = {
    "inbox": "is_sent=0",
    "important": "is_sent=0 AND (priority='High' OR category='Important')",
    "needs_reply": "is_sent=0 AND (needs_reply=1 OR category='Needs Reply')",
    "follow_up": "follow_up=1",
    "newsletters": "is_sent=0 AND category='Newsletter'",
    "receipts": "is_sent=0 AND is_receipt=1",
    "unread": "is_sent=0 AND is_read=0",
}
_SORTS = {
    "date": "date_utc",
    "sender": "LOWER(COALESCE(NULLIF(sender_name,''), sender_email))",
    "subject": "LOWER(subject)",
    "priority": "CASE priority WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END",
    "category": "category",
    "unread": "(1 - is_read)",
}
_OVERRIDE_FIELDS = {"user_category", "user_priority", "user_needs_reply", "user_follow_up"}


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def local_day_bounds(d: date) -> tuple[str, str]:
    """UTC ISO bounds [start, end) of a calendar day in the user's local timezone."""
    start = datetime.combine(d, time.min).astimezone()
    return iso(start), iso(start + timedelta(days=1))


def _like(text: str) -> str:
    esc = text.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._local = threading.local()
        self.fts = False
        self._init()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            try:
                c = sqlite3.connect(self.path, timeout=30)
            except sqlite3.Error as exc:
                raise StorageError(str(exc), user_message=f"Could not open the local database: {exc}") from exc
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = c
        return c

    def _init(self) -> None:
        c = self.conn()
        with c:
            c.executescript(_SCHEMA)
            try:
                c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5("
                          "subject, sender, body, tokenize='unicode61 remove_diacritics 2')")
                self.fts = True
            except sqlite3.OperationalError:
                self.fts = False  # SQLite built without FTS5: search falls back to LIKE
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ---- settings -------------------------------------------------------------------------------
    def get_setting(self, key: str) -> str:
        row = self.db.conn().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else DEFAULT_SETTINGS.get(key, "")

    def set_setting(self, key: str, value: str) -> None:
        with self.db.conn() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (key, str(value)))

    # ---- accounts -------------------------------------------------------------------------------
    def upsert_account(self, provider: str, email: str) -> int:
        with self.db.conn() as c:
            c.execute("INSERT OR IGNORE INTO accounts(provider,email,connected_at) VALUES(?,?,?)",
                      (provider, email, iso(datetime.now(timezone.utc))))
            return c.execute("SELECT id FROM accounts WHERE provider=? AND email=?", (provider, email)).fetchone()["id"]

    def get_account(self, provider: str, email: str):
        return self.db.conn().execute("SELECT * FROM accounts WHERE provider=? AND email=?", (provider, email)).fetchone()

    def touch_sync(self, account_id: int) -> None:
        with self.db.conn() as c:
            c.execute("UPDATE accounts SET last_sync_at=? WHERE id=?", (iso(datetime.now(timezone.utc)), account_id))

    def delete_account_data(self, account_id: int, delete_account_row: bool = True) -> None:
        with self.db.conn() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM emails WHERE account_id=?", (account_id,))]
            if self.db.fts:
                c.executemany("DELETE FROM emails_fts WHERE rowid=?", [(i,) for i in ids])
            c.execute("DELETE FROM emails WHERE account_id=?", (account_id,))  # cascades summaries and drafts
            if delete_account_row:
                c.execute("DELETE FROM accounts WHERE id=?", (account_id,))

    # ---- writing messages -------------------------------------------------------------------------
    def upsert_email(self, account_id: int, m: EmailMessage) -> tuple[int, bool]:
        """Insert a message or refresh its mutable state. Returns (row id, inserted)."""
        c = self.db.conn()
        with c:
            row = c.execute("SELECT id FROM emails WHERE account_id=? AND remote_id=?", (account_id, m.remote_id)).fetchone()
            if row:
                c.execute("UPDATE emails SET is_read=?, labels_json=? WHERE id=?",
                          (int(m.is_read), json.dumps(m.labels), row["id"]))
                return row["id"], False
            body = (m.body_text or "")[:MAX_BODY_CHARS]
            cur = c.execute(
                "INSERT INTO emails(account_id,remote_id,thread_id,sender_name,sender_email,to_json,cc_json,subject,date_utc,"
                "snippet,body_text,is_read,is_sent,has_attachments,labels_json,list_unsubscribe,bulk,in_reply_to) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (account_id, m.remote_id, m.thread_id or "", m.sender_name, m.sender_email,
                 json.dumps([list(a) for a in m.to]), json.dumps([list(a) for a in m.cc]), m.subject, iso(m.date),
                 m.snippet, body, int(m.is_read), int(m.is_sent), int(m.has_attachments), json.dumps(m.labels),
                 int(m.list_unsubscribe), int(m.bulk), m.in_reply_to))
            rid = cur.lastrowid
            if self.db.fts:
                c.execute("INSERT INTO emails_fts(rowid,subject,sender,body) VALUES(?,?,?,?)",
                          (rid, m.subject or "", f"{m.sender_name} {m.sender_email}", f"{m.snippet}\n{body}"))
            return rid, True

    def known_remote_ids(self, account_id: int) -> set[str]:
        return {r["remote_id"] for r in self.db.conn().execute("SELECT remote_id FROM emails WHERE account_id=?", (account_id,))}

    def set_read_states(self, account_id: int, unread_ids: set[str], inbox_ids: set[str]) -> None:
        """Mirror the server's read/unread state for incoming messages we already have."""
        c = self.db.conn()
        with c:
            rows = c.execute("SELECT id, remote_id, is_read FROM emails WHERE account_id=? AND is_sent=0", (account_id,)).fetchall()
            for r in rows:
                if r["remote_id"] not in inbox_ids:
                    continue
                want = 0 if r["remote_id"] in unread_ids else 1
                if want != r["is_read"]:
                    c.execute("UPDATE emails SET is_read=? WHERE id=?", (want, r["id"]))

    def prune_missing_inbox(self, account_id: int, inbox_ids: set[str], since: datetime) -> int:
        """Remove cached incoming mail that is no longer in the server inbox (archived/deleted elsewhere).
        Messages with a saved draft are kept so no user work is lost."""
        c = self.db.conn()
        with c:
            rows = c.execute(
                "SELECT id, remote_id FROM emails WHERE account_id=? AND is_sent=0 AND date_utc>=? "
                "AND id NOT IN (SELECT email_id FROM drafts)", (account_id, iso(since))).fetchall()
            gone = [r["id"] for r in rows if r["remote_id"] not in inbox_ids]
            for i in range(0, len(gone), 500):
                chunk = gone[i:i + 500]
                marks = ",".join("?" * len(chunk))
                if self.db.fts:
                    c.execute(f"DELETE FROM emails_fts WHERE rowid IN ({marks})", chunk)
                c.execute(f"DELETE FROM emails WHERE id IN ({marks})", chunk)
            return len(gone)

    def resolve_threads(self, account_id: int) -> None:
        with self.db.conn() as c:
            c.execute(
                "UPDATE emails SET answered = CASE WHEN EXISTS (SELECT 1 FROM emails s WHERE s.account_id=emails.account_id "
                "AND s.is_sent=1 AND s.thread_id<>'' AND s.thread_id=emails.thread_id AND s.date_utc>emails.date_utc) "
                "THEN 1 ELSE 0 END WHERE account_id=? AND is_sent=0", (account_id,))

    def known_contacts(self, account_id: int) -> set[str]:
        out: set[str] = set()
        for r in self.db.conn().execute("SELECT to_json, cc_json FROM emails WHERE account_id=? AND is_sent=1", (account_id,)):
            for col in ("to_json", "cc_json"):
                try:
                    out.update(a[1].lower() for a in json.loads(r[col] or "[]") if len(a) == 2)
                except ValueError:
                    pass
        return out

    def participated_threads(self, account_id: int) -> set[str]:
        return {r["thread_id"] for r in self.db.conn().execute(
            "SELECT DISTINCT thread_id FROM emails WHERE account_id=? AND is_sent=1 AND thread_id<>''", (account_id,))}

    # ---- analysis ---------------------------------------------------------------------------------
    def unanalyzed(self, account_id: int, version: int, cutoff: str) -> list[EmailRecord]:
        sql = f"SELECT * FROM ({_BASE}) WHERE analyzed_version<>:v"
        rows = self.db.conn().execute(sql, {"acct": account_id, "cutoff": cutoff, "v": version}).fetchall()
        return [EmailRecord.from_row(r) for r in rows]

    def save_analysis(self, email_id: int, version: int, *, category: str, category_reasons: list[str], is_receipt: bool,
                      priority: str, score: float, reasons: list[Reason], needs_reply: bool, awaiting_reply: bool,
                      followup_reason: str, followup_evidence: str) -> None:
        with self.db.conn() as c:
            c.execute(
                "UPDATE emails SET analyzed_version=?, auto_category=?, category_reasons_json=?, is_receipt=?, auto_priority=?, "
                "priority_score=?, priority_reasons_json=?, auto_needs_reply=?, awaiting_reply=?, followup_reason=?, "
                "followup_evidence=? WHERE id=?",
                (version, category, json.dumps(category_reasons), int(is_receipt), priority, score,
                 json.dumps([[r.text, int(r.positive)] for r in reasons]), int(needs_reply), int(awaiting_reply),
                 followup_reason, followup_evidence, email_id))

    def set_override(self, email_id: int, field: str, value) -> None:
        if field not in _OVERRIDE_FIELDS:
            raise ValueError(f"Not an override field: {field}")
        if isinstance(value, bool):
            value = int(value)
        with self.db.conn() as c:
            c.execute(f"UPDATE emails SET {field}=? WHERE id=?", (value, email_id))

    def clear_overrides(self, email_id: int) -> None:
        with self.db.conn() as c:
            c.execute("UPDATE emails SET user_category=NULL, user_priority=NULL, user_needs_reply=NULL, "
                      "user_follow_up=NULL WHERE id=?", (email_id,))

    # ---- reading ----------------------------------------------------------------------------------
    @staticmethod
    def _cutoff(follow_up_days: int) -> str:
        return iso(datetime.now(timezone.utc) - timedelta(days=follow_up_days))

    def get_email(self, account_id: int, email_id: int, follow_up_days: int = 3) -> EmailRecord | None:
        sql = f"SELECT * FROM ({_BASE}) WHERE id=:id"
        r = self.db.conn().execute(sql, {"acct": account_id, "cutoff": self._cutoff(follow_up_days), "id": email_id}).fetchone()
        return EmailRecord.from_row(r) if r else None

    @staticmethod
    def _fts_expr(text: str) -> str:
        return " ".join(f'"{t}"*' for t in re.findall(r"\w+", text, re.UNICODE))

    def _where(self, q: EmailQuery, params: dict) -> list[str]:
        where = [_FOLDER_WHERE.get(q.folder, "1=1")]
        if q.category:
            where.append("category=:cat"); params["cat"] = q.category
        if q.priority:
            where.append("priority=:prio"); params["prio"] = q.priority
        if q.unread_only:
            where.append("is_read=0")
        if q.date_from:
            where.append("date_utc>=:dfrom"); params["dfrom"] = local_day_bounds(q.date_from)[0]
        if q.date_to:
            where.append("date_utc<:dto"); params["dto"] = local_day_bounds(q.date_to)[1]
        if q.sender.strip():
            where.append("(LOWER(sender_email) LIKE :snd ESCAPE '\\' OR LOWER(sender_name) LIKE :snd ESCAPE '\\')")
            params["snd"] = _like(q.sender)
        text = q.text.strip()
        if text:
            expr = self._fts_expr(text) if self.db.fts else ""
            if expr:
                where.append("id IN (SELECT rowid FROM emails_fts WHERE emails_fts MATCH :fts)")
                params["fts"] = expr
            else:
                params["txt"] = _like(text)
                where.append("(LOWER(subject) LIKE :txt ESCAPE '\\' OR LOWER(sender_name) LIKE :txt ESCAPE '\\' "
                             "OR LOWER(sender_email) LIKE :txt ESCAPE '\\' OR LOWER(body_text) LIKE :txt ESCAPE '\\' "
                             "OR LOWER(snippet) LIKE :txt ESCAPE '\\')")
        return where

    def search(self, account_id: int, q: EmailQuery, follow_up_days: int = 3) -> list[EmailRecord]:
        params: dict = {"acct": account_id, "cutoff": self._cutoff(follow_up_days)}
        where = self._where(q, params)
        order = _SORTS.get(q.sort_by, _SORTS["date"])
        direction = "DESC" if q.descending else "ASC"
        sql = (f"SELECT * FROM ({_BASE}) WHERE {' AND '.join(where)} "
               f"ORDER BY {order} {direction}, date_utc DESC, id DESC LIMIT :lim")
        params["lim"] = max(1, min(int(q.limit), 10000))
        try:
            rows = self.db.conn().execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            raise StorageError(str(exc), user_message=f"That search could not be run: {exc}") from exc
        return [EmailRecord.from_row(r) for r in rows]

    def count(self, account_id: int, folder: str, follow_up_days: int = 3) -> int:
        params: dict = {"acct": account_id, "cutoff": self._cutoff(follow_up_days)}
        sql = f"SELECT COUNT(*) AS n FROM ({_BASE}) WHERE {_FOLDER_WHERE[folder]}"
        return self.db.conn().execute(sql, params).fetchone()["n"]

    def folder_counts(self, account_id: int, follow_up_days: int = 3) -> dict[str, int]:
        return {f: self.count(account_id, f, follow_up_days) for f in _FOLDER_WHERE}

    def stats(self, account_id: int, follow_up_days: int = 3) -> dict[str, int]:
        counts = self.folder_counts(account_id, follow_up_days)
        recent_from = iso(datetime.now(timezone.utc) - timedelta(days=7))
        recent = self.db.conn().execute(
            "SELECT COUNT(*) AS n FROM emails WHERE account_id=? AND is_sent=0 AND date_utc>=?", (account_id, recent_from)).fetchone()["n"]
        return {"unread": counts["unread"], "important": counts["important"], "pending_replies": counts["needs_reply"],
                "recent": recent, "total": counts["inbox"]}

    # ---- summaries & drafts -----------------------------------------------------------------------
    def save_summary(self, email_id: int, s: Summary) -> None:
        with self.db.conn() as c:
            c.execute("INSERT INTO summaries(email_id,engine,json,created_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(email_id) DO UPDATE SET engine=excluded.engine, json=excluded.json, created_at=excluded.created_at",
                      (email_id, s.engine, json.dumps(asdict(s)), iso(datetime.now(timezone.utc))))

    def get_summary(self, email_id: int) -> Summary | None:
        r = self.db.conn().execute("SELECT json FROM summaries WHERE email_id=?", (email_id,)).fetchone()
        if not r:
            return None
        try:
            return Summary(**json.loads(r["json"]))
        except (TypeError, ValueError):
            return None

    def save_draft(self, email_id: int, subject: str, body: str) -> None:
        with self.db.conn() as c:
            c.execute("INSERT INTO drafts(email_id,subject,body,updated_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(email_id) DO UPDATE SET subject=excluded.subject, body=excluded.body, updated_at=excluded.updated_at",
                      (email_id, subject, body, iso(datetime.now(timezone.utc))))

    def get_draft(self, email_id: int) -> Draft | None:
        r = self.db.conn().execute("SELECT subject, body FROM drafts WHERE email_id=?", (email_id,)).fetchone()
        return Draft(subject=r["subject"] or "", body=r["body"], engine="saved") if r else None

    def draft_count(self, account_id: int) -> int:
        return self.db.conn().execute(
            "SELECT COUNT(*) AS n FROM drafts d JOIN emails e ON e.id=d.email_id WHERE e.account_id=?", (account_id,)).fetchone()["n"]
