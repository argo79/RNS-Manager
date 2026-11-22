import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'manager'))

import modules.rns_monitor as monitor  # noqa: E402


class MonitorProcessTests(unittest.TestCase):
    def test_monitor_process_is_not_daemonic(self):
        """RNS avvia processi figli per gli stamp: il monitor non deve essere daemon."""
        captured = {}

        class FakeProcess:
            def __init__(self, target=None, args=(), daemon=None, **kwargs):
                captured['daemon'] = daemon
                captured['target'] = target
                captured['args'] = args
                self.pid = 123

            def start(self):
                captured['started'] = True

            def is_alive(self):
                return False

            def terminate(self):
                pass

            def join(self, timeout=None):
                pass

        manager = monitor.RNSMonitorManager.__new__(monitor.RNSMonitorManager)
        manager.is_windows = False
        manager.socket_path = tempfile.mktemp(suffix='.sock')
        manager.aspects = []
        manager.host = None
        manager.port = None

        original_process = monitor.multiprocessing.Process
        original_sleep = monitor.time.sleep
        monitor.multiprocessing.Process = FakeProcess
        monitor.time.sleep = lambda *_: None
        try:
            manager.start_monitor_process()
        finally:
            monitor.multiprocessing.Process = original_process
            monitor.time.sleep = original_sleep

        self.assertIs(captured.get('daemon'), False)
        self.assertTrue(captured.get('started'))


if __name__ == '__main__':
    unittest.main()
