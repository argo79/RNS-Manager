#!/usr/bin/env python3
# telemetry_provider.py - Fornisce dati di telemetria dal sistema

import os
import time
import json
import platform
import subprocess
import threading
from datetime import datetime

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("⚠️ psutil non installato. Installare con: pip install psutil")

try:
    import gps
    GPSD_AVAILABLE = True
except ImportError:
    GPSD_AVAILABLE = False
    print("⚠️ gps non installato. Installare con: pip install gps")

import RNS
import struct
from RNS.vendor import umsgpack

# ==================== SENSORI DI SISTEMA ====================

class SystemInfo:
    """Raccoglie informazioni di sistema"""
    
    @staticmethod
    def get_cpu():
        """Ottieni uso CPU"""
        if not PSUTIL_AVAILABLE:
            return None
        try:
            return {
                'percent': psutil.cpu_percent(interval=0.5),
                'count': psutil.cpu_count(),
                'frequency': psutil.cpu_freq().current if psutil.cpu_freq() else None
            }
        except:
            return None
    
    @staticmethod
    def get_ram():
        """Ottieni uso RAM"""
        if not PSUTIL_AVAILABLE:
            return None
        try:
            mem = psutil.virtual_memory()
            return {
                'total': mem.total,
                'available': mem.available,
                'percent': mem.percent,
                'used': mem.used,
                'free': mem.free
            }
        except:
            return None
    
    @staticmethod
    def get_disk():
        """Ottieni uso disco"""
        if not PSUTIL_AVAILABLE:
            return None
        try:
            disk = psutil.disk_usage('/')
            return {
                'total': disk.total,
                'used': disk.used,
                'free': disk.free,
                'percent': disk.percent
            }
        except:
            return None
    
    @staticmethod
    def get_battery():
        """Ottieni stato batteria (se presente)"""
        if not PSUTIL_AVAILABLE:
            return None
        try:
            battery = psutil.sensors_battery()
            if battery:
                return {
                    'percent': battery.percent,
                    'charging': battery.power_plugged,
                    'time_left': battery.secsleft if battery.secsleft != -1 else None
                }
        except:
            pass
        return None
    
    @staticmethod
    def get_uptime():
        """Ottieni uptime del sistema"""
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                return uptime_seconds
        except:
            # Fallback per Windows/Mac
            import time
            return time.time() - psutil.boot_time() if PSUTIL_AVAILABLE else None
    
    @staticmethod
    def get_hostname():
        """Ottieni hostname"""
        return platform.node()
    
    @staticmethod
    def get_os_info():
        """Ottieni info OS"""
        return {
            'system': platform.system(),
            'release': platform.release(),
            'version': platform.version(),
            'machine': platform.machine()
        }

# ==================== POSIZIONE ====================

# ==================== POSIZIONE DA GPSD ====================

class LocationProvider:
    """Fornisce posizione da GPSD o config"""
    
    def __init__(self, config):
        self.config = config
        self.gps_client = None
        self.last_location = None
        self.last_update = 0
        self.lock = threading.Lock()
        self.running = True
        self.gps_thread = None
        
        # Avvia GPSD se configurato
        if config.get('location_source') == 'gpsd':
            self._init_gpsd()
    
    def _init_gpsd(self):
        """Inizializza connessione a GPSD"""
        try:
            from gpsdclient import GPSDClient
            host = self.config.get('gpsd_host', 'localhost')
            port = self.config.get('gpsd_port', 2947)
            
            self.gps_client = GPSDClient(host=host, port=port)
            print(f"✅ GPSD connesso a {host}:{port}")
            
            # Thread per lettura continua
            self.gps_thread = threading.Thread(target=self._gps_reader, daemon=True)
            self.gps_thread.start()
            
        except Exception as e:
            print(f"❌ Errore connessione GPSD: {e}")
            self.gps_client = None
    
    def _gps_reader(self):
        """Legge dati GPS in continuo"""
        while self.running and self.gps_client:
            try:
                report = next(self.gps_client.dict_stream())
                
                if report.get('class') == 'TPV':
                    with self.lock:
                        self.last_location = {
                            'latitude': report.get('lat'),
                            'longitude': report.get('lon'),
                            'altitude': report.get('altMSL', report.get('alt', 0)),
                            'speed': report.get('speed', 0) * 3.6,  # m/s -> km/h
                            'bearing': report.get('track', 0),
                            'accuracy': report.get('epx', report.get('eph', 10)),
                            'timestamp': time.time(),
                            'source': 'gpsd'
                        }
                        self.last_update = time.time()
                        
            except Exception as e:
                time.sleep(1)
    
    def get_location(self):
        """Ottieni posizione corrente"""
        with self.lock:
            # Se GPSD attivo e ha dati recenti (<10s)
            if self.gps_client and self.last_location and (time.time() - self.last_update) < 10:
                return self.last_location
            
            # Altrimenti posizione fissa
            fixed = self.config.get('fixed_location', {})
            if fixed and fixed.get('latitude') and fixed.get('longitude'):
                return {
                    'latitude': fixed.get('latitude'),
                    'longitude': fixed.get('longitude'),
                    'altitude': fixed.get('altitude', 0),
                    'speed': 0,
                    'bearing': 0,
                    'accuracy': 10,
                    'timestamp': time.time(),
                    'source': 'fixed'
                }
        
        return None
    
    def stop(self):
        """Ferma il thread GPS"""
        self.running = False
        if self.gps_thread:
            self.gps_thread.join(timeout=2)

# ==================== TELEMETRY PROVIDER ====================

class TelemetryProvider:
    """Fornisce tutti i dati di telemetria usando il config.json principale"""
    
    def __init__(self, main_config):
        """
        main_config: il dizionario config del messenger (messenger.config)
        """
        self.main_config = main_config  # Riferimento al config principale!
        self.location = LocationProvider(main_config)
        
        print("✅ TelemetryProvider inizializzato (usa config.json principale)")
        if main_config.get('location_source') == 'gpsd':
            print(f"📡 Fonte posizione: GPSD ({main_config.get('gpsd_host')}:{main_config.get('gpsd_port')})")
        else:
            loc = main_config.get('fixed_location', {})
            print(f"📍 Fonte posizione: Fissa ({loc.get('latitude')}, {loc.get('longitude')})")
    
    def get_telemetry_data(self):
        """Raccoglie tutti i dati di telemetria REALI"""
        data = {}
        
        # TIME - sempre presente
        data['time'] = {'utc': int(time.time())}
        
        # LOCATION - SOLO se abilitato e disponibile
        if self.main_config.get('enable_location', True):
            loc = self.location.get_location()
            if loc:
                # Verifica che la posizione abbia dati validi
                if loc.get('latitude') is not None and loc.get('longitude') is not None:
                    data['location'] = loc
        
        # SYSTEM INFO - SOLO se abilitato e psutil disponibile
        if self.main_config.get('enable_system', True) and PSUTIL_AVAILABLE:
            cpu = SystemInfo.get_cpu()
            if cpu and cpu.get('percent') is not None:
                data['processor'] = cpu
            
            ram = SystemInfo.get_ram()
            if ram and ram.get('percent') is not None:
                data['ram'] = ram
            
            disk = SystemInfo.get_disk()
            if disk and disk.get('percent') is not None:
                data['nvm'] = disk
            
            battery = SystemInfo.get_battery()
            if battery:
                data['battery'] = battery
            
            uptime = SystemInfo.get_uptime()
            if uptime is not None:
                data['information'] = {'uptime': uptime}
        
        return data
    
    def get_telemetry_packed(self):
        """Restituisce telemetria impacchettata per LXMF"""
        data = self.get_telemetry_data()
        
        packed = {}
        
        # Time
        if 'time' in data:
            packed[0x01] = data['time']['utc']
        
        # Location
        if 'location' in data and data['location']:
            loc = data['location']
            try:
                lat = loc.get('latitude')
                lon = loc.get('longitude')
                
                if lat is not None and lon is not None:
                    packed[0x02] = [
                        struct.pack("!i", int(round(float(lat), 6) * 1e6)),
                        struct.pack("!i", int(round(float(lon), 6) * 1e6)),
                        struct.pack("!i", int(round(float(loc.get('altitude', 0)), 2) * 1e2)),
                        struct.pack("!I", int(round(float(loc.get('speed', 0)), 2) * 1e2)),
                        struct.pack("!i", int(round(float(loc.get('bearing', 0)), 2) * 1e2)),
                        struct.pack("!H", int(round(float(loc.get('accuracy', 10)), 2) * 1e2)),
                        int(loc.get('timestamp', time.time()))
                    ]
            except Exception as e:
                print(f"❌ Errore packing location: {e}")
        
        # Battery
        if 'battery' in data:
            bat = data['battery']
            packed[0x04] = [
                float(bat.get('percent', 0)),
                bool(bat.get('charging', False)),
                None
            ]
        
        # Processor
        if 'processor' in data:
            cpu = data['processor']
            if isinstance(cpu, dict) and cpu.get('percent'):
                packed[0x13] = float(cpu['percent'])
        
        # RAM
        if 'ram' in data:
            ram = data['ram']
            if isinstance(ram, dict) and ram.get('percent'):
                packed[0x14] = float(ram['percent'])
        
        # NVM
        if 'nvm' in data:
            disk = data['nvm']
            if isinstance(disk, dict) and disk.get('percent'):
                packed[0x15] = float(disk['percent'])
        
        # Uptime
        if 'information' in data:
            info = data['information']
            if info.get('uptime'):
                packed[0x0F] = f"Uptime: {int(info['uptime'])}s"
        
        return umsgpack.packb(packed) if packed else None

# ==================== INTEGRAZIONE CON MESSENGER ====================

def setup_telemetry_handlers(messenger):
    """Configura il messenger per rispondere alle richieste di telemetria"""
    
    def on_telemetry_request(message, command_data):
        """Callback quando arriva TELEMETRY_REQUEST"""
        source = message.source_hash.hex()
        
        print(f"📡 Richiesta telemetria da {source[:16]}...")
        
        # Prepara risposta
        fields = {
            FIELD_TELEMETRY: messenger.telemetry_provider.get_telemetry_packed()
        }
        
        # Aggiungi icona se configurata
        appearance = messenger.config.get('appearance', ['antenna', '4c9aff', '1a1f2e'])
        if appearance and len(appearance) == 3:
            fg_bytes = bytes.fromhex(appearance[1].lstrip('#')) if isinstance(appearance[1], str) else appearance[1]
            bg_bytes = bytes.fromhex(appearance[2].lstrip('#')) if isinstance(appearance[2], str) else appearance[2]
            fields[FIELD_ICON_APPEARANCE] = [appearance[0], fg_bytes, bg_bytes]
        
        # Invia risposta
        messenger.send(
            source,
            "📊 Telemetria",
            title="Telemetry Reply",
            fields=fields,
            callbacks=None
        )
        
        print(f"✅ Risposta telemetria inviata a {source[:16]}")
    
    # Salva riferimento per uso futuro
    messenger.telemetry_callback = on_telemetry_request
    
    print("✅ Gestore telemetria configurato")