import base64
import json
import os
import unittest
from unittest.mock import patch

import requests
from bs4 import BeautifulSoup
import watcher_v4 as v4
import telegram_radar as radar
import telegram_notify as notify
from persist_state import persist_files


class RadarTests(unittest.TestCase):
    def codes(self, html, official=False):
        return radar.post_codes(BeautifulSoup(html, 'html.parser'), official, v4.w)

    def test_copyable_code_after_emoji(self):
        self.assertIn('DROP2026', self.codes('NMT.GG promo code 🎁 <code>DROP2026</code>'))

    def test_unrelated_game_never_alerts(self):
        self.assertEqual({}, self.codes('OtherGame promo code: OTHER123'))

    def test_link_proves_nmt_context(self):
        self.assertIn('DROP2026', self.codes('<a href="https://nmt.gg">Play</a> promo code: DROP2026'))

    def test_official_channel_without_brand_in_post(self):
        self.assertIn('DROP2026', self.codes('promo code 🎁 <code>DROP2026</code>', True))

    def test_prose_wallet_and_missing_label_rejected(self):
        for html in ('NMT promo code: YATIRIM', 'NMT <code>WALLET123</code>',
                     'NMT promo code <code>some ordinary words</code>'):
            self.assertEqual({}, self.codes(html))

    def test_stale_formatted_code_still_filtered(self):
        event = v4.w.make_event('OLD123', 'NMT', 'https://t.me/nmt_official/1', '', '2020-01-01T00:00:00Z', 'telegram')
        self.assertFalse(v4.w.eligible_group([event], False))

    def test_delivery_error_does_not_claim_scan_failed(self):
        text = notify.alert_message({'title': '⚠️ NMT WATCHER HEALTH: Workflow delivery',
                                     'body': 'durum kaydı başarısız oldu.'})
        self.assertNotIn('taraması başarısız', text)
        self.assertIn('durum kaydı', text)


class PersistenceTests(unittest.TestCase):
    @patch.dict(os.environ, {'GITHUB_REPOSITORY': 'test/repo', 'GITHUB_TOKEN': 'test'})
    def test_ref_conflict_remerges_latest_state_without_force(self):
        state = {'head': 1, 'patches': 0}
        trees = []
        def api(method, path, **kwargs):
            if path == '/git/ref/heads/main':
                return {'object': {'sha': str(state['head'])}}
            if path.startswith('/git/commits/'):
                return {'tree': {'sha': 'base'}}
            if path.startswith('/contents/'):
                data = {'sent_codes': ['REMOTE1'] if state['head'] == 1 else ['REMOTE1', 'REMOTE2']}
                return {'content': base64.b64encode(json.dumps(data).encode()).decode()}
            if path == '/git/trees':
                trees.append(kwargs['json'])
                return {'sha': 'tree'}
            if path == '/git/commits':
                return {'sha': 'commit'}
            if path == '/git/refs/heads/main':
                self.assertFalse(kwargs['json']['force'])
                state['patches'] += 1
                if state['patches'] == 1:
                    state['head'] = 2
                    response = requests.Response(); response.status_code = 422
                    raise requests.HTTPError(response=response)
                return {}
            self.fail(path)
        persist_files({'telegram_state.json': {'sent_codes': ['LOCAL1']}}, api, lambda _: None)
        data = json.loads(trees[-1]['tree'][0]['content'])
        self.assertEqual(data['sent_codes'], ['REMOTE1', 'REMOTE2', 'LOCAL1'])
        self.assertEqual(state['patches'], 2)

    @patch.dict(os.environ, {'GITHUB_REPOSITORY': 'test/repo', 'GITHUB_TOKEN': 'test'})
    def test_permissions_fail_without_retry(self):
        calls = []
        def api(*args, **kwargs):
            calls.append(args)
            response = requests.Response(); response.status_code = 403
            raise requests.HTTPError(response=response)
        with self.assertRaises(requests.HTTPError):
            persist_files({'telegram_state.json': {}}, api, lambda _: self.fail('retried'))
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
