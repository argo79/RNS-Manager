import os
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'messenger'))

if 'gpsdclient' not in sys.modules:
    fake_gps = types.ModuleType('gpsdclient')

    class GPSDClient:
        def __init__(self, *args, **kwargs):
            pass

    fake_gps.GPSDClient = GPSDClient
    sys.modules['gpsdclient'] = fake_gps

if 'flask_socketio' not in sys.modules:
    try:
        import flask_socketio  # noqa: F401
    except ImportError:
        fake_sio = types.ModuleType('flask_socketio')

        class SocketIO:
            def __init__(self, app=None, **kwargs):
                self.app = app
                self.kwargs = kwargs

            def on(self, event):
                return lambda fn: fn

            def emit(self, *args, **kwargs):
                pass

            def run(self, *args, **kwargs):
                pass

        fake_sio.SocketIO = SocketIO
        fake_sio.emit = lambda *args, **kwargs: None
        sys.modules['flask_socketio'] = fake_sio

try:
    import lxmf_chat as chat
    HAS_CHAT = True
except Exception:
    HAS_CHAT = False


class DummyMessenger:
    def __init__(self):
        self.calls = []

    def list_favorite_hashes(self):
        return ['a' * 32]

    def set_peer_favorite(self, identity_hash, favorite):
        self.calls.append(('favorite', identity_hash, favorite))
        return True

    def set_peer_groups(self, identity_hash, groups):
        self.calls.append(('groups', identity_hash, list(groups)))
        return True, ['home']

    def update_peer_settings(self, identity_hash, data):
        self.calls.append(('update', identity_hash, data))
        return True

    def reset_all_groups(self):
        self.calls.append(('reset_groups',))
        return {'identities': 1, 'destinations': 1, 'group_links': 2}

    def announce(self):
        self.calls.append(('announce',))
        return 'a' * 32

    def get_announce_stats(self):
        self.calls.append(('announce_stats',))
        return {
            'total': 3,
            'by_aspect': {'lxmf.delivery': 3},
            'last': {'aspect': 'lxmf.delivery', 'display_name': 'test', 'time': 1},
            'recent': [],
            'handlers': [{'aspect_filter': 'lxmf.delivery', 'receive_path_responses': True}],
            'db_total': 2,
            'db_by_aspect': {'lxmf.delivery': {'count': 2, 'last_seen': 1}},
        }

    def get_telemetry_stats(self):
        self.calls.append(('telemetry_stats',))
        return {
            'requests_sent': 2,
            'responses_received': 1,
            'responses_sent': 0,
            'requests_ignored': 1,
            'last_request': {'destination': 'a' * 32, 'time': 1, 'hash': 'b' * 64},
            'last_response': {'source': 'a' * 32, 'time': 2},
            'last_ignored': {'source': 'a' * 32, 'reason': 'allow_telemetry_false'},
            'allow_telemetry_requests': True,
            'allow_remote_commands': False,
            'last_request_status': 'delivered',
        }

    def send_command(self, dest_hash, command_type, command_data=None, callbacks=None):
        self.calls.append(('send_command', dest_hash, command_type, command_data))
        return {'success': True, 'hash': 'c' * 64}

    def get_hash(self):
        return 'a' * 32

    def get_aspect_hash(self, aspect):
        self.calls.append(('aspect_hash', aspect))
        return 'd' * 32 if aspect == 'lxmf.delivery' else 'e' * 32

    def list_call_history(self, peer_hash=None, limit=50):
        self.calls.append(('call_history', peer_hash, limit))
        return [{
            'id': 1,
            'peer_hash': 'a' * 32,
            'name': 'Peer A',
            'destination_hash': 'b' * 32,
            'direction': 'outgoing',
            'state': 'ended',
            'started_at': 10,
            'answered_at': 11,
            'ended_at': 20,
            'duration': 9,
            'was_connected': True,
            'error': None,
        }]


@unittest.skipUnless(HAS_CHAT, 'RNS/LXMF/LXST/Flask-SocketIO non disponibili')
class MessengerRouteTests(unittest.TestCase):
    def setUp(self):
        chat.messenger = None
        self.client = chat.app.test_client()

    def tearDown(self):
        chat.messenger = None

    def test_web_hardening(self):
        self.assertEqual(self.client.get('/', headers={'Host': 'evil.example'}).status_code, 403)
        response = self.client.get('/', headers={'Host': 'localhost'})
        self.assertEqual(response.status_code, 200)
        self.assertIn("connect-src 'self'", response.headers.get('Content-Security-Policy', ''))
        self.assertEqual(self.client.post('/api/shutdown', headers={'Origin': 'http://evil.example'}).status_code, 403)
        self.assertEqual(self.client.get('/chat', headers={'Host': 'localhost'}).status_code, 200)
        self.assertEqual(getattr(chat.socketio, 'kwargs', {}), {})

    def test_asset_versioning(self):
        response = self.client.get('/', headers={'Host': 'localhost'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'?v={chat.APP_VERSION}'.encode(), response.data)

    def test_config_sanitizer(self):
        with self.assertRaises(ValueError):
            chat._sanitize_config_update({'delivery_mode': 'invalid'})
        with self.assertRaises(ValueError):
            chat._sanitize_config_update({'max_retries': 3})
        with self.assertRaises(ValueError):
            chat._sanitize_config_update({'appearance': ['<img>', '4c9aff', '1a1f2e']})
        with self.assertRaises(ValueError):
            chat._sanitize_config_update({'propagation_node': 'zz'})
        with self.assertRaises(ValueError):
            chat._sanitize_config_update({'propagation_node': 'a' * 64})
        self.assertEqual(
            chat._sanitize_config_update({'propagation_node': 'A' * 32})['propagation_node'],
            'a' * 32,
        )
        with self.assertRaises(ValueError):
            chat._sanitize_config_update({'announce_aspects': 'lxmf.*'})
        clean = chat._sanitize_config_update({
            'delivery_mode': 'direct',
            'stamp_cost': '2',
            'enable_location': '1',
            'appearance': ['antenna', '#4c9aff', '#1A1F2E'],
            'announce_aspects': 'lxmf.delivery, rnstransport.discovery.interface',
        })
        self.assertEqual(clean['announce_aspects'], [
            'lxmf.delivery', 'rnstransport.discovery.interface',
        ])
        self.assertEqual(clean['max_retries'] if 'max_retries' in clean else None, None)
        self.assertEqual(clean['stamp_cost'], 2)
        self.assertTrue(clean['enable_location'])
        self.assertEqual(clean['appearance'], ['antenna', '4c9aff', '1a1f2e'])
        self.assertIsNone(chat._resolve_identity_path('../x'))
        self.assertFalse(chat._is_hex_hash('zz'))

    def test_propagation_route(self):
        chat.messenger = mock.Mock()
        chat.messenger.set_propagation_node.return_value = 'a' * 32
        chat.messenger.config = {}
        headers = {'Host': 'localhost', 'Origin': 'http://localhost:5012'}
        with mock.patch.object(chat, '_save_config') as save_config:
            response = self.client.post(
                '/api/propagation',
                json={'node': 'A' * 32},
                headers=headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(response.get_json()['propagation_node'], 'a' * 32)
        chat.messenger.set_propagation_node.assert_called_once_with('a' * 32)
        save_config.assert_called_once_with(chat.messenger)

        response = self.client.post(
            '/api/propagation',
            json={'node': 'zz'},
            headers=headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()['success'])

    def test_peer_routes(self):
        chat.messenger = DummyMessenger()
        identity = 'a' * 32
        headers = {'Host': 'localhost', 'Origin': 'http://localhost:5012'}
        self.assertEqual(self.client.get('/api/favorites', headers={'Host': 'localhost'}).get_json(), [identity])
        response = self.client.post(f'/api/peer/{identity}/favorite', json={'favorite': True}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['favorite'])
        response = self.client.post(f'/api/peer/{identity}/groups', json={'groups': ['home']}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['groups'], ['home'])
        self.assertEqual(self.client.post('/api/peer/update', json={'identity_hash': identity, 'group': 'work'}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post('/api/favorites/add', json={'peer_hash': identity}, headers=headers).status_code, 200)
        self.assertEqual(self.client.post('/api/favorites/remove', json={'peer_hash': identity}, headers=headers).status_code, 200)
        response = self.client.post('/api/groups/reset', json={}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(response.get_json()['group_links'], 2)
        self.assertIn(('reset_groups',), chat.messenger.calls)
        self.assertEqual(self.client.post('/api/peer/zz/favorite', json={'favorite': True}, headers=headers).status_code, 400)
        rules = {str(rule) for rule in chat.app.url_map.iter_rules()}
        self.assertIn('/api/peer/<identity_hash>/favorite', rules)
        self.assertIn('/api/peer/<identity_hash>/groups', rules)

    def test_aspect_hash_and_telephony_history_routes(self):
        chat.messenger = DummyMessenger()
        headers = {'Host': 'localhost'}
        identity = 'a' * 32

        response = self.client.get(
            '/api/aspect_hash?aspect=lxst.telephony', headers=headers
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['aspect'], 'lxst.telephony')
        self.assertEqual(payload['identity_hash'], identity)
        self.assertEqual(payload['aspect_hash'], 'e' * 32)
        self.assertEqual(payload['label'], 'LXST · Telephony')

        self.assertEqual(
            self.client.get('/api/aspect_hash?aspect=bad!', headers=headers).status_code,
            400,
        )

        response = self.client.get(
            f'/api/telephony/history?peer_hash={identity}&limit=10',
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload['success'])
        self.assertEqual(payload['calls'][0]['name'], 'Peer A')
        self.assertIn(('call_history', identity, 10), chat.messenger.calls)
        self.assertEqual(
            self.client.get('/api/telephony/history?peer_hash=zz', headers=headers).status_code,
            400,
        )

    def test_reticulum_config_routes(self):
        headers = {'Host': 'localhost', 'Origin': 'http://localhost:5012'}
        with mock.patch.object(chat, 'list_reticulum_interfaces', return_value={
            'config_path': '/tmp/reticulum/config',
            'interfaces': [{'name': 'RNode A', 'type': 'RNodeInterface', 'enabled': True}],
        }) as list_interfaces:
            response = self.client.get('/api/reticulum/interfaces', headers={'Host': 'localhost'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertEqual(response.get_json()['interfaces'][0]['name'], 'RNode A')
        list_interfaces.assert_called_once_with()

        with mock.patch.object(chat, 'add_interface', return_value={
            'created': True,
            'requires_restart': True,
            'interface': {'name': 'RNode A', 'type': 'RNodeInterface'},
        }) as add_interface:
            response = self.client.post(
                '/api/reticulum/interface',
                json={'action': 'add', 'type': 'RNodeInterface', 'name': 'RNode A'},
                headers=headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['created'])
        add_interface.assert_called_once()

        with mock.patch.object(chat, 'set_reticulum_interface_enabled', return_value={
            'enabled': False,
            'disabled': True,
            'requires_restart': True,
            'interface': {'name': 'RNode A', 'type': 'RNodeInterface', 'enabled': False},
        }) as set_enabled:
            response = self.client.post(
                '/api/reticulum/interface',
                json={'action': 'set_enabled', 'name': 'RNode A', 'type': 'RNodeInterface', 'enabled': False},
                headers=headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['disabled'])
        set_enabled.assert_called_once_with('RNode A', False, expected_type='RNodeInterface')

        with mock.patch.object(chat, 'set_reticulum_interface_enabled', return_value={
            'enabled': True,
            'disabled': False,
            'requires_restart': True,
            'interface': {'name': 'RNode A', 'type': 'RNodeInterface', 'enabled': True},
        }) as set_enabled:
            response = self.client.post(
                '/api/reticulum/interface',
                json={'action': 'set_enabled', 'name': 'RNode A', 'type': 'RNodeInterface', 'enabled': True},
                headers=headers,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['enabled'])
        set_enabled.assert_called_once_with('RNode A', True, expected_type='RNodeInterface')

        response = self.client.post(
            '/api/reticulum/interface',
            json={'action': 'invalid'},
            headers=headers,
        )
        self.assertEqual(response.status_code, 400)

    def test_rns_status_route(self):
        headers = {'Host': 'localhost'}
        with mock.patch.object(chat.shutil, 'which', return_value='/usr/bin/rnstatus') as which:
            with mock.patch.object(chat.subprocess, 'run') as run:
                run.return_value = mock.Mock(
                    returncode=0,
                    stdout=chr(27) + '[32mPath: OK' + chr(27) + '[0m' + chr(10),
                    stderr='',
                )
                response = self.client.get('/api/rns/status', headers=headers)
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertTrue(data['available'])
        self.assertEqual(data['output'], 'Path: OK')
        run.assert_called_once_with(
            ['/usr/bin/rnstatus'],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

        with mock.patch.object(chat.shutil, 'which', return_value=None):
            response = self.client.get('/api/rns/status', headers=headers)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()['available'])

    def test_command_route_accepts_rangetest(self):
        chat.messenger = DummyMessenger()
        headers = {'Host': 'localhost', 'Origin': 'http://localhost:5012'}
        destination = 'a' * 32

        response = self.client.post(
            '/api/command',
            json={'destination': destination, 'command': 'rangetest', 'text': '15 start'},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertIn(
            ('send_command', destination, chat.Commands.CUSTOM, 'rangetest 15 start'),
            chat.messenger.calls,
        )

        invalid = self.client.post(
            '/api/command',
            json={'destination': destination, 'command': 'rangetest', 'text': 'not-valid'},
            headers=headers,
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(invalid.get_json()['success'])

    def test_discovery_peers_route_returns_location(self):
        from RNS.vendor import umsgpack
        with tempfile.TemporaryDirectory() as tmp:
            obj = chat.Messenger.__new__(chat.Messenger)
            obj.peers_db = os.path.join(tmp, 'peers.db')
            obj.db_lock = threading.Lock()
            chat.Messenger._init_peers_db(obj)
            identity = 'a' * 32
            destination = 'b' * 32
            packed = umsgpack.packb({
                0x00: 'BackboneInterface',
                0x01: True,
                0xFF: 'Node Alpha',
                0x03: 45.4642,
                0x04: 9.1900,
                0x05: 120.0,
                0x02: 'example.org',
                0x06: 4242,
            })
            app_data = (b'\x00' + packed + b'\x00' * 32).hex()
            conn = sqlite3.connect(obj.peers_db)
            c = conn.cursor()
            c.execute(
                'INSERT INTO identities (identity_hash, display_name, first_seen, last_seen) VALUES (?,?,?,?)',
                (identity, 'Node Alpha', 1, 1),
            )
            c.execute(
                'INSERT INTO destinations (destination_hash, identity_hash, aspect, last_seen, app_data) VALUES (?,?,?,?,?)',
                (destination, identity, chat.DISCOVERY_ASPECT, 1, app_data),
            )
            conn.commit()
            conn.close()

            chat.messenger = obj
            data = self.client.get(
                f'/api/peers?aspect={chat.DISCOVERY_ASPECT}',
                headers={'Host': 'localhost'},
            ).get_json()
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]['announce_info']['type'], 'BackboneInterface')
            self.assertEqual(data[0]['last_location']['latitude'], 45.4642)
            self.assertEqual(data[0]['last_location']['source'], 'discovery')

            map_data = self.client.get(
                '/api/map/peers',
                headers={'Host': 'localhost'},
            ).get_json()
            self.assertEqual(len(map_data), 1)
            self.assertEqual(map_data[0]['source'], 'interface')
            self.assertEqual(map_data[0]['interface_type'], 'BackboneInterface')

    def test_announce_route(self):
        chat.messenger = DummyMessenger()
        headers = {'Host': 'localhost', 'Origin': 'http://localhost:5012'}
        response = self.client.post('/api/announce', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        self.assertIn(('announce',), chat.messenger.calls)

        stats = self.client.get('/api/announce/stats', headers={'Host': 'localhost'})
        self.assertEqual(stats.status_code, 200)
        data = stats.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['total'], 3)
        self.assertEqual(data['db_total'], 2)
        self.assertTrue(data['handlers'][0]['receive_path_responses'])
        self.assertIn(('announce_stats',), chat.messenger.calls)

        telemetry = self.client.get('/api/telemetry/stats', headers={'Host': 'localhost'})
        self.assertEqual(telemetry.status_code, 200)
        tdata = telemetry.get_json()
        self.assertTrue(tdata['success'])
        self.assertEqual(tdata['requests_sent'], 2)
        self.assertEqual(tdata['responses_received'], 1)
        self.assertEqual(tdata['last_request_status'], 'delivered')
        self.assertTrue(tdata['allow_telemetry_requests'])
        self.assertIn(('telemetry_stats',), chat.messenger.calls)

    def test_codec2_attachment_conversion(self):
        if not chat.CODEC2_AVAILABLE:
            self.skipTest('Codec2 non disponibile')
        import numpy as np
        rate = 8000
        samples = (np.sin(2 * np.pi * 440 * np.arange(rate // 4) / rate) * 12000).astype(np.int16)
        for mode in (4, 9):
            encoded = chat.audio_codec.encode_codec2(samples, mode)
            self.assertTrue(encoded)
            data, mime, filename = chat._prepare_audio_attachment(encoded, mode)
            self.assertEqual(mime, 'audio/wav')
            self.assertTrue(data.startswith(b'RIFF'))
            self.assertTrue(filename.endswith('.wav'))

    def test_aspects_and_exit_routes(self):
        data = self.client.get('/api/aspects', headers={'Host': 'localhost'}).get_json()
        self.assertIn('lxmf.delivery', data['aspects'])
        self.assertIn('rnstransport.discovery.interface', data['aspects'])
        self.assertEqual(data['labels']['rnstransport.discovery.interface'],
                         'Reticulum · Interface Discovery')
        self.assertIn('rnstransport.discovery.interface', data['map_capable'])

        with mock.patch.object(chat, '_cleanup_messenger', return_value=(True, None)), \
                mock.patch.dict(chat.os.environ, {'LXMF_EXIT_DRY_RUN': '1'}):
            response = self.client.post(
                '/api/exit',
                headers={'Host': 'localhost', 'Origin': 'http://localhost:5012'},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['dry_run'])
        self.assertIn('/api/exit', {str(rule) for rule in chat.app.url_map.iter_rules()})

    def test_peer_update_event(self):
        if not hasattr(chat.socketio, 'test_client'):
            self.skipTest('Socket.IO test client non disponibile')
        client = chat.socketio.test_client(chat.app)
        self.assertTrue(client.is_connected())
        chat.announce_callback({
            'aspect': 'lxmf.delivery',
            'identity_hash': 'a' * 32,
            'destination_hash': 'b' * 32,
            'display_name': 'test',
            'hops': 1,
            'time': 0,
        })
        names = [event['name'] for event in client.get_received()]
        self.assertIn('peer_update', names)
        client.disconnect()

    def test_optional_auth_token(self):
        old_token = chat.AUTH_TOKEN
        chat.AUTH_TOKEN = 'test-secret'
        try:
            self.assertEqual(self.client.get('/', headers={'Host': 'localhost'}).status_code, 401)
            self.assertEqual(self.client.get('/static/favicon.ico', headers={'Host': 'localhost'}).status_code, 200)
            response = self.client.get('/?token=test-secret', headers={'Host': 'localhost'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('lxmf_auth', response.headers.get('Set-Cookie', ''))
            self.assertEqual(self.client.get('/api/status', headers={'Host': 'localhost'}).status_code, 200)
            self.assertEqual(
                self.client.get('/api/status', headers={'Host': 'localhost', 'X-Auth-Token': 'wrong'}).status_code,
                401,
            )
        finally:
            chat.AUTH_TOKEN = old_token


if __name__ == '__main__':
    unittest.main()
