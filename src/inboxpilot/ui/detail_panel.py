"""Right-hand pane: message, insights (priority/follow-up/summary + manual overrides) and draft assistant."""
from __future__ import annotations

import html

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..analysis.drafts import TONES
from ..models import CATEGORIES, EmailRecord, Summary
from ..services.context import AppContext
from . import render
from .workers import Worker

PRIORITY_CHOICES = ("Automatic", "High", "Medium", "Low")


class DetailPanel(QWidget):
    changed = Signal(int)          # email id whose category/priority/flags were changed by the user
    busy = Signal(Worker)          # ask MainWindow to run a background worker
    status = Signal(str)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.rec: EmailRecord | None = None
        self._draft_dirty = False
        self._summary_request = 0

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_message_tab(), "Message")
        self.tabs.addTab(self._build_insights_tab(), "Insights")
        self.tabs.addTab(self._build_draft_tab(), "Draft")
        self.placeholder = QLabel("Select a message to see its details.")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setEnabled(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.placeholder)
        lay.addWidget(self.tabs)
        self.show_email(None)

    # ---- construction --------------------------------------------------------------------------------
    def _build_message_tab(self) -> QWidget:
        w = QWidget()
        self.header = QLabel()
        self.header.setTextFormat(Qt.TextFormat.RichText)
        self.header.setWordWrap(True)
        self.header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body = QPlainTextEdit()
        self.body.setReadOnly(True)
        note = QLabel("InboxPilot only reads your mail. Opening a message here does not mark it as read in your mailbox.")
        note.setWordWrap(True)
        note.setEnabled(False)
        lay = QVBoxLayout(w)
        lay.addWidget(self.header)
        lay.addWidget(self.body, 1)
        lay.addWidget(note)
        return w

    def _build_insights_tab(self) -> QWidget:
        w = QWidget()
        self.category_box = QComboBox()
        self.category_box.addItems(CATEGORIES)
        self.priority_box = QComboBox()
        self.priority_box.addItems(PRIORITY_CHOICES)
        self.reply_check = QCheckBox("Needs a reply")
        self.follow_check = QCheckBox("Follow up")
        self.reset_btn = QPushButton("Reset to automatic")
        self.cat_reason = QLabel()
        self.cat_reason.setWordWrap(True)
        self.cat_reason.setEnabled(False)
        form = QFormLayout()
        form.addRow("Category", self.category_box)
        form.addRow("Priority", self.priority_box)
        flags = QHBoxLayout()
        flags.addWidget(self.reply_check)
        flags.addWidget(self.follow_check)
        flags.addStretch(1)
        flags.addWidget(self.reset_btn)
        self.insights = QTextBrowser()
        self.insights.setOpenLinks(False)
        self.ai_btn = QPushButton("Summarize with AI")
        self.ai_note = QLabel("Sends this email's text to the Anthropic API. Off unless enabled in Settings.")
        self.ai_note.setWordWrap(True)
        self.ai_note.setEnabled(False)
        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(self.cat_reason)
        lay.addLayout(flags)
        lay.addWidget(self.insights, 1)
        row = QHBoxLayout()
        row.addWidget(self.ai_btn)
        row.addWidget(self.ai_note, 1)
        lay.addLayout(row)

        self.category_box.activated.connect(self._on_category)
        self.priority_box.activated.connect(self._on_priority)
        self.reply_check.clicked.connect(self._on_reply_flag)
        self.follow_check.clicked.connect(self._on_follow_flag)
        self.reset_btn.clicked.connect(self._on_reset)
        self.ai_btn.clicked.connect(self._on_ai_summary)
        return w

    def _build_draft_tab(self) -> QWidget:
        w = QWidget()
        self.tone_box = QComboBox()
        self.tone_box.addItems(TONES)
        self.gen_btn = QPushButton("Generate draft")
        self.use_ai = QCheckBox("Use AI")
        self.subject_edit = QLineEdit()
        self.subject_edit.setReadOnly(True)
        self.draft_edit = QPlainTextEdit()
        self.draft_edit.setReadOnly(True)
        self.draft_edit.setPlaceholderText("Click \u201cGenerate draft\u201d. The draft stays on this computer; InboxPilot never sends email.")
        self.edit_btn = QPushButton("Edit")
        self.copy_btn = QPushButton("Copy")
        self.save_btn = QPushButton("Save Draft")
        note = QLabel("Text in [square brackets] is for you to fill in \u2014 InboxPilot does not guess your answers. "
                      "Nothing is ever sent automatically.")
        note.setWordWrap(True)
        note.setEnabled(False)
        self.draft_status = QLabel()
        top = QHBoxLayout()
        top.addWidget(QLabel("Tone"))
        top.addWidget(self.tone_box)
        top.addWidget(self.use_ai)
        top.addStretch(1)
        top.addWidget(self.gen_btn)
        btns = QHBoxLayout()
        btns.addWidget(self.edit_btn)
        btns.addWidget(self.copy_btn)
        btns.addWidget(self.save_btn)
        btns.addStretch(1)
        btns.addWidget(self.draft_status)
        lay = QVBoxLayout(w)
        lay.addLayout(top)
        lay.addWidget(self.subject_edit)
        lay.addWidget(self.draft_edit, 1)
        lay.addLayout(btns)
        lay.addWidget(note)

        self.gen_btn.clicked.connect(self._on_generate)
        self.edit_btn.clicked.connect(self._on_edit)
        self.copy_btn.clicked.connect(self._on_copy)
        self.save_btn.clicked.connect(self._on_save)
        self.draft_edit.textChanged.connect(self._on_draft_text_changed)
        return w

    # ---- showing a message -------------------------------------------------------------------------------
    def flush_draft(self) -> None:
        """If the person edited a draft but didn't save, ask what to do before it is replaced."""
        if self.rec is not None and self._draft_dirty and self.draft_edit.toPlainText().strip():
            answer = QMessageBox.question(
                self, "Unsaved draft", "You have unsaved changes to the draft reply. Save them on this computer?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard, QMessageBox.StandardButton.Save)
            if answer == QMessageBox.StandardButton.Save:
                self.ctx.repo.save_draft(self.rec.id, self.subject_edit.text(), self.draft_edit.toPlainText())
        self._draft_dirty = False

    def show_email(self, rec: EmailRecord | None) -> None:
        self.flush_draft()
        self.rec = rec
        self._summary_request += 1
        self.tabs.setVisible(rec is not None)
        self.placeholder.setVisible(rec is None)
        if rec is None:
            return
        who = "To" if rec.is_sent else "From"
        who_plain = render.format_addresses(rec.to if rec.is_sent else [(rec.sender_name, rec.sender_email)])
        lines = [f"<b style='font-size:15px'>{html.escape(rec.display_subject)}</b>",
                 f"<b>{who}:</b> {html.escape(who_plain)}"]
        if not rec.is_sent:
            lines.append(f"<b>To:</b> {html.escape(render.format_addresses(rec.to))}")
        if rec.cc:
            lines.append(f"<b>Cc:</b> {html.escape(render.format_addresses(rec.cc))}")
        lines.append(f"<b>Date:</b> {render.format_full_date(rec.date)}")
        self.header.setText("<br>".join(lines))
        self.body.setPlainText(rec.body_text or rec.snippet or "(This message has no text content.)")

        widgets = (self.category_box, self.priority_box, self.reply_check, self.follow_check)
        for box in widgets:
            box.blockSignals(True)
        self.category_box.setCurrentText(rec.category)
        self.priority_box.setItemText(0, f"Automatic ({rec.auto_priority})")
        self.priority_box.setCurrentIndex(PRIORITY_CHOICES.index(rec.user_priority) if rec.user_priority else 0)
        self.reply_check.setChecked(rec.needs_reply)
        self.follow_check.setChecked(rec.follow_up)
        for box in widgets:
            box.blockSignals(False)
        self.cat_reason.setText(render.category_reason_text(rec))
        self._render_insights(self.ctx.local_summary(rec))
        ai = self.ctx.ai_available()
        for w in (self.ai_btn, self.ai_note, self.use_ai):
            w.setVisible(ai)

        saved = self.ctx.repo.get_draft(rec.id)
        self._set_draft(saved.subject if saved else "", saved.body if saved else "", locked=True)
        self.draft_status.setText("Saved draft loaded" if saved else "")
        self._draft_dirty = False

    def _render_insights(self, summary: Summary) -> None:
        if self.rec:
            self.insights.setHtml(render.render_insights_html(self.rec, summary))

    def _reload(self) -> None:
        """Re-read the message after a manual change, keeping tab and any draft text the person was editing."""
        if self.rec is None:
            return
        keep_tab = self.tabs.currentIndex()
        subj, body, dirty = self.subject_edit.text(), self.draft_edit.toPlainText(), self._draft_dirty
        fresh = self.ctx.get_email(self.rec.id)
        self.changed.emit(self.rec.id)
        if fresh:
            self._draft_dirty = False  # avoid the unsaved-draft prompt: the same message is being re-shown
            self.show_email(fresh)
            if body or subj:
                self._set_draft(subj, body, locked=True)
                self._draft_dirty = dirty
                if dirty:
                    self.draft_status.setText("Unsaved changes")
            self.tabs.setCurrentIndex(keep_tab)

    # ---- manual overrides (the user is always in control of labels) -----------------------------------------------
    def _on_category(self, _i: int) -> None:
        if self.rec:
            self.ctx.repo.set_override(self.rec.id, "user_category", self.category_box.currentText())
            self._reload()

    def _on_priority(self, _i: int) -> None:
        if self.rec:
            choice = self.priority_box.currentText()
            self.ctx.repo.set_override(self.rec.id, "user_priority", None if choice.startswith("Automatic") else choice)
            self._reload()

    def _on_reply_flag(self, checked: bool) -> None:
        if self.rec:
            self.ctx.repo.set_override(self.rec.id, "user_needs_reply", checked)
            self._reload()

    def _on_follow_flag(self, checked: bool) -> None:
        if self.rec:
            self.ctx.repo.set_override(self.rec.id, "user_follow_up", checked)
            self._reload()

    def _on_reset(self) -> None:
        if self.rec:
            self.ctx.repo.clear_overrides(self.rec.id)
            self._reload()

    # ---- AI summary -------------------------------------------------------------------------------------
    def _on_ai_summary(self) -> None:
        if not self.rec:
            return
        rec, token = self.rec, self._summary_request
        self.ai_btn.setEnabled(False)
        self.status.emit("Asking the AI service for a summary\u2026")
        worker = Worker(lambda: self.ctx.summarize(rec, use_ai=True))

        def done(summary: Summary) -> None:
            self.ai_btn.setEnabled(True)
            if token == self._summary_request:  # ignore if another message was selected meanwhile
                self._render_insights(summary)
            self.status.emit("Summary ready." if summary.engine == "ai" else "AI unavailable \u2014 showing the local summary.")

        def failed(msg: str) -> None:
            self.ai_btn.setEnabled(True)
            self.status.emit(msg)

        worker.signals.finished.connect(done)
        worker.signals.failed.connect(failed)
        self.busy.emit(worker)

    # ---- draft assistant ----------------------------------------------------------------------------------
    def _set_draft(self, subject: str, body: str, locked: bool) -> None:
        with QSignalBlocker(self.draft_edit):
            self.draft_edit.setPlainText(body)
        self.subject_edit.setText(subject)
        self.draft_edit.setReadOnly(locked)
        self.subject_edit.setReadOnly(locked)
        self.edit_btn.setText("Edit" if locked else "Lock")

    def _on_draft_text_changed(self) -> None:
        self._draft_dirty = True
        self.draft_status.setText("Unsaved changes")

    def _on_generate(self) -> None:
        if not self.rec:
            return
        rec, tone, use_ai = self.rec, self.tone_box.currentText(), self.use_ai.isChecked()
        if self.draft_edit.toPlainText().strip() and self._draft_dirty:
            if QMessageBox.question(self, "Replace draft?", "Replace the current draft with a new one?") != QMessageBox.StandardButton.Yes:
                return
        self.gen_btn.setEnabled(False)
        self.status.emit("Writing a draft\u2026")
        worker = Worker(lambda: self.ctx.draft(rec, tone, use_ai))

        def done(draft) -> None:
            self.gen_btn.setEnabled(True)
            if self.rec and self.rec.id == rec.id:
                self._set_draft(draft.subject, draft.body, locked=True)
                self._draft_dirty = False
                self.draft_status.setText("Draft ready \u2014 click Edit to change it")
            self.status.emit("Draft ready. It has not been sent or saved anywhere yet.")

        def failed(msg: str) -> None:
            self.gen_btn.setEnabled(True)
            self.status.emit(msg)

        worker.signals.finished.connect(done)
        worker.signals.failed.connect(failed)
        self.busy.emit(worker)

    def _on_edit(self) -> None:
        locked = not self.draft_edit.isReadOnly()
        self.draft_edit.setReadOnly(locked)
        self.subject_edit.setReadOnly(locked)
        self.edit_btn.setText("Edit" if locked else "Lock")
        if not locked:
            self.draft_edit.setFocus()

    def _on_copy(self) -> None:
        text = self.draft_edit.toPlainText()
        if text.strip():
            QGuiApplication.clipboard().setText(text)
            self.draft_status.setText("Copied to clipboard")

    def _on_save(self) -> None:
        if self.rec and self.draft_edit.toPlainText().strip():
            self.ctx.repo.save_draft(self.rec.id, self.subject_edit.text(), self.draft_edit.toPlainText())
            self._draft_dirty = False
            self.draft_edit.setReadOnly(True)
            self.subject_edit.setReadOnly(True)
            self.edit_btn.setText("Edit")
            self.draft_status.setText("Saved on this computer (not sent)")
