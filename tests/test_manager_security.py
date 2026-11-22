import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANAGER_DIR = ROOT / 'manager'
CACHE_DIR = tempfile.mkdtemp(prefix='rns_manager_test_')
os.environ['RNS_MANAGER_SKIP_MONITOR'] = '1'
os.environ['RNS_MANAGER_CACHE_DIR'] = CACHE_DIR
sys.path.insert(0, str(MANAGER_DIR))

import rns_manager as manager  # noqa: E402


def tearDownModule():
    try:
        manager.monitor_manager.stop()
    except Exception:
        pass
    socket_path = MANAGER_DIR / 'rns_monitor.sock'
    try:
        if socket_path.exists():
            socket_path.unlink()
    except OSError:
        pass
    shutil.rmtree(CACHE_DIR, ignore_errors=True)


class ManagerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = manager.app.test_client()

    def test_host_and_headers(self):
        self.assertEqual(self.client.get('/', headers={'Host': 'evil.example'}).status_code, 403)
        response = self.client.get('/', headers={'Host': 'localhost'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Content-Security-Policy', response.headers)
        self.assertEqual(response.headers.get('X-Frame-Options'), 'DENY')

    def test_csrf_origin(self):
        response = self.client.post('/api/shutdown', headers={'Origin': 'http://evil.example'})
        self.assertEqual(response.status_code, 403)
        response = self.client.post('/api/monitor/clear', headers={'Origin': 'http://evil.example'})
        self.assertEqual(response.status_code, 403)

    def test_input_hardening(self):
        self.assertEqual(self.client.post('/api/rnid', json={'command': 'rnid -g /tmp/x'}).status_code, 403)
        self.assertEqual(self.client.post('/api/cleanup/temp', json={'temp_path': '/etc/hostname'}).status_code, 400)
        self.assertEqual(self.client.get('/api/rns/paths?dest=../../etc/passwd').status_code, 400)
        self.assertEqual(self.client.post('/api/rns/probe', json={'destination': 'zz'}).status_code, 400)
        self.assertEqual(self.client.post('/api/rns/paths/blackhole', json={'destination': 'abc'}).status_code, 400)

    def test_helpers(self):
        self.assertEqual(manager._safe_identity_name('../../evil', 'x'), 'evil')
        self.assertEqual(manager._safe_identity_name('/etc/passwd', 'x'), 'passwd')
        self.assertFalse(manager._is_allowed_identity_path('/etc/passwd'))
        self.assertTrue(manager._hex_arg('a' * 32, 32, 64))
        self.assertFalse(manager._hex_arg('zz', 32, 64))

    def test_single_reset_route(self):
        rules = [str(rule) for rule in manager.app.url_map.iter_rules()]
        self.assertEqual(rules.count('/api/monitor/reset-all'), 1)
        self.assertEqual(self.client.get('/api/monitor', headers={'Host': 'localhost'}).status_code, 200)

    def test_optional_auth_token(self):
        old_token = manager.AUTH_TOKEN
        manager.AUTH_TOKEN = 'test-secret'
        try:
            self.assertEqual(self.client.get('/', headers={'Host': 'localhost'}).status_code, 401)
            self.assertEqual(self.client.get('/static/favicon.ico', headers={'Host': 'localhost'}).status_code, 200)
            response = self.client.get('/?token=test-secret', headers={'Host': 'localhost'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('rns_auth', response.headers.get('Set-Cookie', ''))
            self.assertEqual(self.client.get('/api/monitor/stats', headers={'Host': 'localhost'}).status_code, 200)
            self.assertEqual(
                self.client.get('/api/monitor/stats', headers={'Host': 'localhost', 'X-Auth-Token': 'wrong'}).status_code,
                401,
            )
        finally:
            manager.AUTH_TOKEN = old_token


if __name__ == '__main__':
    unittest.main()
