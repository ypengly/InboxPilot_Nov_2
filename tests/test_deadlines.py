import unittest
from datetime import date

from helpers import REF
from inboxpilot.analysis.deadlines import NO_DEADLINE, describe_deadline, extract_deadline, resolve_when


class DeadlineTests(unittest.TestCase):
    def test_none_when_absent(self):
        self.assertIsNone(extract_deadline("Let's catch up sometime. Thanks for the update.", REF))
        self.assertEqual(describe_deadline(None), "No deadline identified.")
        self.assertEqual(NO_DEADLINE, "No deadline identified.")

    def test_weekday_with_time_does_not_swallow_next_word(self):
        d = extract_deadline("We need sign-off by Friday 5pm so Priya can book the vendor.", REF)
        self.assertEqual(d.matched, "by Friday 5pm")
        self.assertEqual(d.resolved, date(2026, 10, 9))

    def test_various_forms(self):
        cases = {
            "Please reply by tomorrow.": date(2026, 10, 8),
            "Due on March 14, 2027 at 3pm.": date(2027, 3, 14),
            "Submit before 2026-10-20.": date(2026, 10, 20),
            "Deadline: October 30": date(2026, 10, 30),
            "Send it by EOD.": date(2026, 10, 7),
            "No later than 25/10/2026": date(2026, 10, 25),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(extract_deadline(text, REF).resolved, expected)

    def test_ambiguous_numeric_date_is_not_guessed(self):
        d = extract_deadline("Please finish by 10/12.", REF)
        self.assertIsNotNone(d)
        self.assertIsNone(d.resolved)

    def test_relative_window_without_date(self):
        d = extract_deadline("Please respond within 48 hours.", REF)
        self.assertEqual(d.matched.lower(), "within 48 hours")
        self.assertIsNone(d.resolved)

    def test_false_positives(self):
        for text in ("I will call before the meeting starts.", "Wait until then.", "He sat down by the window."):
            with self.subTest(text=text):
                self.assertIsNone(extract_deadline(text, REF))

    def test_next_weekday(self):
        self.assertEqual(resolve_when("next monday", REF.date()), date(2026, 10, 12))
        self.assertEqual(resolve_when("friday", REF.date()), date(2026, 10, 9))

    def test_invalid_date_returns_none(self):
        self.assertIsNone(resolve_when("february 31", REF.date()))


if __name__ == "__main__":
    unittest.main()
