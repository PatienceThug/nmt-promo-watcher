import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import telegram_radar as radar
import watcher_v4
import youtube_watcher as youtube


def page(number, date=None):
    date = date or datetime.now(timezone.utc).isoformat()
    return f'<div class="tgme_widget_message" data-post="nmt_official/{number}"><time datetime="{date}"></time><div class="tgme_widget_message_text">promo code: TEST{number}</div></div>'


class PagingTests(unittest.TestCase):
    def test_next_page_uses_oldest_id_and_deduplicates(self):
        with patch.object(watcher_v4.w, 'fetch_html', side_effect=[page(30)+page(29), page(29)+page(28), page(27, '2020-01-01T00:00:00Z')]) as fetch:
            posts, pages, warning = radar.read_posts('https://t.me/s/nmt_official', watcher_v4.w, 3)
        self.assertEqual((len(posts), pages, warning), (4, 3, None))
        self.assertEqual(fetch.call_args_list[1].args[0], 'https://t.me/s/nmt_official?before=29')

    def test_old_page_stops_backfill(self):
        with patch.object(watcher_v4.w, 'fetch_html', return_value=page(1, '2020-01-01T00:00:00Z')) as fetch:
            radar.read_posts('https://t.me/s/nmt_official', watcher_v4.w, 3)
        self.assertEqual(fetch.call_count, 1)

    def test_failed_backfill_keeps_recent_page(self):
        with patch.object(watcher_v4.w, 'fetch_html', side_effect=[page(30), RuntimeError('timeout')]):
            posts, pages, warning = radar.read_posts('https://t.me/s/nmt_official', watcher_v4.w, 3)
        self.assertEqual(len(posts), 1)
        self.assertEqual(warning, 'timeout')

    def test_first_page_failure_is_not_hidden(self):
        with patch.object(watcher_v4.w, 'fetch_html', return_value='<html>unavailable</html>'):
            with self.assertRaises(RuntimeError):
                radar.read_posts('https://t.me/s/nmt_official', watcher_v4.w, 3)

    def test_repeat_page_stops(self):
        with patch.object(watcher_v4.w, 'fetch_html', return_value=page(30)) as fetch:
            posts, _, _ = radar.read_posts('https://t.me/s/nmt_official', watcher_v4.w, 3)
        self.assertEqual(len(posts), 1)
        self.assertEqual(fetch.call_count, 2)

    def test_youtube_keeps_fresh_metadata(self):
        items = [{'title': 'NMT.GG promo code: NEW123', 'timestamp': datetime.now(timezone.utc).timestamp(), 'webpage_url': 'https://www.youtube.com/watch?v=test', 'duration': 60}]
        events = youtube.youtube_metadata_events(items)
        self.assertEqual(events[0]['code'], 'NEW123')
        self.assertTrue(youtube.eligible(events))

    def test_fast_youtube_has_no_requests(self):
        with patch.object(youtube, 'discover_youtube_items', side_effect=AssertionError('network')):
            self.assertEqual(youtube.scan('fast', {}), [])

    def test_youtube_state_migration_preserves_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'youtube_state.json'
            target.write_text(json.dumps({'initialized': True, 'seen_event_ids': ['seen'], 'last_alert': {'NEW123': 'time'}, 'source_health': {'retired': {}}}))
            with patch.object(youtube, 'STATE_PATH', target):
                data = youtube.load_state()
            self.assertEqual(data['seen_event_ids'], ['seen'])
            self.assertNotIn('source_health', data)


if __name__ == '__main__':
    unittest.main()
