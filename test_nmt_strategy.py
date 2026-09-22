import unittest
from decimal import Decimal
import nmt_strategy as s


class StrategyTests(unittest.TestCase):
    def test_parse_footprints(self):
        self.assertEqual(s.parse_footprint("5x5"), 25)
        self.assertEqual(s.parse_footprint("10×10"), 100)
        self.assertEqual(s.parse_footprint("1"), 1)

    def test_five_cells_probability(self):
        p100 = s.hit_probability(5, 100)
        p190 = s.hit_probability(5, 190)
        self.assertGreater(p100, Decimal("0.049"))
        self.assertLess(p100, Decimal("0.050"))
        self.assertGreater(p190, Decimal("0.091"))
        self.assertLess(p190, Decimal("0.092"))

    def test_five_cells_ev_12h(self):
        m = s.profile_metrics([1,1,1,1,1], 12)
        self.assertEqual(m["area"], 5)
        self.assertEqual(m["min"]["ev_period"], Decimal("54.00"))
        self.assertEqual(m["mid"]["ev_period"], Decimal("78.3000"))
        self.assertEqual(m["max"]["ev_period"], Decimal("102.6000"))

    def test_target_area(self):
        area = s.target_area_for_income("21", "0.00408", 12)
        self.assertGreater(area, Decimal("328"))
        self.assertLess(area, Decimal("330"))

    def test_upgrade_break_even(self):
        r = s.upgrade_break_even(5, 20, 1000)
        self.assertEqual(r["new_area"], 25)
        self.assertGreater(r["extra_ev_day_nmt"], 0)

    def test_flip_fee(self):
        r = s.flip_profit(1000, 1500)
        self.assertEqual(r["net_sale"], Decimal("1350.0"))
        self.assertEqual(r["profit"], Decimal("350.0"))

    def test_merge_fee(self):
        r = s.merge_profit(400, 400, 1200)
        self.assertEqual(r["net_sale"], Decimal("1080.0"))
        self.assertEqual(r["profit"], Decimal("280.0"))

    def test_zero_streak(self):
        p = s.zero_streak_probability(5, 72)
        self.assertGreater(p, 0)
        self.assertLess(p, Decimal("0.01"))

    def test_market_break_even_resale(self):
        self.assertEqual(s.market_break_even_resale(1000), Decimal("1111.111111111111111111111111"))

    def test_nft_power_band_screen(self):
        r = s.nft_pb_screen(5000, 7000, "5x5")
        self.assertEqual(r["area"], 25)
        self.assertEqual(r["rounds"], 57)
        self.assertGreater(r["band_gross_ev_nmt"], Decimal("300"))
        self.assertLess(r["price_recovery_ratio"], Decimal("0.1"))

    def test_band_below_threshold(self):
        r = s.footprint_band_runway(5599, "5x5")
        self.assertEqual(r["rounds"], 0)
        self.assertEqual(r["mid_ev_nmt"], Decimal("0"))

    def test_round_diagnostics_zero_run(self):
        d = s.diagnose_rounds([0] * 72, 5)
        self.assertEqual(d["rounds"], 72)
        self.assertEqual(d["actual_hits"], Decimal("0"))
        self.assertEqual(d["trailing_zero_rounds"], 72)
        self.assertLess(d["trailing_zero_probability_mid"], Decimal("0.01"))
        self.assertLess(d["approx_z"], -2)


if __name__ == "__main__":
    unittest.main()
