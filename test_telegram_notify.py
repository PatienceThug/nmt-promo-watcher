import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import telegram_notify as n


def issue(number, code):
    return {"number": number, "title": "🚨 NMT PROMO: " + code}


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.p = patch.object(n, "STATE_PATH", self.path)
        self.p.start()
        self.addCleanup(self.p.stop)

    @patch.object(n, "send_telegram", return_value=True)
    def test_migrate_existing_history_and_cross_source_duplicates(self, send):
        state = {"initialized": True, "sent_issue_numbers": [36]}
        n.notify_issues(state, [issue(36, "ASS22ZA234"), issue(37, "ASS22ZA234"),
                                issue(38, "NEW123"), issue(39, "new123")], "test")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.kwargs["copy_code"], "NEW123")
        n.notify_issues(n.load_state(), [issue(40, "ASS22ZA234"), issue(41, "NEW123")], "test")
        self.assertEqual(send.call_count, 1)
        self.assertIn(41, n.load_state()["sent_issue_numbers"])

    @patch.object(n, "send_telegram", return_value=True)
    def test_health_rate_limit_does_not_block_new_codes(self, send):
        alerts = [{"number": i, "title": f"⚠️ NMT WATCHER HEALTH: Workflow {i}"} for i in (1, 2, 3)]
        state = {"sent_issue_numbers": []}
        n.notify_issues(state, alerts + [issue(4, "NEW456")], "test")
        self.assertEqual(send.call_count, 2)

    @patch.object(n, "send_telegram", side_effect=RuntimeError("delivery failed"))
    def test_failed_delivery_not_marked_sent(self, send):
        state = {"sent_issue_numbers": []}
        with self.assertRaises(RuntimeError):
            n.notify_issues(state, [issue(1, "NEW123")], "test")
        self.assertEqual(state["sent_issue_numbers"], [])
        self.assertNotIn("NEW123", state.get("sent_codes", []))

    def test_corrupt_state_does_not_reset_history(self):
        self.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            n.load_state()


if __name__ == "__main__":
    unittest.main()
