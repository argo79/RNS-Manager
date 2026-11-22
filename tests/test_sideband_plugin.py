import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / 'sideband_plugins' / 'rangetest.py'


class DummyCommandPlugin:
    def __init__(self, sideband_core):
        self.sideband_core = sideband_core
        self.command_name = type(self).command_name
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def is_running(self):
        return self.running

    def get_sideband(self):
        return self.sideband_core


class DummySideband:
    def __init__(self, db_path, telemetry_points=None):
        self.db_path = db_path
        self.replies = []
        self.requests = []
        self.telemetry_points = telemetry_points or []

    def send_message(self, content, destination_hash, propagation, **kwargs):
        self.replies.append((content, destination_hash, propagation, kwargs))

    def request_latest_telemetry(self, from_addr=None, is_livetrack=False):
        self.requests.append((from_addr, is_livetrack))
        return 'sent'

    def peer_telemetry(self, context_dest=None, after=None, before=None, limit=None):
        return list(self.telemetry_points)


class SidebandRangetestPluginTests(unittest.TestCase):
    def test_plugin_syntax_and_command_lifecycle(self):
        source = PLUGIN_PATH.read_text(encoding='utf-8')
        namespace = {
            'SidebandCommandPlugin': DummyCommandPlugin,
            '__name__': 'rangetest_plugin_test',
        }
        exec(compile(source, str(PLUGIN_PATH), 'exec'), namespace)
        plugin_cls = namespace['plugin_class']

        with tempfile.TemporaryDirectory() as tmp:
            sideband = DummySideband(os.path.join(tmp, 'app_storage', 'sideband.db'))
            os.makedirs(os.path.dirname(sideband.db_path), exist_ok=True)
            plugin = plugin_cls(sideband)
            message = types.SimpleNamespace(source_hash=b'\x11' * 16)

            try:
                plugin.start()
                self.assertTrue(plugin.is_running())
                plugin.handle_command(['15', 'start'], message)
                self.assertIn(message.source_hash, plugin._tests)
                self.assertEqual(plugin._tests[message.source_hash]['interval'], 15)
                self.assertEqual(len(sideband.replies), 1)

                plugin.handle_command(['0', 'stop'], message)
                self.assertNotIn(message.source_hash, plugin._tests)
                self.assertEqual(len(sideband.replies), 2)
            finally:
                plugin.stop()

    def test_plugin_clamps_interval_and_rejects_invalid_arguments(self):
        source = PLUGIN_PATH.read_text(encoding='utf-8')
        namespace = {
            'SidebandCommandPlugin': DummyCommandPlugin,
            '__name__': 'rangetest_plugin_test',
        }
        exec(compile(source, str(PLUGIN_PATH), 'exec'), namespace)
        plugin_cls = namespace['plugin_class']

        with tempfile.TemporaryDirectory() as tmp:
            sideband = DummySideband(os.path.join(tmp, 'app_storage', 'sideband.db'))
            os.makedirs(os.path.dirname(sideband.db_path), exist_ok=True)
            plugin = plugin_cls(sideband)
            message = types.SimpleNamespace(source_hash=b'\x22' * 16)
            try:
                plugin.start()
                plugin.handle_command(['2', 'start'], message)
                self.assertEqual(plugin._tests[message.source_hash]['interval'], 5)
                plugin.handle_command(['bad'], message)
                self.assertEqual(len(sideband.replies), 2)
            finally:
                plugin.stop()

    def test_plugin_exports_gpx_and_kml(self):
        source = PLUGIN_PATH.read_text(encoding='utf-8')
        namespace = {
            'SidebandCommandPlugin': DummyCommandPlugin,
            '__name__': 'rangetest_plugin_test',
        }
        exec(compile(source, str(PLUGIN_PATH), 'exec'), namespace)
        plugin_cls = namespace['plugin_class']

        class FakeTelemeter:
            @staticmethod
            def from_packed(packed):
                return FakeTelemeter()

            def read_all(self):
                return {
                    'location': {
                        'latitude': 45.0,
                        'longitude': 12.0,
                        'altitude': 100.0,
                        'speed': 1.5,
                        'bearing': 90.0,
                    },
                    'battery': {'charge_percent': 80},
                    'temperature': 20.0,
                }

        fake_pkg = types.ModuleType('sbapp')
        fake_sideband = types.ModuleType('sbapp.sideband')
        fake_sense = types.ModuleType('sbapp.sideband.sense')
        fake_sense.Telemeter = FakeTelemeter
        fake_sideband.sense = fake_sense
        fake_pkg.sideband = fake_sideband

        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, 'app_storage', 'sideband.db')
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            sideband = DummySideband(db_path, telemetry_points=[(1234567890, b'packed')])
            plugin = plugin_cls(sideband)
            message = types.SimpleNamespace(source_hash=b'\x33' * 16)
            modules = {
                'sbapp': fake_pkg,
                'sbapp.sideband': fake_sideband,
                'sbapp.sideband.sense': fake_sense,
            }
            try:
                with mock.patch.dict(sys.modules, modules):
                    plugin.start()
                    plugin.handle_command(['5', 'start'], message)
                    plugin.handle_command(['0', 'stop'], message)
            finally:
                plugin.stop()

            output_dir = os.path.join(tmp, 'app_storage', 'rangetest')
            files = sorted(os.listdir(output_dir))
            self.assertTrue(any(name.endswith('.gpx') for name in files))
            self.assertTrue(any(name.endswith('.kml') for name in files))


if __name__ == '__main__':
    unittest.main()
