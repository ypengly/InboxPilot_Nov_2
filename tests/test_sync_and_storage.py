import os
import tempfile
import unittest
from datetime import timedelta, datetime

from helpers import ME, make_app, msg
from inboxpilot.errors import ConfigError
from inboxpilot.models import EmailQuery
from inboxpilot.providers.demo import DEMO_USER
from inboxpilot.storage.db import Database, Repository


def demo_app():
    app = make_app()
    app.connect("demo")
    app.sync()
    return app


class DemoSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = make_app()
        cls.app.connect("demo")
        cls.result = cls.app.sync()

    def q(self, **kw):
        return self.app.search(EmailQuery(**kw))

    def test_sync_counts_and_analysis(self):
        self.assertEqual(self.result.new_messages, 24)
        self.assertEqual(self.result.analyzed, 24)
        self.assertEqual(self.app.user_email, DEMO_USER)

    def test_dashboard_folders_and_stats(self):
        counts = self.app.folder_counts()
        self.assertEqual(set(counts), {"inbox", "important", "needs_reply", "follow_up", "newsletters", "receipts", "unread"})
        stats = self.app.stats()
        self.assertEqual(stats["unread"], counts["unread"])
        self.assertEqual(stats["pending_replies"], counts["needs_reply"])
        self.assertEqual(stats["important"], counts["important"])
        self.assertGreater(stats["recent"], 0)
        self.assertEqual(len(self.q(folder="inbox")), counts["inbox"])

    def test_needs_reply_excludes_answered_cc_and_bulk(self):
        subjects = {r.subject for r in self.q(folder="needs_reply")}
        self.assertIn("Quick question on the Q4 budget", subjects)
        self.assertNotIn("Interview feedback needed", subjects)       # already answered in thread
        self.assertNotIn("Re: Atlas vendor booking", subjects)         # CC only, no action
        self.assertNotIn("Reminder: submit your timesheet", subjects)  # all-staff announcement
        self.assertFalse(any(r.category == "Newsletter" for r in self.q(folder="needs_reply")))

    def test_follow_up_is_old_unanswered_sent_message_only(self):
        self.assertEqual({r.subject for r in self.q(folder="follow_up")}, {"Logo files for the Atlas launch"})

    def test_newsletters_and_receipts(self):
        self.assertTrue(all(r.category == "Newsletter" for r in self.q(folder="newsletters")))
        self.assertTrue(all(r.is_receipt for r in self.q(folder="receipts")))
        self.assertGreaterEqual(len(self.q(folder="receipts")), 2)

    def test_unread_filter_and_search(self):
        self.assertTrue(all(not r.is_read for r in self.q(folder="unread")))
        hits = self.q(text="budget")
        self.assertTrue(hits and all("budget" in (r.subject + r.body_text).lower() for r in hits))
        self.assertEqual(self.q(text="zzzznotpresent"), [])
        self.assertTrue(self.q(text="bud"))  # prefix search

    def test_search_survives_special_characters(self):
        for text in ('"', "AND OR", "foo*(", "a:b", "'; DROP TABLE emails;--", "%", "_"):
            with self.subTest(text=text):
                self.q(text=text)  # must not raise
        self.assertGreater(self.app.stats()["total"], 0)

    def test_sender_category_priority_filters(self):
        self.assertTrue(all("priya" in r.sender_email for r in self.q(sender="priya")))
        self.assertTrue(all(r.category == "Finance" for r in self.q(category="Finance")))
        self.assertTrue(all(r.priority == "High" for r in self.q(priority="High")))
        self.assertEqual(self.q(sender="%"), [])  # wildcard is escaped, not treated as match-all

    def test_date_range(self):
        today = datetime.now().astimezone().date()
        self.assertEqual(len(self.q(date_from=today + timedelta(days=1))), 0)
        self.assertEqual(len(self.q(date_to=today - timedelta(days=365))), 0)
        self.assertGreater(len(self.q(date_from=today - timedelta(days=30), date_to=today)), 0)

    def test_sorting(self):
        by_date = [r.date for r in self.q(sort_by="date")]
        self.assertEqual(by_date, sorted(by_date, reverse=True))
        asc = [r.date for r in self.q(sort_by="date", descending=False)]
        self.assertEqual(asc, sorted(asc))
        senders = [r.sender_display.lower() for r in self.q(sort_by="sender", descending=False)]
        self.assertEqual(senders, sorted(senders))
        rank = {"High": 3, "Medium": 2, "Low": 1}
        pr = [rank[r.priority] for r in self.q(sort_by="priority")]
        self.assertEqual(pr, sorted(pr, reverse=True))
        self.assertFalse(self.q(sort_by="unread")[0].is_read)

    def test_every_row_has_priority_reasons(self):
        for r in self.q(folder="inbox"):
            self.assertTrue(r.priority_reasons, r.subject)
            self.assertIn(r.priority, ("High", "Medium", "Low"))

    def test_resync_is_idempotent_and_keeps_overrides(self):
        app = demo_app()
        rec = app.search(EmailQuery(text="budget", folder="inbox"))[0]
        app.repo.set_override(rec.id, "user_category", "Finance")
        app.repo.set_override(rec.id, "user_priority", "Low")
        app.repo.set_override(rec.id, "user_needs_reply", False)
        self.assertEqual(app.sync().new_messages, 0)
        again = app.get_email(rec.id)
        self.assertEqual((again.category, again.priority, again.needs_reply), ("Finance", "Low", False))
        self.assertEqual(again.auto_category, rec.auto_category)  # automatic value still stored separately
        app.repo.clear_overrides(rec.id)
        self.assertEqual(app.get_email(rec.id).category, rec.auto_category)

    def test_manual_follow_up_flag(self):
        app = demo_app()
        rec = app.search(EmailQuery(folder="inbox"))[0]
        self.assertFalse(rec.follow_up)
        app.repo.set_override(rec.id, "user_follow_up", True)
        self.assertIn(rec.id, {r.id for r in app.search(EmailQuery(folder="follow_up"))})

    def test_user_category_important_appears_in_important_folder(self):
        app = demo_app()
        low = [r for r in app.search(EmailQuery(folder="inbox")) if r.priority == "Low"][0]
        app.repo.set_override(low.id, "user_category", "Important")
        self.assertIn(low.id, {r.id for r in app.search(EmailQuery(folder="important"))})


class RemoteStateTests(unittest.TestCase):
    def test_read_state_mirrors_server_and_removed_mail_is_pruned(self):
        app = demo_app()
        provider = app.provider()
        unread_before = app.stats()["unread"]
        provider._messages["demo-01"].is_read = True          # read elsewhere
        provider._messages["demo-01"].labels = []
        del provider._messages["demo-03"]                      # archived/deleted elsewhere (was unread)
        result = app.sync()
        self.assertEqual(result.removed, 1)
        self.assertEqual(app.stats()["unread"], unread_before - 2)
        self.assertEqual(app.search(EmailQuery(text="renewal")), [])

    def test_prune_keeps_messages_that_have_drafts(self):
        app = demo_app()
        rec = app.search(EmailQuery(text="renewal"))[0]
        app.repo.save_draft(rec.id, "Re: renewal", "my work")
        del app.provider()._messages["demo-03"]
        self.assertEqual(app.sync().removed, 0)
        self.assertEqual(app.repo.get_draft(rec.id).body, "my work")

    def test_disconnect_can_delete_local_data(self):
        app = demo_app()
        app.disconnect(delete_local_data=True)
        self.assertFalse(app.has_account)
        self.assertEqual(app.stats()["total"], 0)
        with self.assertRaises(ConfigError):
            app.sync()


class RepositoryTests(unittest.TestCase):
    def test_fts_fallback_to_like(self):
        db = Database(os.path.join(tempfile.mkdtemp(), "x.db"))
        db.fts = False
        repo = Repository(db)
        acct = repo.upsert_account("demo", ME)
        repo.upsert_email(acct, msg("Quarterly numbers", "see attached spreadsheet", rid="a"))
        repo.upsert_email(acct, msg("Lunch", "pizza?", rid="b"))
        self.assertEqual([r.subject for r in repo.search(acct, EmailQuery(text="spreadsheet"))], ["Quarterly numbers"])

    def test_settings_roundtrip_and_defaults(self):
        app = make_app()
        self.assertEqual(app.settings.sync_days, 90)
        app.settings.sync_days = 30
        app.settings.follow_up_days = 5
        app.settings.ai_enabled = True
        self.assertEqual((app.settings.sync_days, app.settings.follow_up_days, app.settings.ai_enabled), (30, 5, True))
        app.repo.set_setting("sync_days", "banana")
        self.assertEqual(app.settings.sync_days, 90)

    def test_override_field_whitelist(self):
        with self.assertRaises(ValueError):
            make_app().repo.set_override(1, "subject", "x")


if __name__ == "__main__":
    unittest.main()
