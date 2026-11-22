import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'manager'))

import modules.rns_monitor as monitor  # noqa: E402


class SQLiteCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='rns_cache_test_')
        self.cache = monitor.SQLiteAnnounceCache(self.tmp, max_age_days=7, max_size=1000)
        self.base = time.time()
        for i in range(6):
            self.cache.add_announce({
                'id': i,
                'timestamp': self.base + i,
                'dest_hash': ('a%d' % i) * 16,
                'packet_hash': 'p%d' % i,
                'identity_hash': ('1' if i < 4 else '2') * 32,
                'aspect': 'lxmf.delivery' if i < 3 else 'unknown',
                'hops': '?' if i == 5 else str(i % 3),
                'interface': 'TCPInterface[test]',
                'data': 'foo' if i < 2 else 'bar%d' % i,
                'data_length': 4,
                'rssi': -40 - i,
                'snr': 3.0 + i,
                'q': 80,
            })

    def tearDown(self):
        self.cache.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_search_filters_and_sorts(self):
        self.assertEqual(len(self.cache.get_announces(search='foo')), 2)
        self.assertEqual(len(self.cache.get_announces(search='TCPInterface')), 6)
        self.assertEqual([r['aspect'] for r in self.cache.get_announces(aspect='unknown')], ['unknown'] * 3)
        known = [r['aspect'] for r in self.cache.get_announces(aspect='known')]
        self.assertNotIn('unknown', known)
        self.assertEqual(len(known), 3)
        self.assertEqual(self.cache.count_announces(search='foo'), 2)
        self.assertEqual(self.cache.count_announces(aspect='known'), 3)
        self.assertEqual(self.cache.get_announces(sort='rssi_asc')[0]['rssi'], -45)
        self.assertEqual(
            [r['data'] for r in self.cache.get_announces(sort='data_asc')],
            sorted(r['data'] for r in self.cache.get_announces()),
        )

    def test_stats_and_peer_stats(self):
        stats = self.cache.get_stats()
        self.assertEqual(stats['total_announces'], 6)
        self.assertEqual(stats['aspect_counts']['lxmf.delivery'], 3)
        self.assertEqual(len(stats['identities']), 2)
        self.assertEqual(stats['unique_identities'], 2)
        self.assertEqual(sorted(i['aspect_count'] for i in stats['identities']), [1, 2])
        peer = self.cache.get_peer_stats('a0' * 16)
        self.assertEqual(peer['announce_count'], 1)
        self.assertEqual(peer['avg_hops'], 0.0)
        peer_unknown = self.cache.get_peer_stats('a5' * 16)
        self.assertIsNone(peer_unknown['avg_hops'])

    def test_wal_and_clear_preserve_file(self):
        conn = self.cache._connect()
        try:
            self.assertEqual(conn.execute('PRAGMA journal_mode').fetchone()[0].lower(), 'wal')
        finally:
            conn.close()
        db_path = self.cache.db_path
        self.cache.clear()
        self.assertTrue(__import__('os').path.exists(db_path))
        self.assertTrue(self.cache.add_announce({
            'id': 99, 'timestamp': time.time(), 'dest_hash': 'ff' * 16,
            'packet_hash': 'new', 'aspect': 'lxmf.delivery', 'data': 'new',
        }))
        self.assertEqual(self.cache.get_stats()['total_announces'], 1)

    def test_cleanup_scheduled_when_over_capacity(self):
        """Superata la capacità, l'inserimento pianifica il cleanup senza errori."""
        tmp = tempfile.mkdtemp(prefix='rns_cache_cleanup_')
        cache = monitor.SQLiteAnnounceCache(tmp, max_age_days=7, max_size=5)
        try:
            for i in range(250):
                self.assertTrue(cache.add_announce({
                    'id': i,
                    'timestamp': self.base + i,
                    'dest_hash': ('b%d' % (i % 10)) * 16,
                    'packet_hash': 'c%d' % i,
                    'identity_hash': '3' * 32,
                    'aspect': 'lxmf.delivery',
                    'hops': '1',
                    'interface': 'TCPInterface[cleanup]',
                    'data': 'cleanup',
                    'data_length': 7,
                }))
            deadline = time.time() + 5
            while time.time() < deadline and cache.count_announces() > 5:
                time.sleep(0.05)
            self.assertLessEqual(cache.count_announces(), 5)
        finally:
            cache.stop()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_manager_stats_cache_ttl(self):
        manager = types.SimpleNamespace(
            announce_history=[], announce_counter=6, history_lock=threading.Lock(),
            announce_cache=self.cache, monitor_process=None,
            _stats_lock=threading.Lock(), _stats_cache=None, _stats_ts=0,
        )
        calls = {'n': 0}
        original = self.cache.get_stats

        def counting():
            calls['n'] += 1
            return original()

        self.cache.get_stats = counting
        try:
            first = monitor.RNSMonitorManager.get_stats(manager)
            second = monitor.RNSMonitorManager.get_stats(manager)
        finally:
            self.cache.get_stats = original
        self.assertEqual(calls['n'], 1)
        self.assertEqual(first['sqlite_total'], 6)
        self.assertEqual(second['sqlite_total'], 6)


if __name__ == '__main__':
    unittest.main()
