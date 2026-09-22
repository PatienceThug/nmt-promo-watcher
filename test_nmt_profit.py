import copy
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch

import nmt_profit as p
import nmt_brain as b
import nmt_strategy as strategy
import nmt_sources as sources

NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)


def evidence():
    return dict(status='ok', source='test-fixture', observed_at=NOW.isoformat(),
                ttl_seconds=300, verification='observed')


def candidate(cid='a', **kwargs):
    return dict(dict(id=cid, kind='flip', evidence=evidence(), buy_nmt='100',
                     owned_opportunity_cost_nmt='0', exit_value_nmt='200',
                     fee_fraction='0.1', other_costs_nmt='5', stress_haircut_fraction='0.2',
                     target_profit_nmt='10', horizon_days='7', asset_ids=[cid],
                     exit_basis='completed_sales'), **kwargs)


class ProfitTests(unittest.TestCase):
    def setUp(self):
        self.sources = {x: evidence() for x in p.SECTIONS}

    def screen(self, c):
        return p.screen(c, self.sources, NOW)

    def test_freshness_and_missing_are_distinct(self):
        self.assertEqual(p.evidence_status(None, NOW), 'missing')
        e = evidence()
        self.assertEqual(p.evidence_status(e, NOW), 'fresh')
        e['observed_at'] = '2026-09-22T11:00:00+00:00'
        self.assertEqual(p.evidence_status(e, NOW), 'stale')
        e['status'] = 'blocked'
        self.assertEqual(p.evidence_status(e, NOW), 'blocked')

    def test_future_and_naive_time_not_fresh(self):
        for t in ('2026-09-23T12:00:00Z', '2026-09-22T12:00:00'):
            self.assertEqual(p.evidence_status(dict(evidence(), observed_at=t), NOW), 'unverified')

    def test_http_success_not_verification(self):
        self.assertEqual(p.evidence_status(dict(evidence(), verification='unreviewed'), NOW), 'unverified')

    def test_stress_and_max_buy_include_fees(self):
        r = self.screen(candidate())
        self.assertTrue(r['eligible'])
        self.assertEqual(r['stressed_profit_nmt'], Decimal('39'))
        self.assertEqual(r['max_buy_nmt'], Decimal('129'))

    def test_missing_fee_not_defaulted(self):
        c = candidate()
        del c['fee_fraction']
        self.assertFalse(self.screen(c)['eligible'])

    def test_stale_price_blocks_opportunity(self):
        c = candidate(evidence=dict(evidence(), observed_at='2026-09-20T12:00:00Z'))
        self.assertFalse(self.screen(c)['eligible'])

    def test_blocked_market_blocks_opportunity(self):
        self.sources['marketplace']['status'] = 'blocked'
        self.assertFalse(self.screen(candidate())['eligible'])

    def test_asking_price_not_profit_evidence(self):
        self.assertFalse(self.screen(candidate(exit_basis='listing'))['eligible'])

    def test_loss_not_eligible(self):
        self.assertFalse(self.screen(candidate(exit_value_nmt='80'))['eligible'])

    def test_owned_figures_have_opportunity_cost(self):
        self.assertFalse(self.screen(candidate(owned_opportunity_cost_nmt='100'))['eligible'])

    def test_collection_claim_and_unpack_constraints(self):
        c = candidate(kind='collection', daily_nmt='10', minimum_claim_charges=2,
                      planned_claims=1, claim_before_unpack=True, exact_slots_verified=True)
        self.assertTrue(self.screen(c)['eligible'])
        for updates in (dict(planned_claims=3), dict(claim_before_unpack=False),
                        dict(exact_slots_verified=False), dict(minimum_claim_charges=0)):
            self.assertFalse(self.screen(dict(c, **updates))['eligible'])

    def test_portfolio_does_not_reuse_figures(self):
        a = self.screen(candidate('a', asset_ids=['shared']))
        b = self.screen(candidate('b', asset_ids=['shared'], exit_value_nmt='210'))
        c = self.screen(candidate('c'))
        r = p.select_portfolio([a, b, c], 210)
        self.assertEqual(set(r['ids']), {'b', 'c'})
        self.assertEqual(r['cash_required_nmt'], 210)

    def test_portfolio_counts_extra_cash_cost(self):
        self.assertEqual(p.select_portfolio([self.screen(candidate())], 100)['ids'], [])

    def test_mixed_horizons_not_ranked_as_equivalent(self):
        with self.assertRaises(ValueError):
            p.select_portfolio([self.screen(candidate()), self.screen(candidate('b', horizon_days=30))], 500)

    def test_deposits_excluded_and_duplicate_income_once(self):
        rows = [dict(id='1', kind='capital_in', amount_nmt='500'),
                dict(id='2', kind='income', amount_nmt='15'),
                dict(id='2', kind='income', amount_nmt='15'),
                dict(id='3', kind='expense', amount_nmt='10'),
                dict(id='4', kind='capital_out', amount_nmt='100')]
        r = p.ledger_summary(rows)
        self.assertEqual(r['operating_cashflow_nmt'], 5)
        self.assertEqual(r['recorded_balance_change_nmt'], 405)
        self.assertEqual(r['duplicates'], 1)
        self.assertIsNone(r['realized_profit_nmt'])

    def test_invalid_and_conflicting_ledger_not_silent(self):
        rows = [dict(id='1', kind='income', amount_nmt='15'),
                dict(id='1', kind='income', amount_nmt='30'),
                dict(id='2', kind='income', amount_nmt='NaN')]
        r = p.ledger_summary(rows)
        self.assertEqual(r['invalid'], 2)
        self.assertFalse(r['complete'])

    def test_nonfinite_rejected_by_all_money_parsers(self):
        for fn in (p.number, b.num, strategy.D):
            for x in ('NaN', 'Infinity', '-Infinity'):
                with self.assertRaises(ValueError):
                    fn(x)

    def test_commands_read_only_no_telegram(self):
        state = {'ledger': []}
        original = copy.deepcopy(state)
        with patch.object(p, 'load_snapshot', return_value={'schema_version': 1}), patch.object(b, 'tg') as tg:
            for cmd in ('/coverage', '/opportunities', '/ledger'):
                self.assertIn('eksik', b.handle(state, 1, cmd))
            tg.assert_not_called()
        self.assertEqual(state, original)

    def test_duplicate_candidate_rejected(self):
        with self.assertRaises(ValueError):
            p.report(dict(schema_version=1, candidates=[candidate(), candidate()]), {}, NOW)

    def test_no_candidates_does_not_mean_no_opportunities(self):
        text = p.render(p.report(dict(schema_version=1), {}, NOW))
        self.assertIn('piyasada fırsat olmadığı anlamına gelmez', text)


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self.body = body or ('<main><h1>Guide</h1><p>' + 'Mechanics explained. ' * 30 + '</p></main>').encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield self.body


class SourceTests(unittest.TestCase):
    def test_blocked_preserves_last_success(self):
        with patch.object(sources.requests, 'get', return_value=FakeResponse(403)):
            r = sources.check('https://nmt.gg/en/academy/collections', {'last_success_hash': 'old'})
        self.assertEqual(r['status'], 'blocked')
        self.assertEqual(r['last_success_hash'], 'old')

    def test_success_is_not_verified_and_change_is_sticky(self):
        with patch.object(sources.requests, 'get', return_value=FakeResponse()):
            r = sources.check('https://nmt.gg/en/academy/collections', {'last_success_hash': 'old'})
            self.assertEqual(r['status'], 'changed')
            r = sources.check('https://nmt.gg/en/academy/collections', r)
        self.assertEqual(r['status'], 'changed')
        self.assertEqual(r['verification'], 'unreviewed')

    def test_challenge_never_counts_as_guide(self):
        body = ('<h1>Just a moment</h1>' + 'Please wait ' * 100).encode()
        with patch.object(sources.requests, 'get', return_value=FakeResponse(body=body)):
            self.assertEqual(sources.check('https://nmt.gg/en/academy/collections')['status'], 'unreadable')


if __name__ == '__main__':
    unittest.main()
