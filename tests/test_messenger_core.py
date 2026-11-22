import math
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'messenger'))

# gpsdclient e' opzionale/assente in CI: stub minimale per importare il backend
if 'gpsdclient' not in sys.modules:
    fake_gps = types.ModuleType('gpsdclient')

    class GPSDClient:
        def __init__(self, *args, **kwargs):
            pass

    fake_gps.GPSDClient = GPSDClient
    sys.modules['gpsdclient'] = fake_gps

try:
    import RNS  # noqa: F401
    import LXMF  # noqa: F401
    import LXST  # noqa: F401
    HAS_RNS = True
except Exception:
    HAS_RNS = False

if HAS_RNS:
    import numpy as np
    import messenger as M
    import core.telemeter as telemeter
    from RNS.vendor import umsgpack


@unittest.skipUnless(HAS_RNS, 'RNS/LXMF/LXST non installati')
class MessengerCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='messenger_core_')
        self.msg = M.Messenger.__new__(M.Messenger)
        self.msg.peers_db = os.path.join(self.tmp, 'peers.db')
        self.msg.db_lock = threading.Lock()
        self.msg.announces_cache_db = os.path.join(self.tmp, 'missing.db')
        M.Messenger._init_peers_db(self.msg)
        self.ids = ['a' * 32, 'b' * 32, 'c' * 32]
        self.dests = ['d' * 32, 'e' * 32]
        conn = sqlite3.connect(self.msg.peers_db)
        c = conn.cursor()
        for i, ident in enumerate(self.ids[:2]):
            c.execute(
                'INSERT INTO identities (identity_hash, display_name, first_seen, last_seen) VALUES (?,?,?,?)',
                (ident, 'Peer %d' % i, 1, 1),
            )
            c.execute(
                "INSERT INTO destinations (destination_hash, identity_hash, aspect, last_seen) VALUES (?,?,?,?)",
                (self.dests[i], ident, 'lxmf.delivery', 1),
            )
        conn.commit()
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_peer_preferences_and_groups(self):
        peers = {p['hash']: p for p in self.msg.list_peers()}
        self.assertEqual(len(peers), 2)
        self.assertEqual(peers[self.ids[0]]['groups'], [])
        self.assertIsNone(peers[self.ids[0]]['group'])
        self.assertFalse(peers[self.ids[0]]['favorite'])
        self.assertTrue(self.msg.set_peer_favorite(self.ids[0], True))
        self.assertEqual(self.msg.list_favorite_hashes(), [self.ids[0]])
        found, groups = self.msg.set_peer_groups(self.ids[0], ['home', 'work'])
        self.assertTrue(found)
        self.assertEqual(groups, ['home', 'work'])
        peer = {p['hash']: p for p in self.msg.list_peers()}[self.ids[0]]
        self.assertEqual(peer['groups'], ['home', 'work'])
        self.assertEqual(peer['group'], 'home')
        self.assertTrue(self.msg.update_peer_settings(self.ids[0], {
            'groups': ['friends', 'work'], 'favorite': True, 'trust_level': 2,
            'allow_commands': True, 'allow_telemetry': True, 'notes': 'nota',
        }))
        conn = sqlite3.connect(self.msg.peers_db)
        c = conn.cursor()
        c.execute('SELECT group_name, is_favorite, notes FROM identities WHERE identity_hash=?', (self.ids[0],))
        self.assertEqual(c.fetchone(), ('friends', 1, 'nota'))
        c.execute('SELECT trust_level, allow_commands, allow_telemetry, group_name FROM destinations WHERE identity_hash=?', (self.ids[0],))
        self.assertEqual(c.fetchone(), (2, 1, 1, 'friends'))
        conn.close()

        found, groups = self.msg.set_peer_groups(self.ids[0], [])
        self.assertTrue(found)
        self.assertEqual(groups, [])
        peer = {p['hash']: p for p in self.msg.list_peers()}[self.ids[0]]
        self.assertEqual(peer['groups'], [])
        self.assertIsNone(peer['group'])
        conn = sqlite3.connect(self.msg.peers_db)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM peer_groups WHERE identity_hash=?', (self.ids[0],))
        self.assertEqual(c.fetchone()[0], 0)
        conn.close()

        self.assertTrue(self.msg.update_peer_settings(self.ids[1], {'trust_level': 1}))
        with self.assertRaises(ValueError):
            self.msg.set_peer_groups(self.ids[0], ['invalid-group'])

    def test_reset_all_groups(self):
        self.msg.set_peer_groups(self.ids[0], ['home', 'work'])
        self.msg.set_peer_groups(self.ids[1], ['friends'])
        result = self.msg.reset_all_groups()
        self.assertGreaterEqual(result['group_links'], 3)

        peers = {p['hash']: p for p in self.msg.list_peers()}
        self.assertEqual(peers[self.ids[0]]['groups'], [])
        self.assertEqual(peers[self.ids[1]]['groups'], [])
        self.assertIsNone(peers[self.ids[0]]['group'])

        conn = sqlite3.connect(self.msg.peers_db)
        c = conn.cursor()
        c.execute('SELECT group_name FROM identities WHERE identity_hash IN (?, ?)', (self.ids[0], self.ids[1]))
        self.assertEqual(c.fetchall(), [(None,), (None,)])
        c.execute('SELECT COUNT(*) FROM peer_groups')
        self.assertEqual(c.fetchone()[0], 0)
        conn.close()

    def test_legacy_migration(self):
        legacy = tempfile.mktemp(suffix='.db', dir=self.tmp)
        conn = sqlite3.connect(legacy)
        c = conn.cursor()
        c.execute('CREATE TABLE identities (identity_hash TEXT PRIMARY KEY, display_name TEXT, first_seen REAL, last_seen REAL, notes TEXT)')
        c.execute('''CREATE TABLE destinations (destination_hash TEXT PRIMARY KEY, identity_hash TEXT, aspect TEXT, display_name TEXT, group_name TEXT DEFAULT 'public', notes TEXT, last_seen REAL, hops INTEGER, rssi REAL, snr REAL, q REAL, app_data TEXT, appearance TEXT, last_latitude REAL, last_longitude REAL, last_altitude REAL, last_speed REAL, last_bearing REAL, last_accuracy REAL, last_battery REAL, last_battery_charging INTEGER, last_temperature REAL, last_humidity REAL, last_pressure REAL, last_light REAL, last_uptime REAL, last_processor REAL, last_ram REAL, last_nvm REAL, last_telemetry_time REAL, trust_level INTEGER DEFAULT 0, allow_telemetry INTEGER DEFAULT 0, allow_commands INTEGER DEFAULT 0, last_telemetry_request REAL, last_command_executed REAL, last_command_result TEXT, messages_sent INTEGER DEFAULT 0, messages_received INTEGER DEFAULT 0, telemetry_count INTEGER DEFAULT 0, last_message_time REAL, is_favorite INTEGER DEFAULT 0)''')
        c.execute('CREATE TABLE favorites (identity_hash TEXT PRIMARY KEY, added_at REAL)')
        c.execute('INSERT INTO identities VALUES (?,?,?,?,?)', (self.ids[0], 'Legacy', 1, 1, None))
        c.execute("INSERT INTO destinations (destination_hash, identity_hash, aspect, group_name, is_favorite) VALUES (?,?,?,?,?)", (self.dests[0], self.ids[0], 'lxmf.delivery', 'home', 1))
        c.execute('INSERT INTO favorites VALUES (?,?)', (self.ids[0], 1))
        conn.commit()
        conn.close()
        obj = M.Messenger.__new__(M.Messenger)
        obj.peers_db = legacy
        obj.db_lock = threading.Lock()
        M.Messenger._init_peers_db(obj)
        conn = sqlite3.connect(legacy)
        c = conn.cursor()
        c.execute('SELECT group_name, is_favorite FROM identities WHERE identity_hash=?', (self.ids[0],))
        self.assertEqual(c.fetchone(), (None, 1))
        c.execute('SELECT COUNT(*) FROM favorites')
        self.assertEqual(c.fetchone()[0], 0)
        c.execute('SELECT COUNT(*) FROM peer_groups')
        self.assertEqual(c.fetchone()[0], 0)
        conn.close()

    def test_retention_and_raw_cap(self):
        msg = M.Messenger.__new__(M.Messenger)
        msg.peers_db = os.path.join(self.tmp, 'retention.db')
        msg.db_lock = threading.Lock()
        M.Messenger._init_peers_db(msg)
        msg.raw_dir = os.path.join(self.tmp, 'raw')
        msg.telemetry_dir = os.path.join(self.tmp, 'telemetry')
        msg.rangetest_dir = os.path.join(self.tmp, 'rangetest')
        msg.conversations_dir = os.path.join(self.tmp, 'conversations')
        msg.attachments_dir = os.path.join(self.tmp, 'attachments')
        for d in (msg.raw_dir, msg.telemetry_dir, msg.rangetest_dir, msg.conversations_dir, msg.attachments_dir):
            os.makedirs(d, exist_ok=True)
        msg.config = {
            'message_retention_days': 1, 'raw_retention_days': 1,
            'telemetry_retention_days': 1, 'rangetest_retention_days': 1,
        }
        old = time.time() - 10 * 86400
        now = time.time()
        conn = sqlite3.connect(msg.peers_db)
        c = conn.cursor()
        c.execute("INSERT INTO messages (hash, source_hash, destination_hash, content, timestamp) VALUES ('old','a','b','x',?)", (old,))
        c.execute("INSERT INTO messages (hash, source_hash, destination_hash, content, timestamp) VALUES ('new','a','b','y',?)", (now,))
        c.execute("INSERT INTO sent_messages (hash, destination_hash, content, sent_at, status) VALUES ('sold','a','x',?,'sent')", (old,))
        c.execute("INSERT INTO sent_messages (hash, destination_hash, content, sent_at, status) VALUES ('snew','a','y',?,'sent')", (now,))
        conn.commit()
        conn.close()

        def touch(path, timestamp):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(b'x')
            os.utime(path, (timestamp, timestamp))

        touch(os.path.join(msg.raw_dir, 'old.raw'), old)
        touch(os.path.join(msg.raw_dir, 'new.raw'), now)
        touch(os.path.join(msg.telemetry_dir, 'old.json'), old)
        touch(os.path.join(msg.telemetry_dir, 'new.json'), now)
        touch(os.path.join(msg.conversations_dir, 'peer', 'old.lxb'), old)
        touch(os.path.join(msg.conversations_dir, 'peer', 'new.lxb'), now)
        result = msg.cleanup_old_data()
        self.assertEqual(result['messages'], 1)
        self.assertEqual(result['sent_messages'], 1)
        self.assertFalse(os.path.exists(os.path.join(msg.raw_dir, 'old.raw')))
        self.assertTrue(os.path.exists(os.path.join(msg.raw_dir, 'new.raw')))
        self.assertFalse(os.path.exists(os.path.join(msg.conversations_dir, 'peer', 'old.lxb')))
        msg.received_raw = {}
        for i in range(600):
            msg._remember_raw(str(i), 'h', 'a')
        self.assertEqual(len(msg.received_raw), 500)
        self.assertNotIn('99', msg.received_raw)
        self.assertIn('599', msg.received_raw)

    def test_announce_handler_aspect_and_callback(self):
        obj = M.Messenger.__new__(M.Messenger)
        obj.peers_db = os.path.join(self.tmp, 'announces.db')
        obj.db_lock = threading.Lock()
        M.Messenger._init_peers_db(obj)

        captured = []
        obj.announce_callback = captured.append

        announced = types.SimpleNamespace(hash=b'\x11' * 16)
        handler = M.AnnounceHandler(obj, 'lxmf.delivery')
        self.assertTrue(handler.receive_path_responses)
        handler.received_announce(b'\x22' * 16, announced, None, None)

        self.assertEqual(len(captured), 1)
        event = captured[0]
        self.assertEqual(event['aspect'], 'lxmf.delivery')
        self.assertEqual(event['identity_hash'], '11' * 16)
        self.assertEqual(event['destination_hash'], '22' * 16)
        self.assertIn('rssi', event)

        peers = obj.list_peers('lxmf.delivery')
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]['hash'], '11' * 16)
        self.assertEqual(peers[0]['aspect'], 'lxmf.delivery')
        self.assertEqual(obj.list_peers('other.aspect'), [])

    def test_display_name_resolution(self):
        obj = M.Messenger.__new__(M.Messenger)
        obj.storagepath = self.tmp
        obj.config = {}
        obj.identity = types.SimpleNamespace(hash=b'\xab' * 16)
        identity_path = os.path.join(self.tmp, 'TEST3')
        obj.identity_path = identity_path

        sidecar = identity_path + '.name'
        with open(sidecar, 'w') as f:
            f.write('Alice')
        self.assertEqual(obj._load_display_name(), 'Alice')

        os.unlink(sidecar)
        stale_hash_sidecar = os.path.join(self.tmp, obj.identity.hash.hex() + '.name')
        with open(stale_hash_sidecar, 'w') as f:
            f.write('abababab')
        self.assertEqual(obj._load_display_name(), 'TEST3')
        os.unlink(stale_hash_sidecar)

        self.assertEqual(obj._load_display_name(), 'TEST3')

        obj.identity_path = None
        self.assertEqual(obj._load_display_name(), 'abababab')

        self.assertTrue(M.is_hash_preview_name('abababab', obj.identity.hash.hex()))
        self.assertEqual(M.clean_display_name('\x03Bob\x00\x01\x07s', ''), 'Bob')
        self.assertEqual(M.clean_display_name('\x04Josh\x08\x00', ''), 'Josh')

    def test_display_name_migration_and_cleanup(self):
        obj = M.Messenger.__new__(M.Messenger)
        obj.peers_db = os.path.join(self.tmp, 'names.db')
        obj.db_lock = threading.Lock()
        M.Messenger._init_peers_db(obj)

        identity_hash = 'ab' * 16
        destination_hash = 'cd' * 16
        conn = sqlite3.connect(obj.peers_db)
        c = conn.cursor()
        c.execute(
            'INSERT INTO identities (identity_hash, display_name, first_seen, last_seen, group_name) VALUES (?,?,?,?,NULL)',
            (identity_hash, '\x03Bob\x00\x01\x07s', 1, 1),
        )
        c.execute(
            'INSERT INTO destinations (destination_hash, identity_hash, aspect, last_seen) VALUES (?,?,?,?)',
            (destination_hash, identity_hash, 'lxmf.delivery', 1),
        )
        c.execute("DELETE FROM app_meta WHERE key = 'display_names_v1'")
        conn.commit()
        conn.close()

        M.Messenger._init_peers_db(obj)
        conn = sqlite3.connect(obj.peers_db)
        c = conn.cursor()
        c.execute('SELECT display_name FROM identities WHERE identity_hash=?', (identity_hash,))
        self.assertEqual(c.fetchone()[0], 'Bob')
        c.execute("SELECT value FROM app_meta WHERE key = 'display_names_v1'")
        self.assertEqual(c.fetchone()[0], '1')
        conn.close()

    def test_sideband_telemetry_location_format(self):
        # Formato reale inviato da Sideband 19.98: accuracy a 4 byte.
        packed = bytes.fromhex(
            '8401ce6b02ebd00fa004c00297c40402b7e27cc40400bad22cc404000005dc'
            'c40400000000c40400000000c404020001ce6b02ebd0'
        )
        obj = telemeter.Telemeter.from_packed(packed)
        self.assertIsNotNone(obj)
        data = obj.read_all()
        self.assertIsNotNone(data.get('location'))
        self.assertAlmostEqual(data['location']['latitude'], 45.6055, places=3)
        self.assertAlmostEqual(data['location']['longitude'], 12.2435, places=3)
        self.assertAlmostEqual(data['location']['altitude'], 15.0, places=2)


        obj = M.Messenger.__new__(M.Messenger)
        obj.peers_db = os.path.join(self.tmp, 'announce_stats.db')
        obj.db_lock = threading.Lock()
        obj.announce_handlers = []
        obj.announce_stats = {'total': 0, 'by_aspect': {}, 'last': None, 'recent': []}
        obj.announce_stats_lock = threading.Lock()
        M.Messenger._init_peers_db(obj)

        obj._record_announce_event({
            'aspect': 'lxmf.delivery',
            'identity_hash': 'a' * 32,
            'destination_hash': 'b' * 32,
            'display_name': 'Test Peer',
            'hops': 1,
            'time': 123,
            'is_path_response': True,
        })
        stats = obj.get_announce_stats()
        self.assertEqual(stats['total'], 1)
        self.assertEqual(stats['by_aspect']['lxmf.delivery'], 1)
        self.assertEqual(stats['last']['display_name'], 'Test Peer')
        self.assertTrue(stats['last']['is_path_response'])
        self.assertEqual(stats['db_total'], 0)


        obj = M.Messenger.__new__(M.Messenger)
        dest = types.SimpleNamespace(hash=b'\x01' * 16)
        router = types.SimpleNamespace(
            delivery_destinations={dest.hash: dest},
            delivery_callbacks=[],
        )
        obj.dest = dest
        obj.router = router
        obj.running = True
        obj.announce_handlers = []
        obj.announce_handler = None

        with mock.patch.object(M.RNS.Transport, 'deregister_destination') as deregister:
            obj.cleanup()

        deregister.assert_called_once_with(dest)
        self.assertNotIn(dest.hash, router.delivery_destinations)
        self.assertIsNone(obj.dest)
        self.assertIsNone(obj.local_dest)
        self.assertIsNone(obj.router)

    @staticmethod
    def _discovery_payload(overrides=None):
        fields = {
            0x00: 'BackboneInterface',  # INTERFACE_TYPE
            0x01: True,                 # TRANSPORT
            0xFF: 'Node Alpha',          # NAME
            0x03: 45.4642,              # LATITUDE
            0x04: 9.1900,               # LONGITUDE
            0x05: 120.0,                # HEIGHT
            0x02: 'example.org',        # REACHABLE_ON
            0x06: 4242,                 # PORT
            0xFE: b'\x11' * 16,         # TRANSPORT_ID
            0xFD: 'RNS',                # TRANSPORT_IMPL
            0xFC: '1.5.0',              # TRANSPORT_VERS
            0xF0: b'\x22' * 16,         # operator LXMF address
            0x07: 'secret-net',         # IFAC netname (must not leak)
            0x08: 'secret-key',         # IFAC key (must not leak)
        }
        fields.update(overrides or {})
        packed = umsgpack.packb(fields)
        return b'\x00' + packed + b'\x00' * 32

    def test_interface_discovery_parser(self):
        app_data = self._discovery_payload()
        info = M.parse_interface_discovery_app_data(app_data)
        self.assertIsNotNone(info)
        self.assertEqual(info['type'], 'BackboneInterface')
        self.assertEqual(info['name'], 'Node Alpha')
        self.assertEqual(info['latitude'], 45.4642)
        self.assertEqual(info['longitude'], 9.19)
        self.assertEqual(info['height'], 120.0)
        self.assertEqual(info['reachable_on'], 'example.org')
        self.assertEqual(info['port'], 4242)
        self.assertEqual(info['operator_lxmf_address'], '22' * 16)
        self.assertNotIn('ifac_netname', info)
        self.assertNotIn('ifac_netkey', info)
        self.assertNotIn('config_entry', info)
        self.assertIsNone(M.parse_interface_discovery_app_data(b'\x00' + b'\x00' * 40))
        self.assertIsNone(M.parse_interface_discovery_app_data(
            self._discovery_payload({0x03: 120.0})
        ))
        with mock.patch.object(M.RNS.Transport, 'has_network_identity', return_value=False):
            self.assertIsNone(M.parse_interface_discovery_app_data(
                bytes([0x02]) + umsgpack.packb({0x00: 'x'}) + b'\x00' * 32
            ))

    def test_discovery_announce_handler(self):
        obj = M.Messenger.__new__(M.Messenger)
        obj.peers_db = os.path.join(self.tmp, 'discovery.db')
        obj.db_lock = threading.Lock()
        obj.announce_stats_lock = threading.Lock()
        obj.announce_stats = {'total': 0, 'by_aspect': {}, 'last': None, 'recent': []}
        M.Messenger._init_peers_db(obj)
        captured = []
        obj.announce_callback = captured.append

        announced = types.SimpleNamespace(hash=b'\x11' * 16)
        handler = M.AnnounceHandler(obj, M.DISCOVERY_ASPECT)
        handler.received_announce(b'\x22' * 16, announced, self._discovery_payload(), None)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['announce_info']['type'], 'BackboneInterface')
        peers = obj.list_peers(M.DISCOVERY_ASPECT)
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0]['name'], 'Node Alpha')

        conn = sqlite3.connect(obj.peers_db)
        row = conn.execute(
            'SELECT aspect, app_data FROM destinations WHERE destination_hash=?',
            ('22' * 16,),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], M.DISCOVERY_ASPECT)
        self.assertTrue(row[1])

    def test_set_announce_aspects_runtime(self):
        obj = M.Messenger.__new__(M.Messenger)
        obj.announce_aspects = ['lxmf.delivery']
        obj.announce_handlers = []
        obj.announce_aspects_lock = threading.Lock()
        reticulum = mock.Mock()
        with mock.patch.object(M.RNS.Transport, 'register_announce_handler') as register, \
                mock.patch.object(M.RNS.Transport, 'deregister_announce_handler') as deregister, \
                mock.patch.object(M.RNS.Reticulum, 'get_instance', return_value=reticulum):
            result = obj.set_announce_aspects([
                'lxmf.delivery', 'lxmf.propagation', M.DISCOVERY_ASPECT,
            ])
        self.assertEqual(result, ['lxmf.delivery', 'lxmf.propagation', M.DISCOVERY_ASPECT])
        self.assertEqual(register.call_count, 3)
        self.assertEqual(deregister.call_count, 0)
        self.assertEqual(reticulum.wants_destination_type.call_count, 3)

        old_handlers = list(obj.announce_handlers)
        with mock.patch.object(M.RNS.Transport, 'register_announce_handler'), \
                mock.patch.object(M.RNS.Transport, 'deregister_announce_handler') as deregister, \
                mock.patch.object(M.RNS.Reticulum, 'get_instance', return_value=reticulum):
            result = obj.set_announce_aspects(['lxmf.delivery'])
        self.assertEqual(result, ['lxmf.delivery'])
        self.assertEqual(deregister.call_count, 3)
        self.assertTrue(all(h not in obj.announce_handlers for h in old_handlers))

    def test_set_propagation_node(self):
        obj = M.Messenger.__new__(M.Messenger)
        link = types.SimpleNamespace(teardown=mock.Mock())
        router = types.SimpleNamespace(
            set_outbound_propagation_node=mock.Mock(),
            request_path=mock.Mock(),
            outbound_propagation_link=link,
            outbound_propagation_node=None,
        )
        obj.router = router
        obj.propagation_node = None
        obj.config = {}
        node = 'ab' * 16
        self.assertEqual(obj.set_propagation_node(node), node)
        self.assertEqual(obj.propagation_node, bytes.fromhex(node))
        self.assertEqual(obj.config['propagation_node'], node)
        router.set_outbound_propagation_node.assert_called_once_with(bytes.fromhex(node))
        router.request_path.assert_called_once_with(bytes.fromhex(node))

        self.assertIsNone(obj.set_propagation_node(None))
        self.assertIsNone(obj.propagation_node)
        self.assertIsNone(obj.config['propagation_node'])
        link.teardown.assert_called_once()
        self.assertIsNone(router.outbound_propagation_link)
        self.assertIsNone(router.outbound_propagation_node)

        with self.assertRaises(ValueError):
            obj.set_propagation_node('a' * 64)

    def test_remote_rangetest_command_dispatch(self):
        obj = M.Messenger.__new__(M.Messenger)
        obj.config = {'allow_remote_commands': True}
        obj._peer_permissions = mock.Mock(return_value={
            'allow_commands': True,
            'allow_telemetry': False,
            'trust_level': 3,
        })
        obj._start_rangetest = mock.Mock()
        obj._stop_rangetest = mock.Mock()
        obj.send = mock.Mock()
        message = types.SimpleNamespace(
            source_hash=b'\x11' * 16,
            hash=b'\x22' * 32,
        )

        obj._handle_commands(message, [{M.Commands.CUSTOM: '15 start'}])
        obj._start_rangetest.assert_called_once_with('11' * 16, 15)
        obj.send.assert_called_once()

        obj._start_rangetest.reset_mock()
        obj._handle_commands(message, [{M.Commands.CUSTOM: 'rangetest 20 start'}])
        obj._start_rangetest.assert_called_once_with('11' * 16, 20)

        obj._handle_commands(message, [{M.Commands.CUSTOM: '0 stop'}])
        obj._stop_rangetest.assert_called_once_with('11' * 16)

    def test_telemetry_roundtrip_and_audio(self):
        packed = umsgpack.packb({
            0x01: int(time.time()),
            0x03: 1013.2,
            0x04: [85.0, True, 21.0],
            0x07: 21.5,
            0x08: 50.5,
            0x0A: 123.4,
            0x13: 42.0,
            0x14: 61.0,
            0x15: 72.0,
        })
        data = telemeter.Telemeter.from_packed(packed).read_all()
        self.assertEqual(data['temperature'], 21.5)
        self.assertEqual(data['humidity'], 50.5)
        self.assertEqual(data['pressure'], 1013.2)
        self.assertEqual(data['ambient_light'], 123.4)
        self.assertEqual(data['processor'], 42.0)
        self.assertEqual(data['ram'], 61.0)
        self.assertEqual(data['nvm'], 72.0)
        self.assertEqual(data['battery']['charge_percent'], 85.0)

        msg = M.Messenger.__new__(M.Messenger)
        msg.config = {'preferred_audio': 'c2_3200'}
        mode, name = msg._audio_preference()
        self.assertEqual(name, 'c2_3200')
        self.assertEqual(mode, M.AM_CODEC2_3200)
        msg.config = {'preferred_audio': 'opus'}
        self.assertIsNone(msg._audio_preference()[0])
        if M.CODEC2_AVAILABLE:
            path = tempfile.mktemp(suffix='.wav', dir=self.tmp)
            rate = 8000
            t = np.arange(int(rate * 0.25), dtype=np.float64) / rate
            samples = (np.sin(2 * math.pi * 440 * t) * 12000).astype(np.int16)
            with wave.open(path, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes(samples.tobytes())
            encoded = msg._encode_wav_to_codec2(path, M.AM_CODEC2_3200)
            self.assertTrue(encoded)


if __name__ == '__main__':
    unittest.main()
