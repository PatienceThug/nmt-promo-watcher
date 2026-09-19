import unittest
from datetime import timedelta
from unittest.mock import Mock, patch

import requests

import social_watcher_v2 as v2
import social_watcher_v3 as v3


class XBackoffTests(unittest.TestCase):
    def test_retry_after_is_bounded(self):
        response = Mock(headers={"Retry-After": "1"})
        self.assertEqual(v2._retry_after_seconds(response), 300)
        response.headers = {"Retry-After": "999999"}
        self.assertEqual(v2._retry_after_seconds(response), 6 * 3600)

    @patch.object(v2, "_scan_public_reader", return_value=[])
    @patch.object(v2.requests, "get")
    def test_429_uses_fallback_and_records_backoff(self, get, reader):
        response = Mock(status_code=429, headers={"Retry-After": "900"})
        response.raise_for_status.side_effect = requests.HTTPError("429 Too Many Requests")
        get.return_value = response
        self.assertEqual(v2.scan_official_x_free(), [])
        self.assertEqual(v2.LAST_X_HEALTH["primary"], "rate_limited")
        self.assertEqual(v2.LAST_X_HEALTH["retry_after_seconds"], 900)
        self.assertEqual(v2.LAST_X_HEALTH["fallback"], "ok")

    @patch.object(v2, "_scan_public_reader", return_value=[])
    @patch.object(v2.requests, "get")
    def test_cooldown_never_calls_syndication(self, get, reader):
        self.assertEqual(v2.scan_official_x_free(skip_syndication=True), [])
        get.assert_not_called()
        self.assertEqual(v2.LAST_X_HEALTH["primary"], "cooldown")

    def test_persisted_cooldown_is_active(self):
        state = {"source_health": {"x_official": {
            "cooldown_until": (v3.NOW + timedelta(minutes=5)).isoformat()
        }}}
        self.assertTrue(v3._x_cooldown_active(state))


if __name__ == "__main__":
    unittest.main()
