# InboxPilot 📧

A desktop email command center. It connects to your mailbox through the provider's official API and OAuth, downloads
recent mail into a local SQLite database, and helps you see what is **important**, what **needs a reply**, what you are
**waiting on**, and what you can ignore (newsletters, receipts, notifications).

**It is read-only.** InboxPilot never sends, deletes, archives, labels or edits email. Drafts are text you can edit,
copy, or save *on your computer* — you send them yourself from your normal mail app.

## Features

| Area | What it does |
|---|---|
| Dashboard | Folders: Inbox, Important, Needs Reply, Follow Up, Newsletters, Receipts, Unread. Stat cards: unread, important, pending replies, received in last 7 days. |
| Email list | Sender, subject, date, category, priority, unread marker. Full-text search (SQLite FTS5), sender filter, category/priority filters, date ranges (today / 7 / 30 days / custom), sort by date, sender, subject, priority, category or unread. |
| Summaries | Summary, Key Points, Action Items, Deadline, People Mentioned, Suggested Response. Built only from sentences in the email. If no deadline is written in the email it says **No deadline identified.** |
| Categories | Work, Personal, Finance, Shopping, Newsletter, Social, Travel, Notifications (+ manual Important / Needs Reply). Each suggestion shows *why*. You can change any category. |
| Priority | High / Medium / Low with the reasons (✓ directly addressed, ✓ contains a requested action, ✓ mentions a deadline, • bulk mail …). You can override it, and reset to automatic. |
| Follow-up detection | **Needs Reply**: a question or request addressed to you, not already answered in the thread, with the quoted evidence. **Follow Up**: mail *you* sent that asked something and got no reply after N days (default 3), plus anything you flag manually. |
| Draft assistant | Generates an editable reply with `[placeholders]` for anything only you can know. Buttons: **Edit**, **Copy**, **Save Draft**. Never sent automatically. |
| Privacy | OAuth only; refresh token in the OS credential store; Connect / Disconnect (optionally deleting all local data). |
| Optional AI | Off by default. Anthropic API for summaries/drafts on one selected email, only when you click. Output is validated (e.g. an invented deadline is discarded). Falls back to local analysis on any failure. |

## Quick start (development)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python run_inboxpilot.py
```

Click **Explore with a demo mailbox** to try everything without connecting an account (all data is fictional).

## Connecting Gmail

Gmail's API requires an OAuth client that belongs to a Google Cloud project. One-time setup:

1. Go to <https://console.cloud.google.com/>, create a project, and **enable the Gmail API**.
2. *APIs & Services → OAuth consent screen*: choose *External*, add your own address under **Test users**.
3. *Credentials → Create credentials → OAuth client ID → Application type: Desktop app*. Download the JSON.
4. In InboxPilot: **Settings → Gmail OAuth client file → Browse…** and pick that JSON
   (or save it as `client_secret.json` in the InboxPilot data folder, or next to `InboxPilot.exe`, or set
   `INBOXPILOT_GMAIL_CLIENT_SECRETS`).
5. **Connect Gmail**. Your browser opens Google's own sign-in page; approve the single permission
   *"View your email messages and settings"* (`gmail.readonly`).

Notes
* While the app is in Google's *Testing* mode, refresh tokens expire after 7 days and you will simply reconnect.
* `gmail.readonly` is a Google **restricted scope**. Personal use with your own project is fine; distributing the app to
  other people requires your own OAuth client plus Google's app verification (and, for restricted scopes, a security
  assessment). That is a Google policy requirement, not something the app can work around.

## Privacy & security model

* **No passwords.** There is no IMAP/SMTP code; sign-in happens in your browser on the provider's page.
* **Read-only token.** The only Gmail scope requested is `gmail.readonly`; the token cannot modify mail even if the app
  were buggy. Tests assert that the Gmail provider only calls `messages.list`, `messages.get` and `getProfile`.
* **Secure token storage.** Only the OAuth *refresh token* is stored, via `keyring` → Windows Credential Manager /
  macOS Keychain / Secret Service. If no secure backend exists the token is kept in memory for the session only —
  never written to disk in plain text.
* **Disconnect.** *Account → Disconnect* deletes the stored token, asks Google to revoke it, and (checkbox, on by default)
  deletes all downloaded mail, summaries and drafts.
* **Local cache.** Message text is stored in `inboxpilot.db` (`%APPDATA%\InboxPilot` on Windows). InboxPilot does **not**
  encrypt this file; use full-disk encryption such as BitLocker. Logs contain IDs, counts and error text only.
* **AI is opt-in.** Nothing leaves your computer except Google API traffic, unless you enable AI in Settings *and* click
  *Summarize with AI* / tick *Use AI* for a draft. Then the text of that one email is sent to the Anthropic API. The model is
  instructed to use only the email's content and to treat it as data, not instructions.
* **Reading ≠ marking read.** Opening a message in InboxPilot does not change its read state in your mailbox.

## How the analysis works (and its limits)

Analysis is deterministic rules, not a black box. That makes it explainable, but it will sometimes be wrong — which is why
every label has a reason and every label can be overridden. Overrides are stored separately from automatic values, so
re-syncing never undoes your choices.

* *Needs Reply* skips newsletters, automated senders, mail where you are only CC'd, and threads where you already replied.
* *Deadlines* are only reported if a phrase like "by Friday 5pm" or "due March 14" is literally in the message. Relative
  phrases are interpreted relative to the date the email was sent and labelled as such; ambiguous dates like `10/12` are
  not guessed.
* *Receipts* include invoices and order confirmations.
* Local summaries are extractive (they quote sentences from the email), so they can read a little choppy. The optional AI
  gives smoother prose but should still be checked against the original.

## Project layout

```
src/inboxpilot/
  analysis/    categorizer, priority, followup, deadlines, summarizer, drafts, ai   (pure Python, no Qt)
  providers/   base.py (interface + registry), gmail.py, demo.py
  storage/     db.py (SQLite + FTS5), secrets.py (keyring), settings.py
  services/    sync.py, analyzer.py, context.py (application facade)
  ui/          PySide6: main_window, detail_panel, settings_dialog, table_model, workers, render
tests/         unittest/pytest-compatible suite
packaging/     PyInstaller spec, PowerShell build script, Inno Setup installer script
```

### Adding another email provider (e.g. Microsoft Graph / Outlook)

Create `providers/outlook.py`, subclass `EmailProvider` and implement `create`, `account_email`, `connect` (OAuth),
`disconnect`, `is_connected`, `list_ids(scope, since)` and `fetch_messages(ids)` returning `EmailMessage` objects, then
decorate the class with `@register` and import it in `providers/__init__.py`. Use a read-only scope (`Mail.Read`).
Sync, analysis, storage and UI need no changes (add a menu entry that calls `ctx.connect("outlook")`).

## Tests

```bash
python -m unittest discover -s tests      # no extra packages needed
# or:  pip install pytest && pytest
```

The suite (72 tests) covers deadline extraction, categorisation, priority explanations, follow-up detection, summaries,
drafts, SQLite search/filter/sort/date ranges (including FTS special characters and the LIKE fallback), sync against the
demo provider (read-state mirroring, pruning, overrides surviving re-sync), Gmail message parsing, the read-only /
no-password guarantees, token storage, AI parsing with a fake client (including rejecting invented deadlines), and a Qt
smoke test that runs automatically when PySide6 is installed (`QT_QPA_PLATFORM=offscreen`).

## Windows packaging

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
```

This creates a virtualenv, installs dependencies, **runs the tests**, builds `dist\InboxPilot\InboxPilot.exe` with
PyInstaller, and — if [Inno Setup](https://jrsoftware.org/isinfo.php) is installed — `dist\InboxPilot-Setup.exe`.
Place your OAuth `client_secret.json` next to `InboxPilot.exe` if you want it to work without the Settings step.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Gmail needs a Google OAuth client file" | Follow *Connecting Gmail*, then pick the JSON in Settings. |
| "Access blocked / app not verified" | Add your address as a **Test user** on the OAuth consent screen. |
| "Gmail API has not been used in project…" | Enable the Gmail API in your Google Cloud project. |
| Asked to sign in again after a week | Normal in Google's Testing mode (7-day refresh-token expiry). |
| "No secure credential store" in Settings | Install/enable a keyring backend (Linux: GNOME Keyring or KWallet). |
| Anything else | *Help → Open log file*. |
