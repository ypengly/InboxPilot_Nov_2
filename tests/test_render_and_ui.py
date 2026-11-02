import importlib.util
import os
import unittest

from helpers import make_app
from inboxpilot.models import EmailQuery
from inboxpilot.ui import render


def demo_app():
    app = make_app()
    app.connect("demo")
    app.sync()
    return app


class RenderTests(unittest.TestCase):
    def test_insights_html_has_all_sections(self):
        app = demo_app()
        rec = app.search(EmailQuery(text="budget"))[0]
        html = render.render_insights_html(rec, app.summarize(rec))
        for heading in ("Priority:", "Reasons:", "Needs Reply", "Summary", "Key Points", "Action Items", "Deadline",
                        "People Mentioned", "Suggested Response"):
            self.assertIn(heading, html)
        self.assertIn("Question detected in message.", html)

    def test_no_deadline_text_is_rendered(self):
        app = demo_app()
        rec = app.search(EmailQuery(text="dinner"))[0]
        self.assertIn("No deadline identified.", render.render_summary_html(app.summarize(rec)))

    def test_override_is_labelled(self):
        app = demo_app()
        rec = app.search(EmailQuery(folder="inbox"))[0]
        app.repo.set_override(rec.id, "user_priority", "Low")
        self.assertIn("set by you", render.render_priority_html(app.get_email(rec.id)))

    def test_html_is_escaped(self):
        app = demo_app()
        rec = app.search(EmailQuery(text="budget"))[0]
        rec.subject = "<script>alert(1)</script>"
        rec.followup_reason, rec.needs_reply = "<b>x</b>", True
        self.assertNotIn("<script>", render.render_followup_html(rec))


@unittest.skipUnless(importlib.util.find_spec("PySide6"), "PySide6 not installed")
class UISmokeTests(unittest.TestCase):
    def test_main_window_loads_demo_data(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from inboxpilot.ui.main_window import MainWindow
        qapp = QApplication.instance() or QApplication([])
        app = demo_app()
        win = MainWindow(app)
        win.refresh()
        self.assertGreater(win.model.rowCount(), 0)
        win.table.selectRow(0)
        self.assertIsNotNone(win.detail.rec)
        win.close()
        del qapp


if __name__ == "__main__":
    unittest.main()
