"""Pure, testable NMT strategy math.

All mechanics here are sourced from nmt_rules.json. This module never logs in to
NMT and never sends gameplay actions.
"""
from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from pathlib import Path

RULES_PATH = Path(__file__).with_name("nmt_rules.json")
RULES = json.loads(RULES_PATH.read_text(encoding="utf-8"))

PB = RULES["power_blocks"]
BOARD = int(PB["board_cells"])
WIN_MIN = int(PB["winning_cells_min"])
WIN_MID = int(PB["winning_cells_mid"])
WIN_MAX = int(PB["winning_cells_max"])
PAY = Decimal(str(PB["nmt_per_hit"]))
MARKET_FEE = Decimal(str(RULES["marketplace"]["fee_fraction"]))


def D(value) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Geçerli bir sayı gir.")


def fmt(value, places=4) -> str:
    value = D(value)
    s = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return s or "0"


def parse_footprint(token: str) -> int:
    t = token.lower().replace("×", "x").strip()
    if "x" in t:
        left, right = t.split("x", 1)
        w, h = int(left), int(right)
        if w <= 0 or h <= 0:
            raise ValueError("Footprint pozitif olmalı.")
        area = w * h
    else:
        area = int(t)
    if area <= 0 or area > BOARD:
        raise ValueError("Footprint alanı 1–10000 arasında olmalı.")
    return area


def normalize_areas(tokens) -> list[int]:
    areas = [parse_footprint(x) for x in tokens]
    if not areas:
        raise ValueError("En az bir footprint gir.")
    if len(areas) > int(PB["max_slots"]):
        raise ValueError(f"En fazla {PB['max_slots']} slot destekleniyor.")
    if sum(areas) > BOARD:
        raise ValueError("Toplam alan tahtadan büyük olamaz.")
    return areas


def hit_probability(area: int, winning_cells: int) -> Decimal:
    """Probability that at least one covered cell is a winner."""
    area = int(area)
    winning_cells = int(winning_cells)
    if area <= 0:
        return Decimal("0")
    if area >= BOARD or winning_cells >= BOARD:
        return Decimal("1")
    if winning_cells <= 0:
        return Decimal("0")

    miss = 1.0
    for i in range(area):
        miss *= (BOARD - winning_cells - i) / (BOARD - i)
        if miss <= 0:
            return Decimal("1")
    return Decimal(str(1.0 - miss))


def expected_hits(area: int, winning_cells: int) -> Decimal:
    return D(area) * D(winning_cells) / D(BOARD)


def expected_nmt(area: int, winning_cells: int) -> Decimal:
    return expected_hits(area, winning_cells) * PAY


def rounds_for_hours(hours) -> Decimal:
    return D(hours) * Decimal("3600") / D(PB["round_seconds_approx"])


def profile_metrics(areas: list[int], hours=12) -> dict:
    area = sum(int(x) for x in areas)
    rounds = rounds_for_hours(hours)
    out = {
        "slots": len(areas),
        "area": area,
        "power_per_settle": area,
        "rounds": rounds,
    }
    for label, wins in (("min", WIN_MIN), ("mid", WIN_MID), ("max", WIN_MAX)):
        p = hit_probability(area, wins)
        ev = expected_nmt(area, wins)
        out[label] = {
            "wins": wins,
            "hit_probability": p,
            "ev_per_round": ev,
            "ev_period": ev * rounds,
        }
    return out


def zero_streak_probability(area: int, rounds: int, winning_cells=WIN_MID) -> Decimal:
    rounds = int(rounds)
    if rounds < 0:
        raise ValueError("Round sayısı negatif olamaz.")
    p = hit_probability(area, winning_cells)
    return (Decimal("1") - p) ** rounds


def target_area_for_income(target_usd, usd_per_nmt, hours=12, winning_cells=WIN_MID) -> Decimal:
    target_usd = D(target_usd)
    usd_per_nmt = D(usd_per_nmt)
    if target_usd <= 0 or usd_per_nmt <= 0:
        raise ValueError("Hedef ve kur 0'dan büyük olmalı.")
    nmt_needed = target_usd / usd_per_nmt
    rounds = rounds_for_hours(hours)
    nmt_per_area_per_round = D(winning_cells) / D(BOARD) * PAY
    return nmt_needed / (rounds * nmt_per_area_per_round)


def upgrade_break_even(current_area, extra_area, cost_nmt, winning_cells=WIN_MID) -> dict:
    current = int(current_area)
    extra = int(extra_area)
    cost = D(cost_nmt)
    if current < 0 or extra <= 0 or current + extra > BOARD or cost < 0:
        raise ValueError("Upgrade değerleri geçersiz.")
    extra_daily = expected_nmt(extra, winning_cells) * rounds_for_hours(24)
    days = cost / extra_daily if extra_daily > 0 else Decimal("Infinity")
    return {
        "current_area": current,
        "new_area": current + extra,
        "extra_area": extra,
        "cost_nmt": cost,
        "extra_ev_day_nmt": extra_daily,
        "naive_break_even_days": days,
    }


def flip_profit(buy_price, resale_price, fee=MARKET_FEE) -> dict:
    buy = D(buy_price)
    resale = D(resale_price)
    fee = D(fee)
    if buy < 0 or resale < 0 or fee < 0 or fee >= 1:
        raise ValueError("Flip değerleri geçersiz.")
    net_sale = resale * (Decimal("1") - fee)
    profit = net_sale - buy
    roi = profit / buy if buy > 0 else Decimal("0")
    return {"buy": buy, "resale": resale, "net_sale": net_sale, "profit": profit, "roi": roi}


def merge_profit(input_cost_a, input_cost_b, next_level_value, fee=MARKET_FEE) -> dict:
    a, b, value = D(input_cost_a), D(input_cost_b), D(next_level_value)
    if min(a, b, value) < 0:
        raise ValueError("Merge değerleri negatif olamaz.")
    total = a + b
    net_sale = value * (Decimal("1") - D(fee))
    profit = net_sale - total
    roi = profit / total if total > 0 else Decimal("0")
    return {"input_cost": total, "net_sale": net_sale, "profit": profit, "roi": roi}


def collection_break_even(acquisition_cost_nmt, daily_nmt) -> Decimal:
    cost, daily = D(acquisition_cost_nmt), D(daily_nmt)
    if cost < 0 or daily <= 0:
        raise ValueError("Collection maliyeti >=0, günlük NMT >0 olmalı.")
    return cost / daily


def evidence_grade(rounds: int) -> str:
    rounds = int(rounds)
    if rounds < 30:
        return "çok küçük örneklem"
    if rounds < 100:
        return "erken veri"
    if rounds < 300:
        return "orta güven"
    return "güçlü kişisel örneklem"


KNOWN_POWER_THRESHOLDS = {
    "1x2": (448, 2),
    "2x2": (896, 4),
    "5x5": (5600, 25),
    "10x10": (22400, 100),
    "15x15": (50400, 225),
}


def market_break_even_resale(buy_price, fee=MARKET_FEE) -> Decimal:
    buy = D(buy_price)
    fee = D(fee)
    if buy < 0 or fee < 0 or fee >= 1:
        raise ValueError("Marketplace değerleri geçersiz.")
    return buy / (Decimal("1") - fee)


def footprint_band_runway(current_power, footprint: str) -> dict:
    key = footprint.lower().replace("×", "x").strip()
    if key not in KNOWN_POWER_THRESHOLDS:
        raise ValueError("Bu footprint için resmî eşik snapshot'ı yok. Destek: 1x2, 2x2, 5x5, 10x10, 15x15.")
    threshold, area = KNOWN_POWER_THRESHOLDS[key]
    power = D(current_power)
    if power < threshold:
        return {"footprint": key, "threshold": threshold, "area": area, "rounds": 0, "mid_ev_nmt": Decimal("0")}
    rounds = int((power - D(threshold)) // D(area)) + 1
    mid_ev = expected_nmt(area, WIN_MID) * D(rounds)
    return {
        "footprint": key,
        "threshold": threshold,
        "area": area,
        "rounds": rounds,
        "mid_ev_nmt": mid_ev,
    }


def nft_pb_screen(price_nmt, current_power, footprint: str) -> dict:
    price = D(price_nmt)
    if price < 0:
        raise ValueError("NFT fiyatı negatif olamaz.")
    r = footprint_band_runway(current_power, footprint)
    gross = r["mid_ev_nmt"]
    recovery = gross / price if price > 0 else Decimal("0")
    r.update({"price_nmt": price, "band_gross_ev_nmt": gross, "price_recovery_ratio": recovery})
    return r


def diagnose_rounds(rewards, area: int, winning_cells=WIN_MID) -> dict:
    vals = [D(x) for x in rewards]
    area = int(area)
    if area <= 0:
        raise ValueError("Alan pozitif olmalı.")
    rounds = len(vals)
    hits = sum((v / PAY for v in vals), Decimal("0"))
    expected = D(rounds) * expected_hits(area, winning_cells)
    p_cell = D(winning_cells) / D(BOARD)
    trials = rounds * area
    variance = float(D(trials) * p_cell * (Decimal("1") - p_cell)) if trials else 0.0
    std = math.sqrt(max(variance, 0.0))
    z = (float(hits - expected) / std) if std > 0 else 0.0
    trailing_zero = 0
    for value in reversed(vals):
        if value == 0:
            trailing_zero += 1
        else:
            break
    non_multiple = [v for v in vals if (v % PAY) != 0]
    return {
        "rounds": rounds,
        "area": area,
        "actual_hits": hits,
        "expected_hits": expected,
        "approx_z": z,
        "trailing_zero_rounds": trailing_zero,
        "trailing_zero_probability_mid": zero_streak_probability(area, trailing_zero, winning_cells),
        "non_15_multiple_rewards": len(non_multiple),
        "grade": evidence_grade(rounds),
    }
