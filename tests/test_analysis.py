import unittest

from helpers import ME, ctx, msg
from inboxpilot.analysis.categorizer import categorize
from inboxpilot.analysis.drafts import generate_local_draft, reply_subject
from inboxpilot.analysis.followup import assess_followup
from inboxpilot.analysis.priority import format_priority, score_priority
from inboxpilot.analysis.summarizer import summarize_local
from inboxpilot.analysis.textutils import html_to_text, strip_quoted


class TextTests(unittest.TestCase):
    def test_html_to_text_skips_scripts_and_styles(self):
        t = html_to_text("<html><head><style>p{}</style></head><body><p>Hello <b>world</b></p><script>x()</script><li>One</li></body></html>")
        self.assertIn("Hello world", t)
        self.assertNotIn("x()", t)
        self.assertIn("- One", t)

    def test_strip_quoted_removes_earlier_thread(self):
        body = "Sounds good.\n\nOn Tue, Oct 6, 2026 at 9:14 AM Dana <d@x.example> wrote:\n> Can you send the file?\n> Thanks"
        self.assertEqual(strip_quoted(body), "Sounds good.")
        self.assertEqual(strip_quoted("Hi\n> quoted line\nBye"), "Hi\nBye")


class CategoryTests(unittest.TestCase):
    def cat(self, **kw):
        return categorize(msg(**kw), ctx()).category

    def test_categories(self):
        self.assertEqual(self.cat(subject="Your weekly digest", body="news... unsubscribe", sender="News <news@site.example>", unsub=True), "Newsletter")
        self.assertEqual(self.cat(subject="Invoice 123 due", sender="Billing <billing@paypal.example>"), "Finance")
        self.assertEqual(self.cat(subject="Your order has shipped", sender="Amazon <no-reply@amazon.example>"), "Shopping")
        self.assertEqual(self.cat(subject="Your itinerary", sender="Air <b@skyway-airlines.example>"), "Travel")
        self.assertEqual(self.cat(subject="Sam mentioned you", sender="LinkedIn <n@linkedin.example>"), "Social")
        self.assertEqual(self.cat(subject="Security alert", sender="G <no-reply@google.example>"), "Notifications")
        self.assertEqual(self.cat(subject="Agenda for the project sync", sender="Dana <dana@work.example>"), "Work")
        self.assertEqual(self.cat(subject="Dinner Sunday?", sender="Mum <mum@mail.example>"), "Personal")

    def test_receipt_flag_and_reasons(self):
        r = categorize(msg(subject="Your receipt from Acme", sender="Acme <pay@acme.example>"), ctx())
        self.assertTrue(r.is_receipt)
        self.assertTrue(r.reasons)

    def test_same_company_domain_is_work(self):
        self.assertEqual(self.cat(subject="Hi there", sender="Colleague <c@work.example>"), "Work")


class PriorityTests(unittest.TestCase):
    def test_high_priority_has_explanations(self):
        m = msg(subject="Review needed", body="Could you review the plan? Please send feedback by Friday 5pm.")
        res = score_priority(m, ctx())
        self.assertEqual(res.level, "High")
        texts = [r.text for r in res.reasons if r.positive]
        for expected in ("Directly addressed to you", "Contains a requested action", "Mentions a deadline"):
            self.assertIn(expected, texts)
        out = format_priority(res.level, res.reasons)
        self.assertTrue(out.startswith("Priority: High"))
        self.assertIn("\u2713 Directly addressed to you", out)

    def test_bulk_mail_is_low_and_says_why(self):
        m = msg(subject="Sale!", body="Please shop now. Unsubscribe", sender="Deals <deals@shop.example>", unsub=True)
        res = score_priority(m, ctx())
        self.assertEqual(res.level, "Low")
        self.assertTrue(any(not r.positive for r in res.reasons))

    def test_cc_only_is_downweighted(self):
        m = msg(body="Please review this.", to=[("Other", "o@x.example")], cc=[("Me", ME)])
        self.assertLess(score_priority(m, ctx()).score, score_priority(msg(body="Please review this."), ctx()).score)

    def test_sent_mail_not_prioritised(self):
        self.assertEqual(score_priority(msg(sent=True, body="Hello?"), ctx()).level, "Low")


class FollowUpTests(unittest.TestCase):
    def test_question_detected(self):
        r = assess_followup(msg(subject="Project update", body="Hi,\n\nWhen can you share the numbers?"), ctx())
        self.assertTrue(r.needs_reply)
        self.assertEqual(r.reason, "Question detected in message.")
        self.assertIn("share the numbers", r.evidence)

    def test_no_reply_needed_for_fyi(self):
        self.assertFalse(assess_followup(msg(body="FYI the vendor call is booked. No action needed."), ctx()).needs_reply)

    def test_newsletter_and_automated_never_need_reply(self):
        self.assertFalse(assess_followup(msg(body="Questions? Reply to this email. Can you help us grow?", unsub=True), ctx()).needs_reply)
        self.assertFalse(assess_followup(msg(body="Can you confirm?", sender="Bot <noreply@x.example>"), ctx()).needs_reply)

    def test_quoted_question_is_ignored(self):
        body = "Thanks, done.\n\nOn Mon, Oct 5, 2026 at 1:00 PM Pat <p@o.example> wrote:\n> Are you free?"
        self.assertFalse(assess_followup(msg(body=body), ctx()).needs_reply)

    def test_sent_message_waiting_for_answer(self):
        r = assess_followup(msg(sent=True, to=[("E", "e@x.example")], body="Could you send the logo files?"), ctx())
        self.assertTrue(r.awaiting_reply)


class SummaryAndDraftTests(unittest.TestCase):
    def test_no_deadline_text(self):
        s = summarize_local(msg(body="Hi,\n\nThe report is attached. Let me know what you think.\n\nThanks"), ctx())
        self.assertEqual(s.deadline, "No deadline identified.")

    def test_summary_fields_only_use_email_content(self):
        body = "Hi Alex,\n\nWe launch on Friday. Dana owns the budget of $12,000. Could you confirm the vendor by Thursday?\n\nBest,\nPat"
        s = summarize_local(msg(body=body), ctx())
        joined = " ".join([s.summary] + s.key_points + s.action_items)
        for fragment in ("$12,000", "Thursday"):
            self.assertIn(fragment, joined)
        self.assertIn("Thursday", s.deadline)
        self.assertTrue(any("Pat" in p for p in s.people))
        self.assertIn("Pat", s.suggested_response)

    def test_empty_body_is_honest(self):
        s = summarize_local(msg(subject="Ping", body=""), ctx())
        self.assertIn("no readable text", s.summary)
        self.assertEqual(s.action_items, ["No action items identified."])

    def test_draft_is_editable_template_with_placeholders(self):
        m = msg(subject="Project update", body="Hi Alex,\n\nWhen can you share the numbers? Need them by Friday.", sender="Pat Smith <pat@o.example>")
        d = generate_local_draft(m, ctx())
        self.assertEqual(d.subject, "Re: Project update")
        self.assertTrue(d.body.startswith("Hi Pat,"))
        self.assertIn("[add your answer here]", d.body)
        self.assertIn("Me Myself", d.body)
        self.assertEqual(reply_subject("Re: x"), "Re: x")


if __name__ == "__main__":
    unittest.main()
