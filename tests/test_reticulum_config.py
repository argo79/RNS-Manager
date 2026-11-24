import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'messenger'))

from RNS.vendor.configobj import ConfigObj

from core.reticulum_config import (
    ReticulumConfigError,
    add_interface,
    add_rnode_interface,
    disable_interface,
    list_interfaces,
    set_interface_enabled,
)


BASE_CONFIG = """# Test Reticulum config
[reticulum]
  enable_transport = no

[interfaces]
  [[RNode Existing]]
    type = RNodeInterface
    interface_enabled = yes
    port = /dev/ttyUSB0
    frequency = 869000000
    bandwidth = 125000
    txpower = 17
    spreadingfactor = 8
    codingrate = 5
    discovery_name = Existing Node
    passphrase = secret-passphrase

  [[TCP Existing]]
    type = TCPClientInterface
    interface_enabled = no
    target_host = example.org
    target_port = 4242
"""


class ReticulumConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rns_config_')
        self.path = os.path.join(self.tmp.name, 'config')
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(BASE_CONFIG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_interfaces_hides_secrets(self):
        result = list_interfaces(self.path)
        self.assertEqual(result['config_path'], self.path)
        self.assertEqual(len(result['interfaces']), 2)
        existing = result['interfaces'][0]
        self.assertEqual(existing['name'], 'RNode Existing')
        self.assertTrue(existing['enabled'])
        self.assertEqual(existing['frequency'], '869000000')
        self.assertNotIn('passphrase', existing)

    def test_add_rnode_and_detect_duplicate(self):
        payload = {
            'type': 'RNodeInterface',
            'name': 'RNode Node Alpha',
            'port': '/dev/ttyACM0',
            'frequency': 868000000,
            'bandwidth': 250000,
            'spreadingfactor': 9,
            'codingrate': 6,
            'txpower': 14,
            'discovery_name': 'Node Alpha',
        }
        result = add_rnode_interface(payload, path=self.path)
        self.assertTrue(result['created'])
        self.assertTrue(result['requires_restart'])
        self.assertEqual(result['interface']['type'], 'RNodeInterface')
        self.assertTrue(os.path.isfile(self.path + '.rns_manager.bak'))

        config = ConfigObj(self.path, encoding='utf-8')
        section = config['interfaces']['RNode Node Alpha']
        self.assertEqual(section['frequency'], '868000000')
        self.assertEqual(section['bandwidth'], '250000')
        self.assertEqual(section['spreadingfactor'], '9')
        self.assertEqual(section['codingrate'], '6')
        self.assertEqual(section['discovery_name'], 'Node Alpha')
        self.assertIn('# Test Reticulum config', Path(self.path).read_text())

        duplicate = add_rnode_interface(payload, path=self.path)
        self.assertFalse(duplicate['created'])
        self.assertEqual(duplicate['interface']['name'], 'RNode Node Alpha')

    def test_disable_interface_and_expected_type(self):
        result = disable_interface(
            'RNode Existing',
            path=self.path,
            expected_type='RNodeInterface',
        )
        self.assertTrue(result['disabled'])
        self.assertFalse(result['interface']['enabled'])

        config = ConfigObj(self.path, encoding='utf-8')
        self.assertEqual(
            str(config['interfaces']['RNode Existing']['interface_enabled']).lower(),
            'no',
        )
        self.assertEqual(
            str(config['interfaces']['TCP Existing']['interface_enabled']).lower(),
            'no',
        )

        with self.assertRaises(ReticulumConfigError):
            disable_interface(
                'RNode Existing',
                path=self.path,
                expected_type='TCPClientInterface',
            )

    def test_set_interface_enabled_toggle(self):
        result = set_interface_enabled('TCP Existing', True, path=self.path)
        self.assertTrue(result['enabled'])
        self.assertFalse(result['disabled'])
        self.assertTrue(result['interface']['enabled'])
        config = ConfigObj(self.path, encoding='utf-8')
        self.assertEqual(
            str(config['interfaces']['TCP Existing']['interface_enabled']).lower(),
            'yes',
        )

        result = set_interface_enabled(
            'TCP Existing',
            False,
            path=self.path,
            expected_type='TCPClientInterface',
        )
        self.assertFalse(result['enabled'])
        self.assertTrue(result['disabled'])
        config = ConfigObj(self.path, encoding='utf-8')
        self.assertEqual(
            str(config['interfaces']['TCP Existing']['interface_enabled']).lower(),
            'no',
        )

        with self.assertRaises(ReticulumConfigError):
            set_interface_enabled(
                'TCP Existing',
                True,
                path=self.path,
                expected_type='RNodeInterface',
            )

    def test_add_all_supported_interface_types(self):
        cases = [
            {'type': 'BackboneClientInterface', 'name': 'Backbone Client Test', 'target_host': 'backbone.example', 'target_port': 4343, 'discovery_name': 'Backbone Node'},
            {'type': 'TCPClientInterface', 'name': 'TCP Client Test', 'target_host': 'example.net', 'target_port': 4242, 'discovery_name': 'TCP Client Node'},
            {'type': 'KISSInterface', 'name': 'KISS Test', 'port': '/dev/ttyACM1', 'speed': 9600, 'discovery_name': 'KISS Node'},
            {'type': 'WeaveInterface', 'name': 'Weave Test', 'port': '/dev/ttyACM2', 'speed': 3000000, 'discovery_name': 'Weave Node'},
            {'type': 'I2PInterface', 'name': 'I2P Test', 'peers': 'abc.b32.i2p', 'connectable': 'no', 'discovery_name': 'I2P Node'},
        ]
        for payload in cases:
            result = add_interface(payload, path=self.path)
            self.assertTrue(result['created'], payload['type'])
            section = ConfigObj(self.path, encoding='utf-8')['interfaces'][payload['name']]
            self.assertEqual(section['type'], payload['type'])
            self.assertEqual(str(section['interface_enabled']).lower(), 'yes')

        entries = {item['name']: item for item in list_interfaces(self.path)['interfaces']}
        self.assertIn('Backbone Client Test', entries)
        self.assertIn('TCP Client Test', entries)
        self.assertIn('KISS Test', entries)
        self.assertIn('Weave Test', entries)
        self.assertIn('I2P Test', entries)

    def test_rejects_server_interface_types_for_discovery(self):
        with self.assertRaises(ReticulumConfigError):
            add_interface({
                'type': 'BackboneInterface',
                'name': 'Wrong Server',
                'listen_port': 4242,
            }, path=self.path)
        with self.assertRaises(ReticulumConfigError):
            add_interface({
                'type': 'TCPServerInterface',
                'name': 'Wrong TCP Server',
                'listen_port': 4242,
            }, path=self.path)

    def test_add_requires_rnode_values(self):
        with self.assertRaises(ReticulumConfigError):
            add_rnode_interface({
                'type': 'RNodeInterface',
                'name': 'Missing Port',
                'frequency': 869000000,
            }, path=self.path)
        with self.assertRaises(ReticulumConfigError):
            add_rnode_interface({
                'type': 'TCPClientInterface',
                'name': 'Not RNode',
            }, path=self.path)


if __name__ == '__main__':
    unittest.main()
