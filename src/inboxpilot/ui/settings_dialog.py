from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..services.context import AI_KEY, AppContext


class SettingsDialog(QDialog):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)
        s = ctx.settings

        self.name_edit = QLineEdit(s.user_name)
        self.name_edit.setPlaceholderText("Used to sign draft replies")
        self.sync_days = QSpinBox()
        self.sync_days.setRange(7, 365)
        self.sync_days.setValue(s.sync_days)
        self.sync_days.setSuffix(" days")
        self.follow_days = QSpinBox()
        self.follow_days.setRange(1, 30)
        self.follow_days.setValue(s.follow_up_days)
        self.follow_days.setSuffix(" days")
        self.client_edit = QLineEdit(s.gmail_client_secrets_path)
        self.client_edit.setPlaceholderText("client_secret.json from Google Cloud Console")
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse)
        client_row = QHBoxLayout()
        client_row.addWidget(self.client_edit, 1)
        client_row.addWidget(browse)

        general = QFormLayout()
        general.addRow("Your name", self.name_edit)
        general.addRow("Download mail from the last", self.sync_days)
        general.addRow("Suggest follow-up after", self.follow_days)
        general.addRow("Gmail OAuth client file", client_row)
        vault = ("Sign-in credentials are kept in your operating system's secure credential store."
                 if ctx.secrets.secure else
                 "No secure credential store was found. Sign-in will be remembered for this session only; "
                 "nothing is written to disk in plain text.")
        vault_label = QLabel(vault)
        vault_label.setWordWrap(True)
        vault_label.setEnabled(False)

        self.ai_check = QCheckBox("Enable optional AI summaries and drafts")
        self.ai_check.setChecked(s.ai_enabled)
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("API key saved" if ctx.secrets.get(AI_KEY) else "Anthropic API key")
        self.model_edit = QLineEdit(s.ai_model)
        clear_key = QPushButton("Remove saved key")
        clear_key.clicked.connect(self._clear_key)
        notice = QLabel("When you click \u201cSummarize with AI\u201d or choose \u201cUse AI\u201d for a draft, the text of that one email is "
                        "sent to the Anthropic API. Nothing is sent otherwise. Without AI, InboxPilot summarises locally.")
        notice.setWordWrap(True)
        ai_form = QFormLayout()
        ai_form.addRow(self.ai_check)
        ai_form.addRow("API key", self.key_edit)
        ai_form.addRow("Model", self.model_edit)
        ai_form.addRow(clear_key)
        ai_form.addRow(notice)
        ai_group = QGroupBox("AI (optional)")
        ai_group.setLayout(ai_form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(general)
        lay.addWidget(vault_label)
        lay.addWidget(ai_group)
        lay.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose Google OAuth client file", "", "JSON files (*.json)")
        if path:
            self.client_edit.setText(path)

    def _clear_key(self) -> None:
        self.ctx.clear_ai_key()
        self.key_edit.clear()
        self.key_edit.setPlaceholderText("Anthropic API key")

    def _save(self) -> None:
        s = self.ctx.settings
        s.user_name = self.name_edit.text()
        s.sync_days = self.sync_days.value()
        s.follow_up_days = self.follow_days.value()
        s.gmail_client_secrets_path = self.client_edit.text().strip()
        s.ai_enabled = self.ai_check.isChecked()
        s.ai_model = self.model_edit.text() or "claude-sonnet-5-5"
        if self.key_edit.text().strip():
            self.ctx.set_ai_key(self.key_edit.text())
        self.ctx.reset_provider()  # rebuild with the new client file path
        self.accept()
