import unittest
import nmt_brain as b


class BrainMathTests(unittest.TestCase):
    def test_power_blocks_ev_range(self):
        text = b.ev_calc(["25"])
        self.assertIn("3.75", text)
        self.assertIn("7.125", text)
        self.assertIn("25 Power", text)

    def test_power_blocks_ev_fixed_wins(self):
        text = b.ev_calc(["25", "150"])
        self.assertIn("5.625", text)

    def test_collection_accrual(self):
        text = b.collection_calc(["100", "7"])
        self.assertIn("700 NMT", text)
        self.assertIn("10 claim", text)

    def test_lucky_buy_house_edge(self):
        text = b.lucky_calc(["1000", "100"])
        self.assertIn("%10", text)
        self.assertIn("%9", text)
        self.assertIn("-10 NMT", text)

    def test_ledger_deduplicates_update(self):
        state = {"ledger": []}
        b.add_entry(state, 42, "income", "power_blocks", "120")
        b.add_entry(state, 42, "income", "power_blocks", "120")
        self.assertEqual(len(state["ledger"]), 1)

    def test_round_efficiency_logging(self):
        state = {
            "ledger": [], "power_rounds": [],
            "strategy": {"footprints": [1,1,1,1,1], "profile_updated_at": ""},
            "settings": {"daily_outflow_limit_nmt": "0", "manual_usd_per_nmt": "0"},
            "power": {"enabled": True, "interval_minutes": 10, "next_at": "", "last_sent_at": "", "last_placed_at": ""}
        }
        text = b.handle(state, 99, "/round 120 25")
        self.assertIn("4.8 NMT/Power", text)
        self.assertEqual(len(state["power_rounds"]), 1)
        self.assertEqual(len(state["ledger"]), 1)

    def test_five_cell_profile_report(self):
        state = {"strategy": {"footprints": [1,1,1,1,1]}}
        text = b.strategy_profile_text(state, 12)
        self.assertIn("Toplam footprint: 5 kare", text)
        self.assertIn("54", text)
        self.assertIn("102.6", text)

    def test_market_min_report(self):
        text = b.market_min_text("1000")
        self.assertIn("1111.1111", text)
        self.assertIn("11.11", text)

    def test_nftcheck_report(self):
        text = b.nft_check_text("5000", "7000", "5x5")
        self.assertIn("57", text)
        self.assertIn("5x5", text)
        self.assertIn("geri kazanımı", text)

    def test_plan_flags_tiny_profile(self):
        state = {
            "strategy": {"footprints": [1,1,1,1,1]},
            "power_rounds": [],
        }
        text = b.plan_text(state)
        self.assertIn("kapasiten çok küçük", text)
        self.assertIn("/nftcheck", text)

    def test_pbset_command(self):
        state = {
            "ledger": [], "power_rounds": [],
            "strategy": {"footprints": [], "profile_updated_at": ""},
            "settings": {"daily_outflow_limit_nmt": "0", "manual_usd_per_nmt": "0"},
            "power": {"enabled": True, "interval_minutes": 10, "next_at": "", "last_sent_at": "", "last_placed_at": ""}
        }
        text = b.handle(state, 100, "/pbset 1 1 1 1 1")
        self.assertEqual(state["strategy"]["footprints"], [1,1,1,1,1])
        self.assertIn("PB profili kaydedildi", text)


if __name__ == "__main__":
    unittest.main()
