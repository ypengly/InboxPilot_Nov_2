"""Paths and constants."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "InboxPilot"
KEYRING_SERVICE = "InboxPilot"

# Read-only scope: InboxPilot cannot send, delete, archive, label or modify mail,
# because the token it holds does not allow it.
GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)

# Bump when the analysis rules change; all messages are re-analysed on next sync.
ANALYSIS_VERSION = 1

DEFAULT_SETTINGS: dict[str, str] = {
    "sync_days": "90",
    "follow_up_days": "3",
    "gmail_client_secrets_path": "",
    "ai_enabled": "0",
    "user_name": "",
    "ai_model": "claude-sonnet-5-5",
    "active_provider": "",
    "active_account": "",
}


def app_home() -> Path:
    override = os.environ.get("INBOXPILOT_HOME")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return app_home() / "inboxpilot.db"


def log_path() -> Path:
    return app_home() / "inboxpilot.log"
