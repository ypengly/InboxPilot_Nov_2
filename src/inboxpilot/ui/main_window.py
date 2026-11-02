from __future__ import annotations

import logging
import webbrowser

from PySide6.QtCore import QDate, QSignalBlocker, Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QSplitter, QStackedWidget, QTableView, QToolBar, QVBoxLayout,
                               QWidget)

from .. import __version__
from ..config import APP_NAME, log_path
from ..models import CATEGORIES, FOLDERS, PRIORITIES, EmailQuery
from ..services.context import AppContext
from .detail_panel import DetailPanel
from .settings_dialog import SettingsDialog
from .table_model import SORT_KEYS, EmailTableModel
from .workers import Worker

log = logging.getLogger(__name__)

SORT_CHOICES = (("Date", "date"), ("Sender", "sender"), ("Subject", "subject"), ("Priority", "priority"),
                ("Category", "category"), ("Unread first", "unread"))
RANGE_CHOICES = ("Any time", "Today", "Last 7 days", "Last 30 days", "Custom range\u2026")


class StatCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("statCard")
        self.value = QLabel("0")
        self.value.setObjectName("statValue")
        caption = QLabel(title)
        caption.setEnabled(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(0)
        lay.addWidget(self.value)
        lay.addWidget(caption)

    def set_value(self, n: int) -> None:
        self.value.setText(f"{n:,}")


class WelcomePage(QWidget):
    def __init__(self, main: "MainWindow"):
        super().__init__()
        title = QLabel(f"Welcome to {APP_NAME}")
        title.setObjectName("welcomeTitle")
        intro = QLabel(
            "Turn a crowded inbox into a command center: see what is important, what needs a reply, what you are waiting "
            "on, and what can be ignored.<br><br>"
            "<b>How your mailbox is handled</b><ul>"
            "<li>You sign in through your provider's own page (OAuth). InboxPilot never sees or stores your password.</li>"
            "<li>Access is <b>read-only</b>. InboxPilot cannot send, delete, archive or change any email.</li>"
            "<li>The sign-in token is kept in your operating system's secure credential store.</li>"
            "<li>Messages are cached in a local database on this computer. You can disconnect and delete it at any time.</li></ul>")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        self.gmail_btn = QPushButton("Connect Gmail")
        self.gmail_btn.setDefault(True)
        demo_btn = QPushButton("Explore with a demo mailbox")
        settings_btn = QPushButton("Settings\u2026")
        self.gmail_btn.clicked.connect(main.connect_gmail)
        demo_btn.clicked.connect(main.connect_demo)
        settings_btn.clicked.connect(main.open_settings)
        row = QHBoxLayout()
        row.addWidget(self.gmail_btn)
        row.addWidget(demo_btn)
        row.addWidget(settings_btn)
        row.addStretch(1)
        hint = QLabel("Gmail needs a Google OAuth client file the first time. See the README (\u201cConnecting Gmail\u201d) "
                      "or choose it under Settings.")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        box = QVBoxLayout()
        box.addWidget(title)
        box.addWidget(intro)
        box.addLayout(row)
        box.addWidget(hint)
        box.addStretch(1)
        holder = QWidget()
        holder.setMaximumWidth(720)
        holder.setLayout(box)
        outer = QHBoxLayout(self)
        outer.addStretch(1)
        outer.addWidget(holder, 6)
        outer.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.pool = QThreadPool.globalInstance()
        self._workers: set[Worker] = set()
        self._sync_worker: Worker | None = None
        self._sort_by, self._descending = "date", True
        self.setWindowTitle(APP_NAME)
        self.resize(1480, 880)

        self.stack = QStackedWidget()
        self.welcome = WelcomePage(self)
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self._build_main_page())
        self.setCentralWidget(self.stack)
        self._build_menu()
        self._build_status_bar()
        self.detail.busy.connect(self.run_worker)
        self.detail.changed.connect(lambda i: self.refresh(select_id=i, reselect_quietly=True))
        self.detail.status.connect(lambda m: self.statusBar().showMessage(m, 8000))

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.refresh)
        self._update_page()
        if self.ctx.has_account:
            self.refresh()

    # ---- construction ------------------------------------------------------------------------------------
    def _build_main_page(self) -> QWidget:
        self.folders = QListWidget()
        self.folders.setObjectName("folders")
        for key, label in FOLDERS:
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            self.folders.addItem(it)
        self.folders.setCurrentRow(0)
        self.folders.currentRowChanged.connect(lambda _r: self.refresh())
        self.account_label = QLabel()
        self.account_label.setWordWrap(True)
        self.sync_label = QLabel()
        self.sync_label.setEnabled(False)
        self.sync_btn = QPushButton("Sync now")
        self.sync_btn.clicked.connect(self.start_sync)
        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 8, 8, 8)
        sl.addWidget(self.folders, 1)
        sl.addWidget(self.account_label)
        sl.addWidget(self.sync_label)
        sl.addWidget(self.sync_btn)
        side.setMinimumWidth(190)
        side.setMaximumWidth(260)

        self.cards = {k: StatCard(t) for k, t in (("unread", "Unread"), ("important", "Important"),
                                                  ("pending_replies", "Pending replies"),
                                                  ("recent", "Received in last 7 days"))}
        cards_row = QHBoxLayout()
        for c in self.cards.values():
            cards_row.addWidget(c)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search subject, sender and message text\u2026")
        self.search_edit.setClearButtonEnabled(True)
        self.sender_edit = QLineEdit()
        self.sender_edit.setPlaceholderText("Sender")
        self.sender_edit.setClearButtonEnabled(True)
        self.category_filter = QComboBox()
        self.category_filter.addItem("All categories", "")
        for c in CATEGORIES:
            self.category_filter.addItem(c, c)
        self.priority_filter = QComboBox()
        self.priority_filter.addItem("Any priority", "")
        for p in PRIORITIES:
            self.priority_filter.addItem(p, p)
        self.range_box = QComboBox()
        self.range_box.addItems(RANGE_CHOICES)
        self.date_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.date_to = QDateEdit(QDate.currentDate())
        for d in (self.date_from, self.date_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("d MMM yyyy")
            d.setVisible(False)
        self.unread_check = QCheckBox("Unread only")
        self.sort_box = QComboBox()
        for label, key in SORT_CHOICES:
            self.sort_box.addItem(f"Sort: {label}", key)
        self.order_btn = QPushButton("\u2193")
        self.order_btn.setFixedWidth(34)
        self.order_btn.setToolTip("Toggle newest/oldest or A\u2013Z/Z\u2013A")
        clear_btn = QPushButton("Clear filters")

        for w in (self.search_edit, self.sender_edit):
            w.textChanged.connect(lambda _t: self._debounce.start())
        for cb in (self.category_filter, self.priority_filter):
            cb.currentIndexChanged.connect(lambda _i: self.refresh())
        self.range_box.currentIndexChanged.connect(self._on_range_changed)
        for d in (self.date_from, self.date_to):
            d.dateChanged.connect(lambda _d: self.refresh())
        self.unread_check.toggled.connect(lambda _c: self.refresh())
        self.sort_box.currentIndexChanged.connect(self._on_sort_combo)
        self.order_btn.clicked.connect(self._toggle_order)
        clear_btn.clicked.connect(self.clear_filters)

        grid = QGridLayout()
        grid.addWidget(self.search_edit, 0, 0, 1, 3)
        grid.addWidget(self.sender_edit, 0, 3, 1, 2)
        grid.addWidget(self.category_filter, 1, 0)
        grid.addWidget(self.priority_filter, 1, 1)
        grid.addWidget(self.range_box, 1, 2)
        dates = QHBoxLayout()
        dates.addWidget(self.date_from)
        dates.addWidget(QLabel("to"))
        dates.addWidget(self.date_to)
        grid.addLayout(dates, 1, 3, 1, 2)
        row3 = QHBoxLayout()
        row3.addWidget(self.unread_check)
        row3.addWidget(self.sort_box)
        row3.addWidget(self.order_btn)
        row3.addStretch(1)
        self.count_label = QLabel()
        self.count_label.setEnabled(False)
        row3.addWidget(self.count_label)
        row3.addWidget(clear_btn)

        self.model = EmailTableModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionsClickable(True)
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for col, width in ((0, 28), (1, 70), (2, 170), (3, 360), (4, 110), (5, 150), (6, 80)):
            self.table.setColumnWidth(col, width)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hh.sectionClicked.connect(self._on_header_clicked)
        self.table.selectionModel().currentRowChanged.connect(self._on_row_changed)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(8, 8, 8, 8)
        cl.addLayout(cards_row)
        cl.addLayout(grid)
        cl.addLayout(row3)
        cl.addWidget(self.table, 1)

        self.detail = DetailPanel(self.ctx)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(side)
        splitter.addWidget(center)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([210, 760, 520])
        return splitter

    def _build_menu(self) -> None:
        bar = self.menuBar()
        acct = bar.addMenu("&Account")
        self.act_connect = QAction("Connect &Gmail\u2026", self, triggered=self.connect_gmail)
        self.act_demo = QAction("Use &demo mailbox", self, triggered=self.connect_demo)
        self.act_sync = QAction("&Sync now", self, triggered=self.start_sync)
        self.act_sync.setShortcut(QKeySequence("Ctrl+R"))
        self.act_disconnect = QAction("D&isconnect\u2026", self, triggered=self.disconnect_account)
        act_settings = QAction("S&ettings\u2026", self, triggered=self.open_settings)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_quit = QAction("&Quit", self, triggered=self.close)
        acct.addActions([self.act_connect, self.act_demo, self.act_sync])
        acct.addSeparator()
        acct.addActions([self.act_disconnect, act_settings])
        acct.addSeparator()
        acct.addAction(act_quit)
        view = bar.addMenu("&View")
        act_refresh = QAction("&Refresh list", self, triggered=lambda: self.refresh())
        act_refresh.setShortcut(QKeySequence("F5"))
        act_find = QAction("&Search", self, triggered=lambda: self.search_edit.setFocus())
        act_find.setShortcut(QKeySequence("Ctrl+F"))
        view.addActions([act_refresh, act_find])
        help_menu = bar.addMenu("&Help")
        help_menu.addAction(QAction("&Privacy && permissions", self, triggered=self.show_privacy))
        help_menu.addAction(QAction("Open &log file", self, triggered=lambda: webbrowser.open(str(log_path()))))
        help_menu.addAction(QAction("&About", self, triggered=self.show_about))
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.addAction(self.act_sync)
        self.addToolBar(tb)

    def _build_status_bar(self) -> None:
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.setVisible(False)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_sync)
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_btn)

    # ---- state ----------------------------------------------------------------------------------------------
    def _update_page(self) -> None:
        connected = self.ctx.has_account
        self.stack.setCurrentIndex(1 if connected else 0)
        self.act_disconnect.setEnabled(connected)
        self.act_sync.setEnabled(connected and self._sync_worker is None)
        self.act_connect.setEnabled(not connected)
        self.act_demo.setEnabled(not connected)
        title = APP_NAME
        if connected:
            title += f" \u2014 {self.ctx.user_email}" + (" (demo data)" if self.ctx.is_demo else "")
        self.setWindowTitle(title)
        if connected:
            self.account_label.setText(f"<b>{self.ctx.user_email}</b>" + ("<br><i>Demo data (fictional)</i>" if self.ctx.is_demo else ""))
            self._update_sync_label()

    def _update_sync_label(self) -> None:
        aid = self.ctx.account_id
        row = self.ctx.repo.get_account(self.ctx.settings.active_provider, self.ctx.settings.active_account) if aid else None
        last = row["last_sync_at"] if row else None
        self.sync_label.setText(f"Last sync: {last.replace('T', ' ')} UTC" if last else "Not synced yet")

    def run_worker(self, worker: Worker) -> None:
        self._workers.add(worker)
        worker.signals.finished.connect(lambda *_: self._workers.discard(worker))
        worker.signals.failed.connect(lambda *_: self._workers.discard(worker))
        worker.signals.cancelled.connect(lambda: self._workers.discard(worker))
        self.pool.start(worker)

    # ---- querying ---------------------------------------------------------------------------------------------
    def _folder_key(self) -> str:
        item = self.folders.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else "inbox"

    def _query(self) -> EmailQuery:
        q = EmailQuery(folder=self._folder_key(), text=self.search_edit.text(), sender=self.sender_edit.text(),
                       category=self.category_filter.currentData() or "", priority=self.priority_filter.currentData() or "",
                       unread_only=self.unread_check.isChecked(), sort_by=self._sort_by, descending=self._descending)
        today = QDate.currentDate()
        idx = self.range_box.currentIndex()
        if idx == 1:
            q.date_from = q.date_to = today.toPython()
        elif idx == 2:
            q.date_from, q.date_to = today.addDays(-6).toPython(), today.toPython()
        elif idx == 3:
            q.date_from, q.date_to = today.addDays(-29).toPython(), today.toPython()
        elif idx == 4:
            q.date_from, q.date_to = self.date_from.date().toPython(), self.date_to.date().toPython()
        return q

    def refresh(self, select_id: int | None = None, reselect_quietly: bool = False) -> None:
        if not self.ctx.has_account:
            return
        try:
            counts = self.ctx.folder_counts()
            stats = self.ctx.stats()
            records = self.ctx.search(self._query())
        except Exception as exc:  # StorageError or unexpected: show, don't crash
            log.exception("Refresh failed")
            self.statusBar().showMessage(getattr(exc, "user_message", str(exc)), 10000)
            return
        with QSignalBlocker(self.folders):
            for i, (key, label) in enumerate(FOLDERS):
                n = counts.get(key, 0)
                self.folders.item(i).setText(f"{label}   ({n:,})" if n else label)
        for k, card in self.cards.items():
            card.set_value(stats.get(k, 0))

        keep = select_id if select_id is not None else self._selected_id()
        sel = self.table.selectionModel()
        with QSignalBlocker(sel):
            self.model.set_records(records)
            row = self.model.row_of(keep) if keep is not None else -1
            if row >= 0:
                self.table.selectRow(row)
                self.table.scrollTo(self.model.index(row, 0))
        if row < 0 and not reselect_quietly:
            self.detail.show_email(None)
        self.count_label.setText(f"{len(records):,} message{'s' if len(records) != 1 else ''}")
        self._update_sort_indicator()

    def _selected_id(self) -> int | None:
        rec = self.model.record(self.table.currentIndex().row())
        return rec.id if rec else None

    def _on_row_changed(self, current, _previous) -> None:
        rec = self.model.record(current.row())
        self.detail.show_email(self.ctx.get_email(rec.id) if rec else None)

    # ---- sorting & filters ----------------------------------------------------------------------------------------
    def _update_sort_indicator(self) -> None:
        with QSignalBlocker(self.sort_box):
            self.sort_box.setCurrentIndex(next(i for i, (_l, k) in enumerate(SORT_CHOICES) if k == self._sort_by))
        self.order_btn.setText("\u2193" if self._descending else "\u2191")
        col = next((c for c, k in SORT_KEYS.items() if k == self._sort_by), -1)
        hh = self.table.horizontalHeader()
        if col >= 0:
            hh.setSortIndicator(col, Qt.SortOrder.DescendingOrder if self._descending else Qt.SortOrder.AscendingOrder)
        hh.setSortIndicatorShown(col >= 0)

    def _on_header_clicked(self, col: int) -> None:
        key = SORT_KEYS.get(col)
        if not key:
            return
        self._descending = not self._descending if key == self._sort_by else key in ("date", "priority", "unread")
        self._sort_by = key
        self.refresh()

    def _on_sort_combo(self, _i: int) -> None:
        self._sort_by = self.sort_box.currentData()
        self._descending = self._sort_by in ("date", "priority", "unread")
        self.refresh()

    def _toggle_order(self) -> None:
        self._descending = not self._descending
        self.refresh()

    def _on_range_changed(self, idx: int) -> None:
        custom = idx == 4
        self.date_from.setVisible(custom)
        self.date_to.setVisible(custom)
        self.refresh()

    def clear_filters(self) -> None:
        for w in (self.search_edit, self.sender_edit):
            with QSignalBlocker(w):
                w.clear()
        for cb in (self.category_filter, self.priority_filter, self.range_box):
            with QSignalBlocker(cb):
                cb.setCurrentIndex(0)
        with QSignalBlocker(self.unread_check):
            self.unread_check.setChecked(False)
        self.date_from.setVisible(False)
        self.date_to.setVisible(False)
        self._sort_by, self._descending = "date", True
        self.refresh()

    # ---- connect / sync / disconnect -----------------------------------------------------------------------------------
    def connect_gmail(self) -> None:
        self.welcome.gmail_btn.setEnabled(False)
        self.act_connect.setEnabled(False)
        self.statusBar().showMessage("A browser window will open. Sign in with Google and approve read-only access\u2026")
        worker = Worker(self.ctx.connect, "gmail")

        def done(email: str) -> None:
            self.welcome.gmail_btn.setEnabled(True)
            self.statusBar().showMessage(f"Connected as {email}", 6000)
            self._update_page()
            self.start_sync()

        def failed(msg: str) -> None:
            self.welcome.gmail_btn.setEnabled(True)
            self.act_connect.setEnabled(not self.ctx.has_account)
            self.statusBar().clearMessage()
            box = QMessageBox(QMessageBox.Icon.Warning, "Could not connect Gmail", msg, QMessageBox.StandardButton.Ok, self)
            settings_btn = box.addButton("Open Settings", QMessageBox.ButtonRole.ActionRole)
            box.exec()
            if box.clickedButton() is settings_btn:
                self.open_settings()

        worker.signals.finished.connect(done)
        worker.signals.failed.connect(failed)
        self.run_worker(worker)

    def connect_demo(self) -> None:
        try:
            self.ctx.connect("demo")
        except Exception as exc:
            QMessageBox.warning(self, "Demo mailbox", str(exc))
            return
        self._update_page()
        self.start_sync()

    def start_sync(self) -> None:
        if self._sync_worker is not None or not self.ctx.has_account:
            return
        provider = self.ctx.provider()
        if provider is not None and not provider.is_connected():
            self.statusBar().showMessage("You are signed out; a browser window will open so you can sign in again\u2026")
        worker = Worker(self.ctx.sync, with_progress=True, with_cancel=True)
        self._sync_worker = worker
        self.act_sync.setEnabled(False)
        self.sync_btn.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.cancel_btn.setVisible(True)

        def progress(stage: str, cur: int, total: int) -> None:
            if total > 0:
                self.progress.setRange(0, total)
                self.progress.setValue(cur)
                self.statusBar().showMessage(f"{stage}: {cur:,} of {total:,}")
            else:
                self.progress.setRange(0, 0)
                self.statusBar().showMessage(f"{stage}\u2026")

        def finish() -> None:
            self._sync_worker = None
            self.progress.setVisible(False)
            self.cancel_btn.setVisible(False)
            self.sync_btn.setEnabled(True)
            self.act_sync.setEnabled(self.ctx.has_account)

        def done(result) -> None:
            finish()
            self._update_page()
            self.refresh()
            self.statusBar().showMessage(
                f"Sync finished: {result.new_messages:,} new, {result.removed:,} removed, {result.analyzed:,} analysed.", 10000)
            if result.warnings:
                QMessageBox.warning(self, "Sync finished with a problem",
                                    "Some messages could not be downloaded:\n\n" + "\n".join(result.warnings) +
                                    "\n\nWhat was downloaded is available; try Sync again later.")

        def failed(msg: str) -> None:
            finish()
            self.refresh()
            self.statusBar().clearMessage()
            QMessageBox.warning(self, "Sync problem", msg)

        def cancelled() -> None:
            finish()
            self.refresh()
            self.statusBar().showMessage("Sync cancelled. Messages downloaded so far were kept.", 8000)

        worker.signals.progress.connect(progress)
        worker.signals.finished.connect(done)
        worker.signals.failed.connect(failed)
        worker.signals.cancelled.connect(cancelled)
        self.run_worker(worker)

    def cancel_sync(self) -> None:
        if self._sync_worker:
            self._sync_worker.cancel()
            self.statusBar().showMessage("Cancelling\u2026")

    def disconnect_account(self) -> None:
        if not self.ctx.has_account:
            return
        box = QMessageBox(QMessageBox.Icon.Question, "Disconnect account",
                          f"Disconnect {self.ctx.user_email}?\n\nInboxPilot will forget its sign-in and ask Google to revoke "
                          "its access. Your mailbox itself is not changed.",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        delete = QCheckBox("Also delete the downloaded email, summaries and drafts from this computer")
        delete.setChecked(True)
        box.setCheckBox(delete)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        if self._sync_worker:
            self._sync_worker.cancel()
        self.detail.show_email(None)
        worker = Worker(self.ctx.disconnect, delete.isChecked())

        def done(_r) -> None:
            self._update_page()
            self.model.set_records([])
            self.statusBar().showMessage("Disconnected.", 6000)

        worker.signals.finished.connect(done)
        worker.signals.failed.connect(lambda m: QMessageBox.warning(self, "Disconnect", m))
        self.run_worker(worker)

    def open_settings(self) -> None:
        if SettingsDialog(self.ctx, self).exec():
            self.refresh()
            sel = self._selected_id()
            self.detail.show_email(self.ctx.get_email(sel) if sel else None)

    # ---- dialogs -----------------------------------------------------------------------------------------------------
    def show_privacy(self) -> None:
        QMessageBox.information(self, "Privacy & permissions", (
            "<b>What InboxPilot can do</b><br>Read your messages (Gmail scope: gmail.readonly) to organise and summarise them.<br><br>"
            "<b>What it cannot do</b><br>Send, delete, archive, label or edit email. Drafts you save stay in a local database on this computer.<br><br>"
            "<b>Passwords</b><br>Never requested or stored. Sign-in happens on your provider's own page (OAuth).<br><br>"
            "<b>Tokens</b><br>Stored in the operating system's credential store.<br><br>"
            "<b>AI</b><br>Off by default. If you enable it, the text of one selected email is sent to the Anthropic API only when you ask for an AI summary or draft.<br><br>"
            "<b>Local data</b><br>The cached mail database is not encrypted by InboxPilot; use full-disk encryption (e.g. BitLocker) "
            "and choose \u201cDisconnect \u2192 delete downloaded email\u201d to remove it."))

    def show_about(self) -> None:
        QMessageBox.about(self, f"About {APP_NAME}", f"<b>{APP_NAME}</b> {__version__}<br>A local-first email command center.<br>"
                          "Read-only access. Nothing is ever sent, deleted or archived by this app.")

    def closeEvent(self, event) -> None:
        self.detail.flush_draft()
        if self._sync_worker:
            self._sync_worker.cancel()
        self.pool.waitForDone(3000)
        super().closeEvent(event)
