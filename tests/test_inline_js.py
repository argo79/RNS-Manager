import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = [
    ROOT / 'manager/templates/monitor.html',
    ROOT / 'manager/templates/index.html',
    ROOT / 'messenger/templates/lxmf_chat.html',
]


@unittest.skipUnless(shutil.which('node'), 'node non disponibile')
class InlineJavascriptTests(unittest.TestCase):
    def test_inline_scripts_are_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            for template in TEMPLATES:
                source = template.read_text(encoding='utf-8')
                scripts = re.findall(r'<script(?![^>]*src=)[^>]*>(.*?)</script>', source, re.S)
                code = '\n;\n'.join(scripts).replace('{{ version }}', '1.0.0').replace('{{version}}', '1.0.0')
                target = Path(tmp) / (template.stem + '.js')
                target.write_text(code, encoding='utf-8')
                result = subprocess.run(['node', '--check', str(target)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f'{template}: {result.stderr}')

    def test_no_unpinned_opus_cdn(self):
        source = (ROOT / 'messenger/templates/lxmf_chat.html').read_text(encoding='utf-8')
        self.assertNotIn('opus-media-recorder@latest', source)
        self.assertNotIn('opus-media-recorder', source)

    def test_stats_identity_hash_is_full_and_clickable(self):
        source = (ROOT / 'manager/templates/monitor.html').read_text(encoding='utf-8')
        self.assertIn('class="stats-identity-hash"', source)
        self.assertIn("showIdentityInTab('${escapeJsString(fullHash)}')", source)
        self.assertNotIn('id.hash.slice(0,16)', source)
        self.assertNotIn('identity_hash).slice(0,16)', source)

    def test_messenger_assets_are_local_and_map_is_keyless(self):
        template = (ROOT / 'messenger/templates/lxmf_chat.html').read_text(encoding='utf-8')
        backend = (ROOT / 'messenger/lxmf_chat.py').read_text(encoding='utf-8')
        self.assertNotIn('cdn.socket.io', template)
        self.assertNotIn('unpkg.com/leaflet', template)
        self.assertIn('/static/vendor/socket.io/socket.io.min.js', template)
        self.assertIn('/static/vendor/leaflet/leaflet.js', template)
        self.assertNotIn('maps.google', template.lower())
        self.assertIn('World_Street_Map', template)
        self.assertNotIn('basemaps.cartocdn.com', template)
        self.assertIn('tile.openstreetmap.org', template)
        self.assertIn('opentopomap.org', template)
        self.assertIn('cyclosm.openstreetmap.fr', template)
        self.assertNotIn('cdn.socket.io', backend)
        self.assertNotIn('unpkg.com', backend)

    def test_messenger_map_fallbacks(self):
        source = (ROOT / 'messenger/templates/lxmf_chat.html').read_text(encoding='utf-8')
        self.assertIn('id="mapStatus"', source)
        self.assertIn('invalidateMapSize', source)
        self.assertIn('retryMap', source)
        self.assertIn('World_Street_Map', source)
        self.assertIn('World_Topo_Map', source)
        self.assertIn('World_Imagery', source)
        self.assertIn('mapLayerSelect', source)
        self.assertIn('tile.openstreetmap.org', source)
        self.assertIn('opentopomap.org', source)
        self.assertIn('cyclosm.openstreetmap.fr', source)
        self.assertIn('fitMapToMarkers', source)
        self.assertNotIn('basemaps.cartocdn.com', source)

    def test_messenger_announce_list_refresh(self):
        source = (ROOT / 'messenger/templates/lxmf_chat.html').read_text(encoding='utf-8')
        self.assertIn('applyAnnounceToPeerList', source)
        self.assertIn('startPeerRefreshWatchdog', source)
        self.assertIn('schedulePeerDisplay', source)
        self.assertIn('if (peerRefreshTimer) return;', source)
        self.assertNotIn('if (peerRefreshTimer) clearTimeout(peerRefreshTimer);', source)
        self.assertIn('/api/announce/stats', source)
        self.assertIn('announceStatsDisplay', source)
        self.assertIn('/api/telemetry/stats', source)
        self.assertIn('allowTelemetryRequests', source)
        self.assertIn('telemetryDiagnostics', source)
        self.assertIn('togglePeerCard', source)
        self.assertIn('peer-compact-meta', source)
        self.assertIn('peer-card-head', source)
        self.assertIn('renderAspectMapMarkers', source)
        self.assertIn('aspectMarkersByHash', source)
        self.assertIn('announce_info', source)
        self.assertIn('/api/map/peers', source)
        self.assertIn('iface-map-name', source)
        self.assertIn('showContactMarkers', source)
        self.assertIn('peerAspectSelect', source)
        self.assertIn('announceAspects', source)
        self.assertIn('map-mode', source)
        self.assertIn('mapModeBtn', source)
        self.assertIn('mapLayerSelect', source)
        self.assertIn('fitMapToMarkers', source)
        self.assertIn('centerOnMe', source)
        self.assertIn('measureTool', source)
        self.assertIn('measureLayer', source)
        self.assertIn('mapMeasureTool', source)
        self.assertIn('clearMeasurements', source)
        self.assertIn('measureHoverLine', source)
        self.assertIn('clearMeasureHover', source)
        self.assertIn('updateMeasureHover', source)
        self.assertIn('measureDistanceTotal', source)
        self.assertIn('measureCircleMetrics', source)
        self.assertIn('measureRectangleMetrics', source)
        self.assertIn('selectedInterfaceTypes', source)
        self.assertIn('interfaceFilterPanel', source)
        self.assertIn('interface-filter-chip', source)
        self.assertIn('iface-map-marker', source)
        self.assertIn('setPropagationNode', source)
        self.assertIn('setPropagationFromPeer', source)
        self.assertIn('propagationStatus', source)
        self.assertIn('peerGroupControls', source)
        self.assertIn('messageComposer', source)
        self.assertNotIn('peer-readonly-note', source)
        self.assertNotIn('Aspect informativo:', source)
        self.assertIn('toggleRawEntry', source)
        self.assertIn('raw-entry-details', source)

    def test_messenger_audio_metadata(self):
        source = (ROOT / 'messenger/templates/lxmf_chat.html').read_text(encoding='utf-8')
        self.assertIn('data.audio_mode', source)
        self.assertIn('audio_size', source)
        self.assertNotIn("attachments[0].mode = 16", source)


if __name__ == '__main__':
    unittest.main()
