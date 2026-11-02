from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from ..models import EmailRecord
from .render import PRIORITY_COLORS, format_list_date

COLUMNS = ("", "Priority", "From", "Subject", "Category", "Flags", "Date")
SORT_KEYS = {0: "unread", 1: "priority", 2: "sender", 3: "subject", 4: "category", 6: "date"}


class EmailTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[EmailRecord] = []

    def set_records(self, records: list[EmailRecord]) -> None:
        self.beginResetModel()
        self._rows = records
        self.endResetModel()

    def record(self, row: int) -> EmailRecord | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def row_of(self, email_id: int) -> int:
        return next((i for i, r in enumerate(self._rows) if r.id == email_id), -1)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    @staticmethod
    def _flags(r: EmailRecord) -> str:
        bits = []
        if r.needs_reply:
            bits.append("Reply")
        if r.follow_up:
            bits.append("Follow up")
        if r.is_receipt:
            bits.append("Receipt")
        if r.has_attachments:
            bits.append("Attachment")
        return " \u00b7 ".join(bits)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, col = self._rows[index.row()], index.column()
        unread = not r.is_read and not r.is_sent
        if role == Qt.ItemDataRole.DisplayRole:
            return ("\u25cf" if unread else "", r.priority if not r.is_sent else "", r.counterparty_display,
                    r.display_subject, r.category, self._flags(r), format_list_date(r.date))[col]
        if role == Qt.ItemDataRole.FontRole and unread:
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == 1:
                return QColor(PRIORITY_COLORS.get(r.priority, "#7a8088"))
            if col == 0:
                return QColor("#2f6fed")
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (0, 1):
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.ToolTipRole:
            return (r.snippet or "")[:300]
        if role == Qt.ItemDataRole.UserRole:
            return r.id
        return None
