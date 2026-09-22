import unittest
from persist_state import merge_brain_state


class BrainStateMergeTests(unittest.TestCase):
    def test_newer_profile_replaces_old_profile(self):
        old = {
            "version": 2,
            "updated_at": "2026-09-22T06:00:00+00:00",
            "last_update_id": 10,
            "strategy": {"footprints": [1,1,1,1,1]},
            "ledger": [],
            "power_rounds": [],
        }
        new = {
            "version": 2,
            "updated_at": "2026-09-22T07:00:00+00:00",
            "last_update_id": 11,
            "strategy": {"footprints": [25]},
            "ledger": [],
            "power_rounds": [],
        }
        merged = merge_brain_state(old, new)
        self.assertEqual(merged["strategy"]["footprints"], [25])
        self.assertEqual(merged["last_update_id"], 11)

    def test_event_rows_are_not_lost(self):
        old = {
            "updated_at": "2026-09-22T06:00:00+00:00",
            "ledger": [{"id": "a", "amount_nmt": "10"}],
            "power_rounds": [{"id": "r1", "reward_nmt": "0"}],
        }
        new = {
            "updated_at": "2026-09-22T07:00:00+00:00",
            "ledger": [{"id": "b", "amount_nmt": "20"}],
            "power_rounds": [{"id": "r2", "reward_nmt": "15"}],
        }
        merged = merge_brain_state(old, new)
        self.assertEqual({x["id"] for x in merged["ledger"]}, {"a", "b"})
        self.assertEqual({x["id"] for x in merged["power_rounds"]}, {"r1", "r2"})

    def test_older_worker_cannot_overwrite_new_settings(self):
        newer_remote = {
            "updated_at": "2026-09-22T08:00:00+00:00",
            "settings": {"daily_outflow_limit_nmt": "5000"},
            "strategy": {"footprints": [100]},
            "ledger": [],
            "power_rounds": [],
        }
        stale_incoming = {
            "updated_at": "2026-09-22T07:00:00+00:00",
            "settings": {"daily_outflow_limit_nmt": "1000"},
            "strategy": {"footprints": [5]},
            "ledger": [],
            "power_rounds": [],
        }
        merged = merge_brain_state(newer_remote, stale_incoming)
        self.assertEqual(merged["settings"]["daily_outflow_limit_nmt"], "5000")
        self.assertEqual(merged["strategy"]["footprints"], [100])


if __name__ == "__main__":
    unittest.main()
