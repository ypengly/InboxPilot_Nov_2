"""Run slow work (network sync, OAuth, AI calls) off the UI thread."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from ..errors import Cancelled, InboxPilotError

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(str, int, int)


class Worker(QRunnable):
    def __init__(self, fn: Callable[..., Any], *args: Any, with_progress: bool = False, with_cancel: bool = False):
        super().__init__()
        self.setAutoDelete(False)  # MainWindow keeps a reference until the result signal arrives
        self._fn, self._args = fn, args
        self._with_progress, self._with_cancel = with_progress, with_cancel
        self.signals = WorkerSignals()
        self.cancel_event = threading.Event()

    def cancel(self) -> None:
        self.cancel_event.set()

    @Slot()
    def run(self) -> None:
        kwargs: dict[str, Any] = {}
        if self._with_progress:
            kwargs["progress"] = lambda stage, cur, total: self.signals.progress.emit(stage, cur, total)
        if self._with_cancel:
            kwargs["cancel"] = self.cancel_event
        try:
            result = self._fn(*self._args, **kwargs)
        except Cancelled:
            self.signals.cancelled.emit()
        except InboxPilotError as exc:
            log.warning("Task failed: %s", exc)
            self.signals.failed.emit(exc.user_message)
        except Exception as exc:  # last resort: never let a worker thread die silently
            log.exception("Unexpected error in background task")
            self.signals.failed.emit(f"Something unexpected went wrong: {exc}\n\nDetails were written to the log file.")
        else:
            self.signals.finished.emit(result)
