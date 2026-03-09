#!/usr/bin/env python3
# messenger.py - Backend per LXMF Web App

import os
import RNS
import LXMF
import time
import json
import traceback
import threading
import struct
import sqlite3
import uuid
import re
import wave
import numpy as np
import tempfile
import subprocess
from RNS.vendor import umsgpack
from gpsdclient import GPSDClient
import core.telemeter as telemeter
import audio_codec
from telemetry_provider import TelemetryProvider, setup_telemetry_handlers

# Costanti LXMF (dalla specifica ufficiale)
FIELD_TELEMETRY = 0x02
FIELD_TELEMETRY_STREAM = 0x03
FIELD_ICON_APPEARANCE = 0x04
FIELD_FILE_ATTACHMENTS = 0x05
FIELD_IMAGE = 0x06
FIELD_AUDIO = 0x07
FIELD_COMMANDS = 0x09

# Modalità audio LXMF
AM_CODEC2_450PWB = 0x01
AM_CODEC2_450 = 0x02
AM_CODEC2_700C = 0x03
AM_CODEC2_1200 = 0x04
AM_CODEC2_1300 = 0x05
AM_CODEC2_1400 = 0x06
AM_CODEC2_1600 = 0x07
AM_CODEC2_2400 = 0x08
AM_CODEC2_3200 = 0x09
AM_OPUS_OGG = 0x10

class Commands:
    CUSTOM = 0x00
    TELEMETRY_REQUEST = 0x01
    PING = 0x02
    ECHO = 0x03
    SIGNAL_REPORT = 0x04
    RANGETEST = 0x05

CODEC2_AVAILABLE = audio_codec.detect_codec2()
_RETICULUM_INSTANCE = None

def get_reticulum():
    global _RETICULUM_INSTANCE
    if _RETICULUM_INSTANCE is None:
        _RETICULUM_INSTANCE = RNS.Reticulum()
    return _RETICULUM_INSTANCE

def guess_image_format(data):
    if data.startswith(b'\xff\xd8'):
        return "JPEG"
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):
        return "PNG"
    elif data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
        return "GIF"
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return "WebP"
    elif data.startswith(b'BM'):
        return "BMP"
    else:
        return "Unknown"

# 🔴 FUNZIONE DI DEBUG PER VISUALIZZARE PACCHETTI
def debug_packet(packet_data, direction, description="", decrypted_data=None, is_telemetry=False):
    """Visualizza un pacchetto in formato hex e ascii"""
    try:
        print("\n" + "="*80)
        print(f"📦 PACCHETTO {direction} - {description}")
        print("="*80)
        
        # HEX dump
        print(f"\n🔹 HEX ({len(packet_data)} bytes):")
        hex_str = packet_data.hex()
        for i in range(0, len(hex_str), 64):
            print(f"   {hex_str[i:i+64]}")
        
        # ASCII dump
        print(f"\n🔹 ASCII:")
        ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in packet_data)
        for i in range(0, len(ascii_str), 64):
            print(f"   {ascii_str[i:i+64]}")
        
        # Tentativo di decodifica come msgpack
        try:
            unpacked = umsgpack.unpackb(packet_data)
            print(f"\n🔹 DECODIFICATO (msgpack):")
            
            # Se è telemetria, mostriamola in modo leggibile
            if isinstance(unpacked, dict):
                print(f"   📊 TELEMETRIA RILEVATA!")
                for sensor_id, sensor_data in unpacked.items():
                    sensor_name = get_telemetry_sensor_name(sensor_id)
                    print(f"      Sensore 0x{sensor_id:02x} ({sensor_name}): {sensor_data}")
                    
                    # Decodifica specifica per location (SID 0x02)
                    if sensor_id == 0x02 and isinstance(sensor_data, list) and len(sensor_data) >= 7:
                        try:
                            lat = struct.unpack("!i", sensor_data[0])[0]/1e6
                            lon = struct.unpack("!i", sensor_data[1])[0]/1e6
                            alt = struct.unpack("!i", sensor_data[2])[0]/1e2
                            speed = struct.unpack("!I", sensor_data[3])[0]/1e2
                            bearing = struct.unpack("!i", sensor_data[4])[0]/1e2
                            accuracy = struct.unpack("!H", sensor_data[5])[0]/1e2
                            last_update = sensor_data[6]
                            print(f"         📍 Posizione: {lat}, {lon}")
                            print(f"         📈 Altitudine: {alt}m")
                            print(f"         ⚡ Velocità: {speed} km/h")
                            print(f"         🧭 Direzione: {bearing}°")
                            print(f"         🎯 Precisione: {accuracy}m")
                            print(f"         🕐 Ultimo aggiornamento: {last_update}")
                        except Exception as e:
                            print(f"         Errore decodifica location: {e}")
                    
                    # Decodifica per battery (SID 0x04)
                    elif sensor_id == 0x04 and isinstance(sensor_data, list):
                        try:
                            charge = sensor_data[0]
                            charging = sensor_data[1]
                            temp = sensor_data[2] if len(sensor_data) > 2 else None
                            print(f"         🔋 Batteria: {charge}% {'(in carica)' if charging else '(scaricando)'}")
                            if temp:
                                print(f"         🌡️ Temperatura: {temp}°C")
                        except:
                            pass
                    
                    # Decodifica per pressure (SID 0x03)
                    elif sensor_id == 0x03:
                        print(f"         🌡️ Pressione: {sensor_data} mbar")
                    
                    # Decodifica per temperature (SID 0x07)
                    elif sensor_id == 0x07:
                        print(f"         🌡️ Temperatura: {sensor_data}°C")
                    
                    # Decodifica per humidity (SID 0x08)
                    elif sensor_id == 0x08:
                        print(f"         💧 Umidità: {sensor_data}%")
                    
                    # Decodifica per time (SID 0x01)
                    elif sensor_id == 0x01:
                        from datetime import datetime
                        dt = datetime.fromtimestamp(sensor_data)
                        print(f"         🕐 Tempo: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            
            else:
                print(f"   {unpacked}")
                
        except Exception as e:
            print(f"\n🔹 DECODIFICATO: Non è msgpack valido ({e})")
        
        print("="*80 + "\n")
    except Exception as e:
        print(f"❌ Errore nel debug packet: {e}")

def get_telemetry_sensor_name(sensor_id):
    """Restituisce il nome del sensore telemetria dato il suo SID"""
    sensor_names = {
        0x01: "Time",
        0x02: "Location",
        0x03: "Pressure",
        0x04: "Battery",
        0x05: "Physical Link",
        0x06: "Acceleration",
        0x07: "Temperature",
        0x08: "Humidity",
        0x09: "Magnetic Field",
        0x0A: "Ambient Light",
        0x0B: "Gravity",
        0x0C: "Angular Velocity",
        0x0E: "Proximity",
        0x0F: "Information",
        0x10: "Received",
        0x11: "Power Consumption",
        0x12: "Power Production",
        0x13: "Processor",
        0x14: "RAM",
        0x15: "NVM",
        0x16: "Tank",
        0x17: "Fuel",
        0x19: "RNS Transport",
        0x18: "LXMF Propagation",
        0x1A: "Connection Map",
        0xFF: "Custom"
    }
    return sensor_names.get(sensor_id, f"Sconosciuto (0x{sensor_id:02x})")

class Messenger:
    def __init__(self, storagepath="~/.rns_manager/storage", configpath="~/.rns_manager/config.json", identity_path=None):
        self.storagepath = os.path.expanduser(storagepath)
        self.configpath = os.path.expanduser(configpath)
        self.telemetry_dir = os.path.join(self.storagepath, "telemetry")
        self.attachments_dir = os.path.join(self.storagepath, "attachments")
        self.conversations_dir = os.path.join(self.storagepath, "conversations")
        self.raw_dir = os.path.join(self.storagepath, "raw")
        self.peers_db = os.path.join(self.storagepath, "peers.db")
        self.announces_cache_db = os.path.expanduser("~/.rns_manager/Cache/announces.db")
        self.rangetest_dir = os.path.join(self.storagepath, "rangetest")

        os.makedirs(self.storagepath, exist_ok=True)
        os.makedirs(self.telemetry_dir, exist_ok=True)
        os.makedirs(self.attachments_dir, exist_ok=True)
        os.makedirs(self.conversations_dir, exist_ok=True)
        os.makedirs(self.raw_dir, exist_ok=True)
        os.makedirs(self.rangetest_dir, exist_ok=True)

        self.db_lock = threading.Lock()
        self.config = self._load_config()
        self.rns = get_reticulum()
        
        if identity_path and os.path.exists(identity_path):
            self.identity = RNS.Identity.from_file(identity_path)
            print(f"✅ Identità caricata da file: {identity_path}")
        else:
            self.identity = self._choose_identity()
        
        self.display_name = self._load_display_name()
        self.lxmf_storage = os.path.dirname(self.storagepath)
        os.makedirs(self.lxmf_storage, exist_ok=True)

        self.router = LXMF.LXMRouter(identity=self.identity, storagepath=self.lxmf_storage)
        self.dest = self.router.register_delivery_identity(
            self.identity,
            display_name=self.display_name,
            stamp_cost=self.config.get('stamp_cost')
        )

        self.message_callback = None
        self.delivery_callbacks = {}
        self.failed_callbacks = {}
        self.progress_callbacks = {}
        self.sent_messages = {}

        self.router.register_delivery_callback(self._on_message)
        self.propagation_node = None
        self._set_propagation_node()
        self._init_peers_db()

        self.announce_handler = AnnounceHandler(self)
        RNS.Transport.register_announce_handler(self.announce_handler)
        self.received_raw = {}
        
        self.running = True
        self.progress_thread = threading.Thread(target=self._monitor_progress, daemon=True)
        self.progress_thread.start()
        self.active_rangetests = {}  # {peer_hash: {'interval': secondi, 'timer': threading.Timer, 'last_run': timestamp, 'data_points': []}}

        # 🔴 Inizializza telemetria provider con il config principale
        try:
            from telemetry_provider import TelemetryProvider, setup_telemetry_handlers
            self.telemetry_provider = TelemetryProvider(self.config)  # Passa il config esistente!
            setup_telemetry_handlers(self)
            print("✅ TelemetryProvider esterno inizializzato (usa config.json)")
        except Exception as e:
            print(f"⚠️ Errore inizializzazione telemetria esterna: {e}")
            self.telemetry_provider = None

    def cleanup(self):
        """Pulisce le risorse Reticulum prima di distruggere l'istanza"""
        self.running = False
        try:
            # Aspetta che il thread di progresso finisca
            if hasattr(self, 'progress_thread') and self.progress_thread.is_alive():
                self.progress_thread.join(timeout=1.0)
            
            # Deregistra la destinazione principale - CORREZIONE!
            if hasattr(self, 'dest') and self.dest:
                try:
                    # LXMRouter non ha unregister_delivery_identity, dobbiamo rimuovere dal router
                    if hasattr(self.router, 'delivery_destinations'):
                        self.router.delivery_destinations = [
                            d for d in self.router.delivery_destinations 
                            if d != self.dest
                        ]
                    print("   ✅ Destinazione rimossa dal router")
                except Exception as e:
                    print(f"   ⚠️ Errore rimozione: {e}")
            
            # Rimuovi l'announce handler
            if hasattr(self, 'announce_handler'):
                try:
                    RNS.Transport.unregister_announce_handler(self.announce_handler)
                except:
                    pass
            
            # Ferma il router
            if hasattr(self, 'router'):
                # Pulisci riferimenti
                self.router.delivery_callbacks = []
                self.router = None
                
            # Pulisci riferimenti
            self.dest = None
            self.announce_handler = None
            
            print("🧹 Messenger pulito correttamente")
            
        except Exception as e:
            print(f"⚠️ Errore durante cleanup: {e}")

    def _init_peers_db(self):
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            c.execute('''
                CREATE TABLE IF NOT EXISTS identities (
                    identity_hash TEXT PRIMARY KEY,
                    display_name TEXT,
                    first_seen REAL,
                    last_seen REAL,
                    notes TEXT,
                    group_name TEXT
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS destinations (
                    destination_hash TEXT PRIMARY KEY,
                    identity_hash TEXT,
                    aspect TEXT,
                    last_seen REAL,
                    hops INTEGER,
                    rssi REAL,
                    snr REAL,
                    q REAL,
                    app_data TEXT,
                    appearance TEXT,
                    FOREIGN KEY(identity_hash) REFERENCES identities(identity_hash) ON DELETE CASCADE
                )
            ''')
            c.execute('''
                CREATE TABLE IF NOT EXISTS sent_messages (
                    hash TEXT PRIMARY KEY,
                    destination_hash TEXT,
                    content TEXT,
                    title TEXT,
                    sent_at REAL,
                    status TEXT,
                    method TEXT,
                    delivered_at REAL,
                    failed_at REAL,
                    attempts INTEGER,
                    packed_size INTEGER
                )
            ''')
            
            # 🔴 CREA TABELLA MESSAGGI PER TUTTI I MESSAGGI (ricevuti e inviati)
            c.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    hash TEXT PRIMARY KEY,
                    source_hash TEXT,
                    destination_hash TEXT,
                    content TEXT,
                    title TEXT,
                    timestamp REAL,
                    method TEXT,
                    packed_size INTEGER,
                    rssi REAL,
                    snr REAL,
                    q REAL,
                    hops INTEGER,
                    incoming INTEGER,
                    raw_hex TEXT,
                    raw_ascii TEXT,
                    attachments TEXT
                )
            ''')
            
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_identity ON destinations(identity_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_aspect ON destinations(aspect)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_sent_status ON sent_messages(status)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_sent_dest ON sent_messages(destination_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_messages_dest ON messages(destination_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_messages_source ON messages(source_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(timestamp)')
            
            # 🔴 AGGIUNGI COLONNA appearance SE NON ESISTE
            try:
                c.execute("ALTER TABLE destinations ADD COLUMN appearance TEXT")
            except:
                pass
                
            conn.commit()
            conn.close()

    def _load_config(self):
        default = {
            "propagation_node": None,
            "delivery_mode": "direct",
            "max_retries": 3,
            "preferred_audio": "c2_3200",
            "stamp_cost": 0,
            "propagation_stamp_cost": 16,
            "accept_invalid_stamps": False,
            "max_stamp_retries": 3,
            "auto_send_voice": False,
            "try_propagation_on_fail": True,
            # 🔴 OPZIONI POSIZIONE
            "location_source": "fixed",  # "fixed" o "gpsd"
            "enable_location": True,
            "enable_system": True,
            "fixed_location": {
                "latitude": 45.4642,
                "longitude": 9.1900,
                "altitude": 120
            },
            "gpsd_host": "localhost",
            "gpsd_port": 2947,
            "appearance": ["📡", "4c9aff", "1a1f2e"]
        }
        if os.path.exists(self.configpath):
            with open(self.configpath, 'r') as f:
                config = json.load(f)
                default.update(config)
                print(f"📄 Config caricato: propagation_node={config.get('propagation_node', 'None')}")
        else:
            os.makedirs(os.path.dirname(self.configpath), exist_ok=True)
            with open(self.configpath, 'w') as f:
                json.dump(default, f, indent=4)
            print("📄 Creato config di default con opzioni telemetria")
        return default

    def _set_propagation_node(self):
        node_hash = self.config.get("propagation_node")
        if node_hash:
            try:
                self.propagation_node = bytes.fromhex(node_hash)
                self.router.set_outbound_propagation_node(self.propagation_node)
                print(f"📡 Propagation node impostato: {node_hash}")
            except Exception as e:
                print(f"❌ Errore propagation node: {e}")

    def _choose_identity(self):
        identities = []
        for f in os.listdir(self.storagepath):
            fpath = os.path.join(self.storagepath, f)
            if os.path.isfile(fpath) and os.path.getsize(fpath) == 64:
                identities.append(f)
        
        if identities:
            print(f"🔑 Trovate {len(identities)} identità. Uso la prima: {identities[0]}")
            path = os.path.join(self.storagepath, identities[0])
            return RNS.Identity.from_file(path)
        
        print("✨ Creazione nuova identità...")
        identity = RNS.Identity()
        path = os.path.join(self.storagepath, identity.hash.hex())
        identity.to_file(path)
        return identity

    def _load_display_name(self):
        name_file = os.path.join(self.storagepath, f"{self.identity.hash.hex()}.name")
        if os.path.exists(name_file):
            with open(name_file, 'r') as f:
                return f.read().strip()
        else:
            default_name = self.identity.hash.hex()[:8]
            with open(name_file, 'w') as f:
                f.write(default_name)
            return default_name

    def _save_attachment(self, msg_hash, typ, data, filename_hint=None):
        """🔴 DEPRECATO - Non usiamo più file su disco"""
        return None

    def _save_telemetry(self, source_hash, telemetry_obj, msg_data=None):
        """Salva la telemetria ricevuta in formato JSON"""
        try:
            ts = int(time.time())
            json_filename = f"{source_hash}_{ts}.json"
            json_path = os.path.join(self.telemetry_dir, json_filename)
            
            # Ottieni i dati in formato leggibile
            data = telemetry_obj.read_all()
            
            # CONVERTI I DATI NEL FORMATO LEGGIBILE
            formatted_data = {}
            
            # Time
            if 'time' in data and data['time']:
                formatted_data['time'] = {'utc': data['time'].get('utc', ts)}
            
            # Location
            if 'location' in data and data['location']:
                loc = data['location']
                formatted_data['location'] = {
                    'latitude': loc.get('latitude'),
                    'longitude': loc.get('longitude'),
                    'altitude': loc.get('altitude'),
                    'speed': loc.get('speed', 0),
                    'bearing': loc.get('bearing', 0),
                    'accuracy': loc.get('accuracy', 0),
                    'last_update': loc.get('last_update', ts)
                }
            
            # Information
            if 'information' in data and data['information']:
                formatted_data['information'] = {'contents': data['information'].get('contents', '')}
            
            # Battery
            if 'battery' in data and data['battery']:
                bat = data['battery']
                formatted_data['battery'] = {
                    'charge_percent': bat.get('charge_percent', 0),
                    'charging': bat.get('charging', False),
                    'temperature': bat.get('temperature')
                }
            
            # Temperature
            if 'temperature' in data and data['temperature']:
                formatted_data['temperature'] = data['temperature']
            
            # Humidity
            if 'humidity' in data and data['humidity']:
                formatted_data['humidity'] = data['humidity']
            
            # Pressure
            if 'pressure' in data and data['pressure']:
                formatted_data['pressure'] = data['pressure']
            
            # Ambient light
            if 'ambient_light' in data and data['ambient_light']:
                formatted_data['ambient_light'] = data['ambient_light']
            
            # Gravity
            if 'gravity' in data and data['gravity']:
                formatted_data['gravity'] = data['gravity']
            
            # Acceleration
            if 'acceleration' in data and data['acceleration']:
                formatted_data['acceleration'] = data['acceleration']
            
            # 🔴 CORREZIONE: AGGIUNGI DATI RADIO
            if msg_data:
                # RSSI
                if hasattr(msg_data, 'rssi') and msg_data.rssi is not None:
                    formatted_data['rssi'] = msg_data.rssi
                
                # SNR
                if hasattr(msg_data, 'snr') and msg_data.snr is not None:
                    formatted_data['snr'] = msg_data.snr
                
                # Q
                if hasattr(msg_data, 'q') and msg_data.q is not None:
                    formatted_data['q'] = msg_data.q
                
                # 🔴 HOPS - CORRETTO! Usa Transport.hops_to()
                try:
                    if msg_data.source_hash:
                        hops = RNS.Transport.hops_to(msg_data.source_hash)
                        if hops is not None:
                            formatted_data['hops'] = hops
                except Exception as e:
                    print(f"⚠️ Errore nel calcolo hops: {e}")
            
            with open(json_path, 'w') as f:
                json.dump(formatted_data, f, indent=2)
            
            # Salva anche il binario originale
            bin_filename = f"{source_hash}_{ts}.bin"
            bin_path = os.path.join(self.telemetry_dir, bin_filename)
            packed = telemetry_obj.packed()
            if packed and len(packed) > 0:
                with open(bin_path, 'wb') as f:
                    f.write(packed)
            
            print(f"📊 Telemetria salvata: {json_filename}")
            if msg_data:
                radio_info = []
                if hasattr(msg_data, 'rssi') and msg_data.rssi:
                    radio_info.append(f"RSSI={msg_data.rssi}dBm")
                if hasattr(msg_data, 'snr') and msg_data.snr:
                    radio_info.append(f"SNR={msg_data.snr}dB")
                if hasattr(msg_data, 'q') and msg_data.q:
                    radio_info.append(f"Q={msg_data.q}%")
                try:
                    if msg_data.source_hash:
                        hops = RNS.Transport.hops_to(msg_data.source_hash)
                        if hops:
                            radio_info.append(f"Hops={hops}")
                except:
                    pass
                if radio_info:
                    print(f"📡 Dati radio: {' • '.join(radio_info)}")
            
            return json_path
            
        except Exception as e:
            RNS.log(f"Errore salvataggio telemetria: {e}", RNS.LOG_ERROR)
            traceback.print_exc()
            return None

    def _save_peer_appearance(self, peer_hash, appearance):
        """Salva l'appearance di un peer nel database."""
        try:
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                
                # Crea JSON con l'appearance
                appearance_json = json.dumps({
                    'icon': appearance[0],
                    'fg': appearance[1].hex() if isinstance(appearance[1], bytes) else appearance[1],
                    'bg': appearance[2].hex() if isinstance(appearance[2], bytes) else appearance[2]
                })
                
                # Aggiorna la tabella destinations
                c.execute('''
                    UPDATE destinations SET appearance = ? WHERE destination_hash = ?
                ''', (appearance_json, peer_hash))
                
                conn.commit()
                conn.close()
                print(f"💾 Appearance salvata per {peer_hash[:8]}: {appearance[0]}")
        except Exception as e:
            print(f"❌ Errore salvataggio appearance: {e}")


    def _get_last_telemetry_timestamp(self, peer_hash):
        max_ts = 0
        prefix = f"{peer_hash}_"
        for fname in os.listdir(self.telemetry_dir):
            if fname.startswith(prefix) and fname.endswith('.bin'):
                try:
                    ts = int(fname.split('_')[1].split('.')[0])
                    if ts > max_ts:
                        max_ts = ts
                except:
                    continue
        return max_ts

    def _get_telemetry_since(self, peer_hash, timebase):
        stream = []
        prefix = f"{peer_hash}_"
        for fname in os.listdir(self.telemetry_dir):
            if fname.startswith(prefix) and fname.endswith('.bin'):
                try:
                    ts = int(fname.split('_')[1].split('.')[0])
                    if ts >= timebase:
                        with open(os.path.join(self.telemetry_dir, fname), 'rb') as f:
                            stream.append(f.read())
                except Exception as e:
                    RNS.log(f"Errore nel caricare telemetria {fname}: {e}", RNS.LOG_ERROR)
        return stream

    def _update_peer_from_message(self, source_hash, message):
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            now = time.time()
            hops = RNS.Transport.hops_to(bytes.fromhex(source_hash)) if source_hash else None
            rssi = getattr(message, 'rssi', None)
            snr = getattr(message, 'snr', None)
            q = getattr(message, 'q', None)
            
            # 🔴 ESTRAI APPEARANCE SOLO SE PRESENTE
            appearance_json = None
            if message.fields and FIELD_ICON_APPEARANCE in message.fields:
                appearance = message.fields[FIELD_ICON_APPEARANCE]
                if isinstance(appearance, (list, tuple)) and len(appearance) == 3:
                    appearance_json = json.dumps({
                        'icon': appearance[0],
                        'fg': appearance[1].hex() if isinstance(appearance[1], bytes) else appearance[1],
                        'bg': appearance[2].hex() if isinstance(appearance[2], bytes) else appearance[2]
                    })
                    print(f"🎨 Appearance salvata per {source_hash[:8]}: {appearance[0]}")
            
            c.execute("SELECT identity_hash FROM destinations WHERE destination_hash = ?", (source_hash,))
            row = c.fetchone()
            identity_hash = row[0] if row else None
            if not identity_hash:
                ident = RNS.Identity.recall(bytes.fromhex(source_hash))
                if ident:
                    identity_hash = ident.hash.hex()
            
            # 🔴 SE NON C'E' APPEARANCE, MANTIENI QUELLA ESISTENTE!
            if appearance_json is None:
                # Recupera appearance esistente
                c.execute("SELECT appearance FROM destinations WHERE destination_hash = ?", (source_hash,))
                existing = c.fetchone()
                if existing and existing[0]:
                    appearance_json = existing[0]
            
            c.execute('''
                INSERT INTO destinations (destination_hash, last_seen, hops, rssi, snr, q, app_data, appearance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(destination_hash) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    hops = excluded.hops,
                    rssi = excluded.rssi,
                    snr = excluded.snr,
                    q = excluded.q,
                    app_data = excluded.app_data,
                    appearance = COALESCE(excluded.appearance, appearance)
            ''', (source_hash, now, hops, rssi, snr, q, None, appearance_json))
            
            if identity_hash:
                c.execute('''
                    INSERT INTO identities (identity_hash, last_seen)
                    VALUES (?, ?)
                    ON CONFLICT(identity_hash) DO UPDATE SET
                        last_seen = excluded.last_seen
                ''', (identity_hash, now))
            conn.commit()
            conn.close()

    def _save_raw(self, msg_hash, packed):
        if not packed:
            return
        try:
            raw_path = os.path.join(self.raw_dir, f"{msg_hash}.raw")
            with open(raw_path, 'wb') as f:
                f.write(packed)
            hex_path = os.path.join(self.raw_dir, f"{msg_hash}.hex")
            with open(hex_path, 'w') as f:
                f.write(packed.hex())
        except Exception as e:
            RNS.log(f"Errore salvataggio raw per {msg_hash}: {e}", RNS.LOG_ERROR)

    def _message_to_dict(self, lxmf_message):
        fields_dict = {}
        if lxmf_message.fields:
            for ftype, fvalue in lxmf_message.fields.items():
                if ftype == FIELD_IMAGE and isinstance(fvalue, (list, tuple)) and len(fvalue) == 2:
                    fields_dict['image'] = {'type': fvalue[0], 'bytes': fvalue[1]}
                elif ftype == FIELD_AUDIO and isinstance(fvalue, (list, tuple)) and len(fvalue) == 2:
                    fields_dict['audio'] = {'mode': fvalue[0], 'bytes': fvalue[1]}
                elif ftype == FIELD_FILE_ATTACHMENTS and isinstance(fvalue, list):
                    attachments = []
                    for item in fvalue:
                        if isinstance(item, (list, tuple)) and len(item) == 2:
                            attachments.append({'name': item[0], 'bytes': item[1]})
                    fields_dict['file_attachments'] = attachments
                elif ftype == FIELD_ICON_APPEARANCE and isinstance(fvalue, (list, tuple)) and len(fvalue) == 3:
                    fields_dict['appearance'] = {
                        'icon': fvalue[0],
                        'foreground': fvalue[1].hex() if isinstance(fvalue[1], bytes) else fvalue[1],
                        'background': fvalue[2].hex() if isinstance(fvalue[2], bytes) else fvalue[2]
                    }
                elif ftype == FIELD_COMMANDS and isinstance(fvalue, list):
                    fields_dict['commands'] = fvalue
                elif ftype == FIELD_TELEMETRY and isinstance(fvalue, bytes):
                    fields_dict['telemetry'] = fvalue.hex()
                elif ftype == FIELD_TELEMETRY_STREAM and isinstance(fvalue, list):
                    stream = []
                    for packed in fvalue:
                        if isinstance(packed, bytes):
                            stream.append(packed.hex())
                    fields_dict['telemetry_stream'] = stream
                else:
                    if isinstance(fvalue, bytes):
                        fields_dict[ftype] = fvalue.hex()
                    else:
                        fields_dict[ftype] = fvalue
        rssi = getattr(lxmf_message, 'rssi', None)
        snr = getattr(lxmf_message, 'snr', None)
        q = getattr(lxmf_message, 'q', None)
        packed_size = len(lxmf_message.packed) if lxmf_message.packed else 0
        return {
            'hash': lxmf_message.hash.hex(),
            'from': lxmf_message.source_hash.hex() if lxmf_message.source_hash else None,
            'to': lxmf_message.destination_hash.hex() if lxmf_message.destination_hash else None,
            'incoming': lxmf_message.incoming,
            'content': lxmf_message.content.decode('utf-8') if lxmf_message.content else '',
            'title': lxmf_message.title.decode('utf-8') if lxmf_message.title else '',
            'timestamp': lxmf_message.timestamp,
            'method': lxmf_message.method,
            'state': lxmf_message.state,
            'progress': lxmf_message.progress,
            'delivery_attempts': lxmf_message.delivery_attempts,
            'rssi': rssi,
            'snr': snr,
            'q': q,
            'packed_size': packed_size,
            'fields': fields_dict,
            'raw_hex': lxmf_message.packed.hex() if lxmf_message.packed else None,
            'raw_ascii': ''.join(chr(b) if 32 <= b < 127 else '.' for b in lxmf_message.packed) if lxmf_message.packed else None,
        }

    def _convert_opus_to_codec2(self, opus_bytes, target_mode):
        temp_opus = None
        temp_wav = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as f:
                f.write(opus_bytes)
                temp_opus = f.name
            temp_wav = temp_opus.replace('.opus', '.wav')
            cmd = ['ffmpeg', '-i', temp_opus, '-acodec', 'pcm_s16le',
                   '-ac', '1', '-ar', '8000', '-f', 'wav', '-y', temp_wav]
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode != 0 or not os.path.exists(temp_wav):
                return None
            with wave.open(temp_wav, 'rb') as wav:
                frames = wav.readframes(wav.getnframes())
                samples = np.frombuffer(frames, dtype=np.int16)
            import audio_codec
            return audio_codec.encode_codec2(samples, target_mode)
        except Exception as e:
            print(f"Conversione Opus->Codec2 fallita: {e}")
            return None
        finally:
            for f in [temp_opus, temp_wav]:
                if f and os.path.exists(f):
                    try:
                        os.unlink(f)
                    except:
                        pass

    def _save_message(self, message, incoming=True):
        """Salva un messaggio nel database con tutti i dati"""
        try:
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                
                # Calcola hops
                hops = None
                if message.source_hash:
                    try:
                        hops = RNS.Transport.hops_to(message.source_hash)
                    except:
                        pass
                
                # Raw hex/ascii
                raw_hex = None
                raw_ascii = None
                if message.packed:
                    raw_hex = message.packed.hex()
                    raw_ascii = ''.join(chr(b) if 32 <= b < 127 else '.' for b in message.packed)
                
                # 📦 ESTRAI ATTACHMENTS - FORMATO CORRETTO
                attachments_list = []
                if message.fields:
                    
                    # IMMAGINI
                    if FIELD_IMAGE in message.fields:
                        img = message.fields[FIELD_IMAGE]
                        if isinstance(img, (list, tuple)) and len(img) == 2:
                            attachments_list.append({
                                'type': 'image',
                                'format': img[0],  # stringa
                                'size': len(img[1]),
                                'bytes': img[1].hex()  # SALVA I BYTES!
                            })
                            print(f"📸 Salvata immagine: {img[0]}, {len(img[1])} bytes")
                    
                    # AUDIO
                    if FIELD_AUDIO in message.fields:
                        aud = message.fields[FIELD_AUDIO]
                        if isinstance(aud, (list, tuple)) and len(aud) == 2:
                            attachments_list.append({
                                'type': 'audio',
                                'mode': aud[0],  # int
                                'size': len(aud[1]),
                                'bytes': aud[1].hex()  # SALVA I BYTES!
                            })
                            mode_name = "Codec2" if aud[0] < 16 else "Opus"
                            print(f"🎵 Salvato audio: {mode_name} mode={aud[0]}, {len(aud[1])} bytes")
                    
                    # FILE
                    if FIELD_FILE_ATTACHMENTS in message.fields:
                        files = message.fields[FIELD_FILE_ATTACHMENTS]
                        if isinstance(files, list):
                            for f in files:
                                if isinstance(f, (list, tuple)) and len(f) == 2:
                                    attachments_list.append({
                                        'type': 'file',
                                        'name': f[0],  # stringa
                                        'size': len(f[1]),
                                        'bytes': f[1].hex()  # SALVA I BYTES!
                                    })
                                    print(f"📎 Salvato file: {f[0]}, {len(f[1])} bytes")
                
                attachments_json = json.dumps(attachments_list) if attachments_list else None
                
                # Determina metodo come stringa
                method_str = 'direct'
                if hasattr(message, 'method'):
                    if message.method == 0x03:
                        method_str = 'propagated'
                    elif message.method == 0x01:
                        method_str = 'opportunistic'
                
                # Inserisci nel database
                c.execute('''
                    INSERT OR REPLACE INTO messages 
                    (hash, source_hash, destination_hash, content, title, timestamp, 
                     method, packed_size, rssi, snr, q, hops, incoming, raw_hex, raw_ascii, attachments)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    message.hash.hex(),
                    message.source_hash.hex() if message.source_hash else None,
                    message.destination_hash.hex() if message.destination_hash else None,
                    message.content.decode('utf-8') if message.content else '',
                    message.title.decode('utf-8') if message.title else '',
                    message.timestamp,
                    method_str,
                    len(message.packed) if message.packed else 0,
                    getattr(message, 'rssi', None),
                    getattr(message, 'snr', None),
                    getattr(message, 'q', None),
                    hops,
                    1 if incoming else 0,
                    raw_hex,
                    raw_ascii,
                    attachments_json
                ))
                
                conn.commit()
                conn.close()
                
                if attachments_list:
                    print(f"💾 Messaggio salvato con {len(attachments_list)} attachments")
                
        except Exception as e:
            print(f"❌ Errore salvataggio messaggio: {e}")
            traceback.print_exc()

    def _on_message(self, message):
        # 🔴 DEBUG: Visualizza il pacchetto ricevuto (cifrato) e il contenuto decifrato
        if message.packed:
            # Ricostruisci i dati decifrati (sono nel message.payload)
            if hasattr(message, 'payload') and message.payload:
                try:
                    # I dati decifrati sono in message.payload come lista msgpack
                    decrypted = umsgpack.packb(message.payload)  # Re-impacchetta per il debug
                except:
                    decrypted = None
            else:
                decrypted = None
                
            debug_packet(message.packed, "⬇️ RICEVUTO", 
                        f"Hash: {message.hash.hex()[:16]}... Da: {message.source_hash.hex()[:16]}...",
                        decrypted)
        
        # 🔴 DEBUG ESTESO: Mostra TUTTI i campi del messaggio decifrato
        print("\n" + "="*80)
        print(f"📦 CONTENUTO DECIFRATO - Hash: {message.hash.hex()[:16]}...")
        print("="*80)
        print(f"   📝 Titolo: {message.title.decode('utf-8', errors='ignore')}")
        print(f"   📝 Contenuto: {message.content.decode('utf-8', errors='ignore')}")
        
        if message.fields:
            print(f"\n   📌 CAMPI LXMF (FIELDS):")
            for field_type, field_value in message.fields.items():
                field_name = {
                    0x02: "TELEMETRY",
                    0x03: "TELEMETRY_STREAM",
                    0x04: "ICON_APPEARANCE",
                    0x05: "FILE_ATTACHMENTS",
                    0x06: "IMAGE",
                    0x07: "AUDIO",
                    0x09: "COMMANDS"
                }.get(field_type, f"UNKNOWN(0x{field_type:02x})")
                
                print(f"\n      📌 Campo 0x{field_type:02x} ({field_name}):")
                
                if field_type == FIELD_TELEMETRY and isinstance(field_value, bytes):
                    print(f"         📊 TELEMETRIA ({len(field_value)} bytes)")
                    try:
                        # Decodifica la telemetria
                        telemetry_data = umsgpack.unpackb(field_value)
                        print(f"         📊 Dati telemetria grezzi: {telemetry_data}")
                        
                        # Decodifica ogni sensore
                        for sensor_id, sensor_value in telemetry_data.items():
                            sensor_name = {
                                0x01: "TIME", 0x02: "LOCATION", 0x03: "PRESSURE",
                                0x04: "BATTERY", 0x05: "PHYSICAL_LINK", 0x06: "ACCELERATION",
                                0x07: "TEMPERATURE", 0x08: "HUMIDITY", 0x09: "MAGNETIC_FIELD",
                                0x0A: "AMBIENT_LIGHT", 0x0B: "GRAVITY", 0x0C: "ANGULAR_VELOCITY",
                                0x0E: "PROXIMITY", 0x0F: "INFORMATION", 0x10: "RECEIVED",
                                0x11: "POWER_CONSUMPTION", 0x12: "POWER_PRODUCTION",
                                0x13: "PROCESSOR", 0x14: "RAM", 0x15: "NVM",
                                0x16: "TANK", 0x17: "FUEL", 0x19: "RNS_TRANSPORT",
                                0x18: "LXMF_PROPAGATION", 0x1A: "CONNECTION_MAP",
                                0xFF: "CUSTOM"
                            }.get(sensor_id, f"UNKNOWN(0x{sensor_id:02x})")
                            
                            print(f"            📍 Sensore {sensor_name} (0x{sensor_id:02x}):")
                            
                            # Decodifica specifica per tipo
                            if sensor_id == 0x01 and isinstance(sensor_value, int):  # TIME
                                from datetime import datetime
                                dt = datetime.fromtimestamp(sensor_value)
                                print(f"               UTC: {sensor_value} -> {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                            
                            elif sensor_id == 0x02 and isinstance(sensor_value, list) and len(sensor_value) >= 7:  # LOCATION
                                try:
                                    lat = struct.unpack("!i", sensor_value[0])[0]/1e6
                                    lon = struct.unpack("!i", sensor_value[1])[0]/1e6
                                    alt = struct.unpack("!i", sensor_value[2])[0]/1e2
                                    speed = struct.unpack("!I", sensor_value[3])[0]/1e2
                                    bearing = struct.unpack("!i", sensor_value[4])[0]/1e2
                                    accuracy = struct.unpack("!H", sensor_value[5])[0]/1e2
                                    last_update = sensor_value[6]
                                    print(f"               📍 Posizione: {lat}, {lon}")
                                    print(f"               📈 Altitudine: {alt}m")
                                    print(f"               ⚡ Velocità: {speed} km/h")
                                    print(f"               🧭 Direzione: {bearing}°")
                                    print(f"               🎯 Precisione: {accuracy}m")
                                    print(f"               🕐 Ultimo aggiornamento: {last_update}")
                                except Exception as e:
                                    print(f"               Errore decodifica location: {e}")
                            
                            elif sensor_id == 0x04 and isinstance(sensor_value, list):  # BATTERY
                                try:
                                    charge = sensor_value[0]
                                    charging = sensor_value[1]
                                    temp = sensor_value[2] if len(sensor_value) > 2 else None
                                    print(f"               🔋 Carica: {charge}%")
                                    print(f"               ⚡ In carica: {charging}")
                                    if temp:
                                        print(f"               🌡️ Temperatura: {temp}°C")
                                except:
                                    print(f"               {sensor_value}")
                            
                            elif sensor_id == 0x07 and sensor_value is not None:  # TEMPERATURE
                                print(f"               🌡️ Temperatura: {sensor_value}°C")
                            
                            elif sensor_id == 0x08 and sensor_value is not None:  # HUMIDITY
                                print(f"               💧 Umidità: {sensor_value}%")
                            
                            elif sensor_id == 0x03 and sensor_value is not None:  # PRESSURE
                                print(f"               🌡️ Pressione: {sensor_value} mbar")
                            
                            elif sensor_id == 0x0A and sensor_value is not None:  # AMBIENT_LIGHT
                                print(f"               💡 Luce: {sensor_value} lux")
                            
                            elif sensor_id == 0x0B and isinstance(sensor_value, list) and len(sensor_value) >= 3:  # GRAVITY
                                try:
                                    print(f"               📊 Gravità: x={sensor_value[0]}, y={sensor_value[1]}, z={sensor_value[2]} m/s²")
                                except:
                                    print(f"               {sensor_value}")
                            
                            elif sensor_id == 0x06 and isinstance(sensor_value, list) and len(sensor_value) >= 3:  # ACCELERATION
                                try:
                                    print(f"               📊 Accelerazione: x={sensor_value[0]}, y={sensor_value[1]}, z={sensor_value[2]} m/s²")
                                except:
                                    print(f"               {sensor_value}")
                            
                            elif sensor_id == 0x05 and sensor_value is not None:  # PHYSICAL_LINK
                                print(f"               📡 Link fisico: {sensor_value}")
                            
                            elif sensor_id == 0x09 and sensor_value is not None:  # MAGNETIC_FIELD
                                print(f"               🧲 Campo magnetico: {sensor_value}")
                            
                            elif sensor_id == 0x0C and sensor_value is not None:  # ANGULAR_VELOCITY
                                print(f"               🔄 Velocità angolare: {sensor_value}")
                            
                            elif sensor_id == 0x0E and sensor_value is not None:  # PROXIMITY
                                print(f"               📏 Prossimità: {sensor_value}")
                            
                            elif sensor_id == 0x0F and sensor_value is not None:  # INFORMATION
                                print(f"               ℹ️ Informazione: {sensor_value}")
                            
                            elif sensor_id == 0x10 and isinstance(sensor_value, list):  # RECEIVED
                                try:
                                    print(f"               📨 Ricevuto da: {sensor_value[0]}")
                                    print(f"               🔀 Via: {sensor_value[1]}")
                                    if len(sensor_value) > 3:
                                        print(f"               📏 Distanza geodesica: {sensor_value[2]}m")
                                        print(f"               📏 Distanza euclidea: {sensor_value[3]}m")
                                except:
                                    print(f"               {sensor_value}")
                            
                            elif sensor_id == 0x11 and sensor_value is not None:  # POWER_CONSUMPTION
                                print(f"               ⚡ Consumo: {sensor_value}W")
                            
                            elif sensor_id == 0x12 and sensor_value is not None:  # POWER_PRODUCTION
                                print(f"               ⚡ Produzione: {sensor_value}W")
                            
                            elif sensor_id == 0x13 and sensor_value is not None:  # PROCESSOR
                                print(f"               💻 Processore: {sensor_value}%")
                            
                            elif sensor_id == 0x14 and sensor_value is not None:  # RAM
                                print(f"               🧠 RAM: {sensor_value}%")
                            
                            elif sensor_id == 0x15 and sensor_value is not None:  # NVM
                                print(f"               💾 Memoria: {sensor_value}%")
                            
                            elif sensor_id == 0x16 and sensor_value is not None:  # TANK
                                print(f"               🛢️ Serbatoio: {sensor_value}%")
                            
                            elif sensor_id == 0x17 and sensor_value is not None:  # FUEL
                                print(f"               ⛽ Carburante: {sensor_value}%")
                            
                            elif sensor_id == 0x19 and sensor_value is not None:  # RNS_TRANSPORT
                                print(f"               🚇 Trasporto RNS: {sensor_value}")
                            
                            elif sensor_id == 0x18 and sensor_value is not None:  # LXMF_PROPAGATION
                                print(f"               📡 Propagazione LXMF: {sensor_value}")
                            
                            elif sensor_id == 0x1A and sensor_value is not None:  # CONNECTION_MAP
                                print(f"               🗺️ Mappa connessioni: {sensor_value}")
                            
                            elif sensor_id == 0xFF and sensor_value is not None:  # CUSTOM
                                print(f"               🔧 Custom: {sensor_value}")
                            
                            else:
                                print(f"               {sensor_value}")
                    
                    except Exception as e:
                        print(f"         Errore decodifica telemetria: {e}")
                
                elif field_type == FIELD_ICON_APPEARANCE and isinstance(field_value, (list, tuple)) and len(field_value) == 3:
                    print(f"         🎨 Icona: {field_value[0]}")
                    print(f"         🎨 Foreground: {field_value[1].hex() if isinstance(field_value[1], bytes) else field_value[1]}")
                    print(f"         🎨 Background: {field_value[2].hex() if isinstance(field_value[2], bytes) else field_value[2]}")
                
                elif field_type == FIELD_IMAGE and isinstance(field_value, (list, tuple)) and len(field_value) == 2:
                    print(f"         🖼️ Immagine: {len(field_value[1])} bytes, tipo: {field_value[0]}")
                
                elif field_type == FIELD_AUDIO and isinstance(field_value, (list, tuple)) and len(field_value) == 2:
                    mode_name = "Codec2" if field_value[0] <= 0x09 else "Opus"
                    print(f"         🎵 Audio: {len(field_value[1])} bytes, mode: {mode_name} (0x{field_value[0]:02x})")
                
                elif field_type == FIELD_FILE_ATTACHMENTS and isinstance(field_value, list):
                    print(f"         📎 Allegati: {len(field_value)} file")
                    for i, att in enumerate(field_value):
                        if isinstance(att, (list, tuple)) and len(att) == 2:
                            print(f"            File {i+1}: {att[0]} ({len(att[1])} bytes)")
                
                elif field_type == FIELD_COMMANDS and isinstance(field_value, list):
                    print(f"         🎮 Comandi: {field_value}")
                
                elif field_type == FIELD_TELEMETRY_STREAM and isinstance(field_value, list):
                    print(f"         📊 Stream telemetria: {len(field_value)} pacchetti")
                
                else:
                    print(f"         {field_value}")
        
        print("="*80 + "\n")
            
        attachments = []
        telemetry_obj = None
        appearance = None

        if message.fields:
            # GESTIONE IMMAGINE RICEVUTA - SECONDO STANDARD LXMF
            if FIELD_IMAGE in message.fields:
                img_data = message.fields[FIELD_IMAGE]
                if isinstance(img_data, (list, tuple)) and len(img_data) == 2:
                    image_type = img_data[0]
                    image_bytes = img_data[1]
                    if isinstance(image_bytes, bytes):
                        # 🔴 NON SALVARE SU DISCO, AGGIUNGI AI METADATI
                        attachments.append(("image", {
                            'type': image_type,
                            'bytes': image_bytes.hex(),
                            'size': len(image_bytes)
                        }))

            if FIELD_AUDIO in message.fields:
                audio_data = message.fields[FIELD_AUDIO]
                if isinstance(audio_data, (list, tuple)) and len(audio_data) == 2:
                    audio_mode = audio_data[0]
                    audio_bytes = audio_data[1]
                    if isinstance(audio_bytes, bytes):
                        # 🔴 NON SALVARE SU DISCO, AGGIUNGI AI METADATI
                        attachments.append(("audio", {
                            'mode': audio_mode,
                            'bytes': audio_bytes.hex(),
                            'size': len(audio_bytes)
                        }))

            if FIELD_FILE_ATTACHMENTS in message.fields:
                file_list = message.fields[FIELD_FILE_ATTACHMENTS]
                if isinstance(file_list, list):
                    for item in file_list:
                        if isinstance(item, (list, tuple)) and len(item) == 2:
                            file_name = item[0]
                            file_bytes = item[1]
                            if isinstance(file_bytes, bytes):
                                # 🔴 NON SALVARE SU DISCO, AGGIUNGI AI METADATI
                                attachments.append(("file", {
                                    'name': file_name,
                                    'bytes': file_bytes.hex(),
                                    'size': len(file_bytes)
                                }))

            if FIELD_TELEMETRY in message.fields:
                try:
                    telemetry_obj = telemeter.Telemeter.from_packed(message.fields[FIELD_TELEMETRY])
                    self._save_telemetry(message.source_hash.hex(), telemetry_obj, message)
                    
                    # 🔴 SE IL PEER CHE HA INVIATO LA TELEMETRIA È IN RANGETEST, SALVA!
                    source_hash = message.source_hash.hex()
                    if source_hash in self.active_rangetests:
                        self._save_rangetest_data(source_hash, telemetry_obj, message)
                        print(f"✅ Rangetest: telemetria ricevuta da {source_hash[:8]}")
                    
                    print(f"📊 TELEMETRY object creato: {telemetry_obj}")
                except Exception as e:
                    RNS.log(f"Errore parsing telemetria: {e}", RNS.LOG_ERROR)
                    traceback.print_exc()

            if FIELD_TELEMETRY_STREAM in message.fields:
                try:
                    stream = []
                    for packed in message.fields[FIELD_TELEMETRY_STREAM]:
                        t = telemeter.Telemeter.from_packed(packed)
                        if t:
                            stream.append(t)
                    telemetry_obj = stream
                    print(f"📊 TELEMETRY STREAM: {len(stream)} pacchetti")
                except Exception as e:
                    RNS.log(f"Errore parsing stream telemetria: {e}", RNS.LOG_ERROR)

            if FIELD_ICON_APPEARANCE in message.fields:
                appearance = message.fields[FIELD_ICON_APPEARANCE]
                print(f"🎨 APPEARANCE: {appearance}")
                self._save_peer_appearance(message.source_hash.hex(), appearance)

            if FIELD_COMMANDS in message.fields:
                self._handle_commands(message, message.fields[FIELD_COMMANDS])

        source_hash_hex = message.source_hash.hex()
        conv_dir = os.path.join(self.conversations_dir, source_hash_hex)
        os.makedirs(conv_dir, exist_ok=True)
        lxb_path = message.write_to_directory(conv_dir)
        if lxb_path:
            RNS.log(f"💾 Messaggio ricevuto salvato in: {lxb_path}", RNS.LOG_DEBUG)

        raw_hex = None
        raw_ascii = None
        packed_size = 0
        if message.packed:
            packed_size = len(message.packed)
            raw_hex = message.packed.hex()
            raw_ascii = ''.join(chr(b) if 32 <= b < 127 else '.' for b in message.packed)
            self._save_raw(message.hash.hex(), message.packed)

        self._update_peer_from_message(source_hash_hex, message)

        # 🔴 SALVA IL MESSAGGIO NEL DATABASE
        self._save_message(message, incoming=True)

        msg_dict = {
            'from': source_hash_hex,
            'to': message.destination_hash.hex(),
            'content': message.content.decode('utf-8') if message.content else '',
            'title': message.title.decode('utf-8') if message.title else '',
            'time': message.timestamp,
            'hash': message.hash.hex(),
            'size': len(message.content) if message.content else 0,
            'packed_size': packed_size,
            'method': ('propagated' if message.method == 0x03 else
                      ('opportunistic' if message.method == 0x01 else 'direct')),
            'signature_valid': message.signature_validated,
            'attachments': attachments,
            'telemetry': telemetry_obj,
            'appearance': appearance,
            'rssi': getattr(message, 'rssi', None),
            'snr': getattr(message, 'snr', None),
            'q': getattr(message, 'q', None),
            'hops': RNS.Transport.hops_to(message.source_hash) if message.source_hash else None,
            'raw_hex': raw_hex,
            'raw_ascii': raw_ascii,
            'fields': message.fields,
        }

        meta_path = os.path.join(self.raw_dir, f"{message.hash.hex()}.json")
        try:
            with open(meta_path, 'w') as f:
                json.dump({
                    'content': msg_dict['content'],
                    'title': msg_dict['title'],
                    'rssi': msg_dict.get('rssi'),
                    'snr': msg_dict.get('snr'),
                    'q': msg_dict.get('q'),
                    'hops': msg_dict.get('hops'),
                    'time': msg_dict['time'],
                    'from': msg_dict['from'],
                    'to': msg_dict['to'],
                    'method': msg_dict['method'],
                    'packed_size': msg_dict['packed_size']
                }, f, indent=2)
        except Exception as e:
            pass

        if raw_hex:
            self.received_raw[msg_dict['hash']] = {'hex': raw_hex, 'ascii': raw_ascii}

        if self.message_callback:
            self.message_callback(msg_dict)

    def _handle_commands(self, message, commands):
        source = message.source_hash.hex()
        for cmd_dict in commands:
            if not isinstance(cmd_dict, dict):
                continue
            for cmd_type, cmd_data in cmd_dict.items():
                if cmd_type == Commands.PING:
                    self.send(source, "Ping reply", callbacks=None)
                elif cmd_type == Commands.ECHO:
                    if isinstance(cmd_data, bytes):
                        text = cmd_data.decode('utf-8', errors='replace')
                    else:
                        text = str(cmd_data)
                    self.send(source, f"Echo reply: {text}", callbacks=None)
                elif cmd_type == Commands.SIGNAL_REPORT:
                    q = getattr(message, 'q', None)
                    rssi = getattr(message, 'rssi', None)
                    snr = getattr(message, 'snr', None)
                    report = []
                    if q is not None:
                        report.append(f"Link Quality: {q}%")
                    if rssi is not None:
                        report.append(f"RSSI: {rssi} dBm")
                    if snr is not None:
                        report.append(f"SNR: {snr} dB")
                    if not report:
                        report.append("No reception info available")
                    self.send(source, "\n".join(report), callbacks=None)

                # 🔴 GESTIONE COMANDI CUSTOM (tipo 0x00)
                elif cmd_type == Commands.CUSTOM:
                    print(f"📦 Comando custom ricevuto: {cmd_data}")
                    
                    # Parsa comando in formato "rangetest 15 start"
                    if isinstance(cmd_data, str):
                        parts = cmd_data.lower().split()
                        if len(parts) >= 3 and parts[0] == 'rangetest':
                            try:
                                interval = int(parts[1])
                                action = parts[2]
                                
                                if action == 'start' and interval > 0:
                                    self._start_rangetest(source, interval)
                                    self.send(source, f"✅ Rangetest avviato ogni {interval} secondi", callbacks=None)
                                elif action == 'stop':
                                    self._stop_rangetest(source)
                                    self.send(source, "✅ Rangetest fermato", callbacks=None)
                                else:
                                    self.send(source, "❌ Comando rangetest non valido", callbacks=None)
                            except ValueError:
                                self.send(source, "❌ Intervallo non valido", callbacks=None)
                        else:
                            self.send(source, f"❌ Comando sconosciuto: {cmd_data}", callbacks=None)
                    else:
                        self.send(source, f"❌ Formato comando non supportato", callbacks=None)

                # 🔴 TELEMETRY_REQUEST - CORREZIONE COMPLETA
                elif cmd_type == Commands.TELEMETRY_REQUEST:
                    print(f"📡 Richiesta telemetria da {source[:16]}...")
                    
                    try:
                        # Raccogli info radio del messaggio ricevuto
                        q = getattr(message, 'q', None)
                        rssi = getattr(message, 'rssi', None)
                        snr = getattr(message, 'snr', None)
                        hops = RNS.Transport.hops_to(message.source_hash) if message.source_hash else None
                        
                        # Crea contenuto con info radio
                        content_parts = ["📊 Telemetria"]
                        if q is not None:
                            content_parts.append(f"📶 Q:{q}%")
                        if rssi is not None:
                            content_parts.append(f"📡 RSSI:{rssi}dBm")
                        if snr is not None:
                            content_parts.append(f"🔊 SNR:{snr}dB")
                        if hops is not None:
                            content_parts.append(f"🔄 Hops:{hops}")
                        
                        content_str = " | ".join(content_parts)
                        
                        # Prepara fields
                        fields = {}
                        
                        # 📊 TELEMETRIA (se disponibile)
                        if hasattr(self, 'telemetry_provider') and self.telemetry_provider:
                            telemetry_packed = self.telemetry_provider.get_telemetry_packed()
                            if telemetry_packed:
                                fields[FIELD_TELEMETRY] = telemetry_packed
                                print(f"📦 Telemetria packata: {len(telemetry_packed)} bytes")
                        
                        # 🎨 ICON APPEARANCE - QUESTA PARTE MANCAVA!
                        appearance = self.config.get('appearance', ['antenna', '4c9aff', '1a1f2e'])
                        if appearance and len(appearance) == 3:
                            try:
                                fg_bytes = bytes.fromhex(appearance[1].lstrip('#'))
                                bg_bytes = bytes.fromhex(appearance[2].lstrip('#'))
                                fields[FIELD_ICON_APPEARANCE] = [appearance[0], fg_bytes, bg_bytes]
                                print(f"🎨 Icona aggiunta alla risposta: {appearance[0]}")
                            except Exception as e:
                                print(f"⚠️ Errore conversione appearance: {e}")
                        
                        # Invia risposta
                        self.send(
                            source,
                            content_str,
                            title="Telemetry Reply",
                            fields=fields,
                            callbacks=None
                        )
                        print(f"✅ Risposta telemetria inviata a {source[:16]}")
                        
                    except Exception as e:
                        print(f"❌ Errore telemetria: {e}")
                        traceback.print_exc()


    def _start_rangetest(self, peer_hash, interval):
        """Avvia un rangetest periodico verso un peer"""
        self._stop_rangetest(peer_hash)
        
        print(f"📡 Avvio rangetest verso {peer_hash[:8]} ogni {interval}s")
        
        def run_rangetest():
            if peer_hash not in self.active_rangetests:
                return
            
            self.active_rangetests[peer_hash]['last_run'] = time.time()
            print(f"📡 Rangetest: richiesta telemetria a {peer_hash[:8]}")
            
            # 🔴 USA GLI STESSI CALLBACK CHE FUNZIONANO PER LE RICHIESTE SINGOLE!
            def on_delivery(info):
                print(f"✅ Rangetest: richiesta consegnata a {peer_hash[:8]}")
                # La risposta arriverà in _on_message!
            
            def on_failed(info):
                print(f"❌ Rangetest: richiesta fallita per {peer_hash[:8]}")
            
            # Richiedi telemetria con gli stessi callback delle richieste singole
            self.request_telemetry(
                peer_hash, 
                callbacks={
                    'delivery': on_delivery,
                    'failed': on_failed
                }
            )
            
            if peer_hash in self.active_rangetests:
                timer = threading.Timer(interval, run_rangetest)
                timer.daemon = True
                timer.start()
                self.active_rangetests[peer_hash]['timer'] = timer
        
        # Avvia il primo ciclo DOPO interval secondi
        timer = threading.Timer(interval, run_rangetest)
        timer.daemon = True
        timer.start()
        
        self.active_rangetests[peer_hash] = {
            'interval': interval,
            'timer': timer,
            'last_run': time.time(),
            'start_time': time.time(),
            'data_points': []
        }
        
        print(f"✅ Rangetest avviato verso {peer_hash[:8]}")

    def _stop_rangetest(self, peer_hash):
        """Ferma un rangetest attivo"""
        if peer_hash in self.active_rangetests:
            # Ferma il timer
            if 'timer' in self.active_rangetests[peer_hash]:
                timer = self.active_rangetests[peer_hash]['timer']
                timer.cancel()
            
            # Esporta i dati in KML/GPX prima di rimuovere
            self._export_rangetest_data(peer_hash)
            
            # Rimuovi dallo stato attivo
            del self.active_rangetests[peer_hash]
            print(f"📡 Rangetest fermato per {peer_hash[:8]}")

    def _save_rangetest_data(self, peer_hash, telemetry_obj, original_message):
        """Salva un punto dati del rangetest per il peer B"""
        if peer_hash in self.active_rangetests:
            try:
                # Leggi i dati di telemetria di B
                data = telemetry_obj.read_all()
                
                # Posizione di B
                location = None
                if 'location' in data:
                    loc = data['location']
                    location = {
                        'latitude': loc.get('latitude'),
                        'longitude': loc.get('longitude'),
                        'altitude': loc.get('altitude', 0),
                        'speed': loc.get('speed', 0),
                        'bearing': loc.get('bearing', 0),
                        'accuracy': loc.get('accuracy', 0)
                    }
                
                # Dati radio della ricezione (link A←B)
                radio = {
                    'rssi': getattr(original_message, 'rssi', None),
                    'snr': getattr(original_message, 'snr', None),
                    'q': getattr(original_message, 'q', None),
                    'hops': RNS.Transport.hops_to(original_message.source_hash) if original_message.source_hash else None
                }
                
                # Timestamp di A quando ha ricevuto
                received_time = time.time()
                
                data_point = {
                    'timestamp': received_time,
                    'peer': 'B',  # Questo è il peer in movimento
                    'peer_hash': peer_hash,
                    'location': location,
                    'radio': radio,
                    'battery': data.get('battery'),
                    'temperature': data.get('temperature'),
                    'ambient_light': data.get('ambient_light'),
                    'raw_data': data
                }
                
                self.active_rangetests[peer_hash]['data_points'].append(data_point)
                print(f"📊 Punto B {len(self.active_rangetests[peer_hash]['data_points'])} salvato - Posizione B: {location}")
                
            except Exception as e:
                print(f"❌ Errore salvataggio dati rangetest: {e}")

    def _export_rangetest_data(self, peer_hash):
        """Esporta i dati del rangetest in formato KML e GPX"""
        if peer_hash not in self.active_rangetests:
            return
        
        data = self.active_rangetests[peer_hash]
        points = data['data_points']
        
        if not points:
            print(f"⚠️ Nessun dato da esportare per {peer_hash[:8]}")
            return
        
        # Crea directory per i rangetest
        rangetest_dir = os.path.join(self.storagepath, "rangetest")
        os.makedirs(rangetest_dir, exist_ok=True)
        
        timestamp = int(time.time())
        peer_short = peer_hash[:8]
        
        # Esporta in GPX
        self._export_to_gpx(points, peer_short, timestamp, rangetest_dir)
        
        # Esporta in KML
        self._export_to_kml(points, peer_short, timestamp, rangetest_dir)
        
        print(f"📊 Dati rangetest esportati per {peer_short}")

    def _export_to_gpx(self, points, peer_short, timestamp, output_dir):
        """Esporta in formato GPX"""
        try:
            import xml.etree.ElementTree as ET
            from xml.dom import minidom
            
            gpx = ET.Element('gpx', version='1.1', creator='RNS Manager')
            trk = ET.SubElement(gpx, 'trk')
            name = ET.SubElement(trk, 'name')
            name.text = f"Rangetest {peer_short} {time.strftime('%Y-%m-%d %H:%M:%S')}"
            
            trkseg = ET.SubElement(trk, 'trkseg')
            
            for point in points:
                # 🔴 CORREZIONE: location è DENTRO point, NON in point['telemetry']!
                if point.get('location') and point['location'].get('latitude'):
                    loc = point['location']
                    trkpt = ET.SubElement(trkseg, 'trkpt', 
                                          lat=str(loc.get('latitude', 0)), 
                                          lon=str(loc.get('longitude', 0)))
                    
                    ele = ET.SubElement(trkpt, 'ele')
                    ele.text = str(loc.get('altitude', 0))
                    
                    time_elem = ET.SubElement(trkpt, 'time')
                    time_elem.text = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(point['timestamp']))
                    
                    # Aggiungi estensioni con dati radio e batteria
                    extensions = ET.SubElement(trkpt, 'extensions')
                    
                    if point.get('radio'):
                        radio = point['radio']
                        if radio.get('rssi') is not None:
                            rssi_elem = ET.SubElement(extensions, 'rssi')
                            rssi_elem.text = str(radio['rssi'])
                        if radio.get('snr') is not None:
                            snr_elem = ET.SubElement(extensions, 'snr')
                            snr_elem.text = str(radio['snr'])
                        if radio.get('q') is not None:
                            q_elem = ET.SubElement(extensions, 'quality')
                            q_elem.text = str(radio['q'])
                        if radio.get('hops') is not None:
                            hops_elem = ET.SubElement(extensions, 'hops')
                            hops_elem.text = str(radio['hops'])
                    
                    if point.get('battery'):
                        battery = point['battery']
                        if battery.get('charge_percent') is not None:
                            batt_elem = ET.SubElement(extensions, 'battery')
                            batt_elem.text = str(battery['charge_percent'])
            
            xml_str = minidom.parseString(ET.tostring(gpx)).toprettyxml(indent="  ")
            filename = os.path.join(output_dir, f"rangetest_{peer_short}_{timestamp}.gpx")
            with open(filename, 'w') as f:
                f.write(xml_str)
            print(f"💾 GPX salvato: {filename}")
            
        except Exception as e:
            print(f"❌ Errore esportazione GPX: {e}")
            traceback.print_exc()

    def _export_to_kml(self, points, peer_short, timestamp, output_dir):
        """Esporta in formato KML"""
        import xml.etree.ElementTree as ET
        from xml.dom import minidom
        
        kml = ET.Element('kml', xmlns='http://www.opengis.net/kml/2.2')
        document = ET.SubElement(kml, 'Document')
        
        name = ET.SubElement(document, 'name')
        name.text = f"Rangetest {peer_short} {time.strftime('%Y-%m-%d %H:%M:%S')}"
        
        # Crea una linea per il percorso
        placemark = ET.SubElement(document, 'Placemark')
        line_name = ET.SubElement(placemark, 'name')
        line_name.text = "Percorso"
        
        line_style = ET.SubElement(placemark, 'Style')
        line_style = ET.SubElement(line_style, 'LineStyle')
        line_color = ET.SubElement(line_style, 'color')
        line_color.text = 'ff0000ff'  # Rosso
        line_width = ET.SubElement(line_style, 'width')
        line_width.text = '2'
        
        linestring = ET.SubElement(placemark, 'LineString')
        tessellate = ET.SubElement(linestring, 'tessellate')
        tessellate.text = '1'
        coordinates = ET.SubElement(linestring, 'coordinates')
        
        coord_text = []
        for point in points:
            # 🔴 CORREZIONE: location è DENTRO point, NON in point['telemetry']!
            if point.get('location') and point['location'].get('latitude'):
                loc = point['location']
                coord_text.append(f"{loc.get('longitude', 0)},{loc.get('latitude', 0)},{loc.get('altitude', 0)}")
        
        coordinates.text = '\n'.join(coord_text)
        
        # Aggiungi punti come placemarks
        for i, point in enumerate(points):
            if point.get('location') and point['location'].get('latitude'):
                loc = point['location']
                pt = ET.SubElement(document, 'Placemark')
                pt_name = ET.SubElement(pt, 'name')
                pt_name.text = f"Punto {i+1}"
                
                # Aggiungi descrizione con dati radio
                description = ET.SubElement(pt, 'description')
                radio_text = []
                if point.get('radio'):
                    radio = point['radio']
                    if radio.get('rssi'):
                        radio_text.append(f"RSSI: {radio['rssi']} dBm")
                    if radio.get('snr'):
                        radio_text.append(f"SNR: {radio['snr']} dB")
                    if radio.get('q'):
                        radio_text.append(f"Q: {radio['q']}%")
                    if radio.get('hops'):
                        radio_text.append(f"Hops: {radio['hops']}")
                
                if point.get('battery'):
                    radio_text.append(f"Batteria: {point['battery'].get('charge_percent', 0)}%")
                
                description.text = "\n".join(radio_text) if radio_text else "Nessun dato radio"
                
                pt_point = ET.SubElement(pt, 'Point')
                pt_coords = ET.SubElement(pt_point, 'coordinates')
                pt_coords.text = f"{loc.get('longitude', 0)},{loc.get('latitude', 0)},{loc.get('altitude', 0)}"
        
        # Formatta e salva
        xml_str = minidom.parseString(ET.tostring(kml)).toprettyxml(indent="  ")
        filename = os.path.join(output_dir, f"rangetest_{peer_short}_{timestamp}.kml")
        with open(filename, 'w') as f:
            f.write(xml_str)
        print(f"💾 KML salvato: {filename}")

    def send(self, dest_hash, content, title="", callbacks=None,
                 image_path=None, audio_path=None,
                 file_attachments=None, telemetry=None, fields=None):
            try:
                dest_hash_bytes = bytes.fromhex(dest_hash)
                dest_identity = RNS.Identity.recall(dest_hash_bytes)
                if not dest_identity:
                    RNS.Transport.request_path(dest_hash_bytes)
                    time.sleep(2)
                    dest_identity = RNS.Identity.recall(dest_hash_bytes)
                    if not dest_identity:
                        return {'success': False, 'error': 'Destinatario sconosciuto'}

                dest = RNS.Destination(
                    dest_identity,
                    RNS.Destination.OUT,
                    RNS.Destination.SINGLE,
                    "lxmf",
                    "delivery"
                )
                source_dest = self.dest

                fields_dict = {}

                # 🔴 IMMAGINE - AGGIUNGI TITOLO DEFAULT SE NECESSARIO
                if image_path and not title and not content:
                    title = "🖼️ Image"
                    content = " "  # spazio vuoto per far visualizzare il messaggio

                # GESTIONE IMMAGINE - FORMATO ORIGINALE LXMF (SIDEBAND COMPATIBLE)
                if image_path:
                    with open(image_path, 'rb') as f:
                        img_bytes = f.read()
                    
                    # Rileva il tipo dall'estensione o dal magic bytes
                    ext = os.path.splitext(image_path)[1].lower()
                    
                    # Determina il tipo come STRINGA (formato originale Sideband)
                    if ext in ['.jpg', '.jpeg']:
                        image_type = 'jpg'
                    elif ext == '.png':
                        image_type = 'png'
                    elif ext == '.gif':
                        image_type = 'gif'
                    elif ext == '.webp':
                        image_type = 'webp'
                    else:
                        # Rileva dal magic bytes se l'estensione non è riconosciuta
                        if img_bytes.startswith(b'\xff\xd8'):
                            image_type = 'jpg'
                        elif img_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
                            image_type = 'png'
                        elif img_bytes.startswith(b'GIF87a') or img_bytes.startswith(b'GIF89a'):
                            image_type = 'gif'
                        elif img_bytes.startswith(b'RIFF') and img_bytes[8:12] == b'WEBP':
                            image_type = 'webp'
                        else:
                            image_type = 'img'  # fallback generico
                    
                    # 🔴 FORMATO ORIGINALE: [tipo_stringa, bytes] - SENZA CONVERSIONI!
                    fields_dict[FIELD_IMAGE] = [image_type, img_bytes]
                    print(f"📸 Immagine aggiunta: {image_type}, {len(img_bytes)} bytes (formato originale)")

                # 🔴 AUDIO - AGGIUNGI TITOLO DEFAULT SE NECESSARIO
                if audio_path and not title and not content:
                    title = "🎤 Audio Message"
                    content = " "  # spazio vuoto

                # 🎵 AUDIO - CONVERSIONE CON FFMPEG (garantito)
                if audio_path:
                    ext = os.path.splitext(audio_path)[1].lower()
                    
                    if ext == '.wav':
                        try:
                            print(f"🎵 Conversione WAV con ffmpeg...")
                            
                            # Crea file Opus temporaneo
                            temp_opus = audio_path + "_compressed.opus"
                            
                            # FFMPEG: converti WAV in Opus con parametri ottimali
                            cmd = [
                                'ffmpeg', '-i', audio_path,
                                '-c:a', 'libopus',           # Codec Opus
                                '-b:a', '16k',                # Bitrate 16kbps (voce)
                                '-application', 'voip',        # Ottimizzato per voce
                                '-frame_duration', '60',       # Frame 60ms come Sideband
                                '-packet_loss', '1',           # Tolleranza perdita pacchetti
                                '-ac', '1',                     # Mono
                                '-ar', '48000',                  # Sample rate 48kHz
                                '-y',                             # Sovrascrivi
                                temp_opus
                            ]
                            
                            result = subprocess.run(cmd, capture_output=True)
                            
                            if result.returncode == 0 and os.path.exists(temp_opus):
                                with open(temp_opus, 'rb') as f:
                                    audio_bytes = f.read()
                                os.unlink(temp_opus)
                                
                                fields_dict[FIELD_AUDIO] = [AM_OPUS_OGG, audio_bytes]
                                print(f"✅ Audio Opus compresso con ffmpeg: {len(audio_bytes)} bytes")
                                print(f"   Rapporto compressione: {os.path.getsize(audio_path)} → {len(audio_bytes)} bytes")
                            else:
                                print(f"❌ FFmpeg errore: {result.stderr.decode()}")
                                return {'success': False, 'error': 'FFmpeg compression failed'}
                                
                        except Exception as e:
                            print(f"❌ Conversione audio fallita: {e}")
                            traceback.print_exc()
                            return {'success': False, 'error': f'Conversione audio fallita: {e}'}

                # 🔴 FILE - AGGIUNGI TITOLO DEFAULT SE NECESSARIO
                if file_attachments and not title and not content:
                    if len(file_attachments) == 1:
                        filename = file_attachments[0][0]
                        title = f"📎 {filename}"
                    else:
                        title = f"📎 {len(file_attachments)} files"
                    content = " "  # spazio vuoto

                # 📎 FILE ATTACHMENTS
                if file_attachments:
                    attachments_list = []
                    for fname, fpath in file_attachments:
                        with open(fpath, 'rb') as f:
                            file_bytes = f.read()
                        attachments_list.append([fname, file_bytes])
                        print(f"📎 File aggiunto: {fname}, {len(file_bytes)} bytes")
                    
                    fields_dict[FIELD_FILE_ATTACHMENTS] = attachments_list

                # 🎨 ICON APPEARANCE
                appearance = self.config.get('appearance', ['📡', '4c9aff', '1a1f2e'])
                if appearance and len(appearance) == 3:
                    # Converti hex color in bytes
                    fg_bytes = bytes.fromhex(appearance[1].lstrip('#')) if isinstance(appearance[1], str) else appearance[1]
                    bg_bytes = bytes.fromhex(appearance[2].lstrip('#')) if isinstance(appearance[2], str) else appearance[2]
                    fields_dict[FIELD_ICON_APPEARANCE] = [appearance[0], fg_bytes, bg_bytes]

                # 📊 TELEMETRIA
                if telemetry:
                    fields_dict[FIELD_TELEMETRY] = telemetry.packed()
                
                # Altri fields
                if fields:
                    fields_dict.update(fields)

                # Crea il messaggio LXMF
                msg = LXMF.LXMessage(
                    dest,
                    source_dest,
                    content,
                    title,
                    fields=fields_dict if fields_dict else None,
                    desired_method=0x01  # opportunistic
                )

                msg.stamp_cost = self.config.get('stamp_cost', 0)
                if self.config.get('try_propagation_on_fail') and self.propagation_node:
                    msg.try_propagation_on_fail = True

                msg.pack()
                msg_hash = msg.hash.hex()

                # Salva raw
                self._save_raw(msg_hash, msg.packed)

                # Registra callbacks
                if callbacks:
                    if 'delivery' in callbacks:
                        self.delivery_callbacks[msg_hash] = callbacks['delivery']
                    if 'failed' in callbacks:
                        self.failed_callbacks[msg_hash] = callbacks['failed']
                    if 'progress' in callbacks:
                        self.progress_callbacks[msg_hash] = callbacks['progress']

                msg.register_delivery_callback(self._delivery_callback)
                msg.register_failed_callback(self._failed_callback)
                
                self.sent_messages[msg_hash] = {
                    'message': msg,
                    'last_progress': 0,
                    'callbacks': callbacks
                }

                self.router.handle_outbound(msg)

                # SALVA NEL DATABASE
                self._save_message(msg, incoming=False)

                return {
                    'success': True,
                    'hash': msg_hash,
                    'method': 'opportunistic',
                    'status': 'sending',
                    'packed_size': msg.packed_size
                }

            except Exception as e:
                traceback.print_exc()
                return {'success': False, 'error': str(e)}

    def send_command(self, dest_hash, command_type, command_data=None, callbacks=None):
        try:
            dest_hash_bytes = bytes.fromhex(dest_hash)
            dest_identity = RNS.Identity.recall(dest_hash_bytes)
            if not dest_identity:
                RNS.Transport.request_path(dest_hash_bytes)
                time.sleep(2)
                dest_identity = RNS.Identity.recall(dest_hash_bytes)
                if not dest_identity:
                    return {'success': False, 'error': 'Destinatario sconosciuto'}

            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            source_dest = self.dest

            fields = {FIELD_COMMANDS: [{command_type: command_data}]}
            desired_method = 0x01
            method_name = 'opportunistic'

            msg = LXMF.LXMessage(
                dest,
                source_dest,
                "",
                "Command",
                fields=fields,
                desired_method=desired_method
            )

            msg.stamp_cost = self.config.get('stamp_cost')
            if self.config.get('try_propagation_on_fail') and self.propagation_node:
                msg.try_propagation_on_fail = True

            msg.pack()
            msg_hash = msg.hash.hex()

            # 🔴 DEBUG: Visualizza il pacchetto comando in invio
            if msg.packed:
                debug_packet(msg.packed, "⬆️ COMANDO", f"Tipo: {command_type}")

            self._save_raw(msg_hash, msg.packed)

            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO sent_messages (hash, destination_hash, content, sent_at, status, method)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (msg_hash, dest_hash, "[Command]", time.time(), 'sending', method_name))
                conn.commit()
                conn.close()

            # 🔴 SALVA IL COMANDO COME MESSAGGIO
            self._save_message(msg, incoming=False)

            if callbacks:
                if 'delivery' in callbacks:
                    self.delivery_callbacks[msg_hash] = callbacks['delivery']
                if 'failed' in callbacks:
                    self.failed_callbacks[msg_hash] = callbacks['failed']
                if 'progress' in callbacks:
                    self.progress_callbacks[msg_hash] = callbacks['progress']

            msg.register_delivery_callback(self._delivery_callback)
            msg.register_failed_callback(self._failed_callback)
            
            # Salva per monitoraggio
            self.sent_messages[msg_hash] = {
                'message': msg,
                'last_progress': 0,
                'callbacks': callbacks
            }

            self.router.handle_outbound(msg)

            return {
                'success': True,
                'hash': msg_hash,
                'method': method_name,
                'status': 'sending',
                'packed_size': msg.packed_size
            }

        except Exception as e:
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

    def request_telemetry(self, dest_hash, timebase=None, is_collector_request=False, callbacks=None):
        try:
            dest_hash_bytes = bytes.fromhex(dest_hash)
            dest_identity = RNS.Identity.recall(dest_hash_bytes)
            if not dest_identity:
                RNS.Transport.request_path(dest_hash_bytes)
                return {'success': False, 'error': 'Destinatario sconosciuto, richiesto path'}

            dest = RNS.Destination(dest_identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
            source_dest = self.dest

            if timebase is None:
                timebase = self._get_last_telemetry_timestamp(dest_hash)

            commands = [{Commands.TELEMETRY_REQUEST: [timebase, is_collector_request]}]
            fields = {FIELD_COMMANDS: commands}

            desired_method = 0x01
            method_name = 'opportunistic'

            msg = LXMF.LXMessage(
                dest,
                source_dest,
                "",
                "Telemetry Request",
                fields=fields,
                desired_method=desired_method
            )

            msg.stamp_cost = self.config.get('stamp_cost')
            if self.config.get('try_propagation_on_fail') and self.propagation_node:
                msg.try_propagation_on_fail = True

            msg.pack()
            msg_hash = msg.hash.hex()

            # 🔴 DEBUG: Visualizza il pacchetto telemetria in invio
            if msg.packed:
                debug_packet(msg.packed, "⬆️ TELEMETRIA", f"Richiesta per {dest_hash[:16]}...")

            self._save_raw(msg_hash, msg.packed)

            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO sent_messages (hash, destination_hash, content, sent_at, status, method)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (msg_hash, dest_hash, "[Telemetry Request]", time.time(), 'sending', method_name))
                conn.commit()
                conn.close()

            # 🔴 SALVA LA RICHIESTA COME MESSAGGIO
            self._save_message(msg, incoming=False)

            if callbacks:
                if 'delivery' in callbacks:
                    self.delivery_callbacks[msg_hash] = callbacks['delivery']
                if 'failed' in callbacks:
                    self.failed_callbacks[msg_hash] = callbacks['failed']
                if 'progress' in callbacks:
                    self.progress_callbacks[msg_hash] = callbacks['progress']

            msg.register_delivery_callback(self._delivery_callback)
            msg.register_failed_callback(self._failed_callback)
            
            # Salva per monitoraggio
            self.sent_messages[msg_hash] = {
                'message': msg,
                'last_progress': 0,
                'callbacks': callbacks
            }

            self.router.handle_outbound(msg)

            return {
                'success': True,
                'hash': msg_hash,
                'method': method_name,
                'status': 'sending',
                'packed_size': msg.packed_size
            }

        except Exception as e:
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

    def _monitor_progress(self):
        """Monitora il progresso dei messaggi in invio"""
        while self.running:
            try:
                for msg_hash, data in list(self.sent_messages.items()):
                    msg = data['message']
                    last_progress = data['last_progress']
                    
                    # Se il progresso è cambiato
                    if msg.progress != last_progress and msg.progress > 0:
                        data['last_progress'] = msg.progress
                        
                        # Calcola velocità
                        speed = 0
                        bytes_sent = 0
                        total_bytes = 0
                        
                        if hasattr(msg, 'resource_representation') and msg.resource_representation:
                            resource = msg.resource_representation
                            if hasattr(resource, 'get_progress') and hasattr(resource, 'size'):
                                try:
                                    resource_progress = resource.get_progress()
                                    total_bytes = resource.size
                                    bytes_sent = int(resource_progress * total_bytes)
                                    
                                    # 🔴 CALCOLA VELOCITÀ
                                    now = time.time()
                                    if 'last_update' not in data:
                                        data['last_update'] = now
                                        data['last_bytes'] = bytes_sent
                                    else:
                                        time_diff = now - data['last_update']
                                        bytes_diff = bytes_sent - data['last_bytes']
                                        if time_diff > 0:
                                            speed = bytes_diff / time_diff  # bytes/sec
                                        data['last_update'] = now
                                        data['last_bytes'] = bytes_sent
                                        
                                except:
                                    pass
                        
                        if msg_hash in self.progress_callbacks:
                            progress_data = {
                                'hash': msg_hash,
                                'progress': msg.progress,
                                'state': msg.state,
                                'delivery_attempts': msg.delivery_attempts,
                                'status': 'transferring',
                                'speed': speed,
                                'bytes_sent': bytes_sent,
                                'total_bytes': total_bytes
                            }
                            
                            self.progress_callbacks[msg_hash](progress_data)
            except Exception as e:
                print(f"Errore nel monitoraggio progresso: {e}")
            
            time.sleep(0.5)

    def sync_messages(self, limit=10):
        if not self.propagation_node:
            return {'success': False, 'error': 'No propagation node in config'}
        self.router.request_messages_from_propagation_node(self.identity, max_messages=limit)
        timeout = 30
        while timeout > 0 and self.router.propagation_transfer_state not in [
            LXMF.LXMRouter.PR_IDLE,
            LXMF.LXMRouter.PR_COMPLETE,
            LXMF.LXMRouter.PR_FAILED
        ]:
            time.sleep(0.5)
            timeout -= 0.5
        return {
            'success': True,
            'new': self.router.propagation_transfer_last_result,
            'state': self.router.propagation_transfer_state
        }

    def announce(self):
        self.router.announce(self.dest.hash)
        print(f"📢 Annuncio inviato con nome: {self.display_name}")
        return self.identity.hash.hex()

    def get_hash(self):
        return self.identity.hash.hex()

    def get_delivery_hash(self):
        return self.dest.hash.hex()

    def get_peer_name(self, dest_hash):
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            c.execute('''
                SELECT i.display_name FROM identities i
                JOIN destinations d ON i.identity_hash = d.identity_hash
                WHERE d.destination_hash = ?
            ''', (dest_hash,))
            row = c.fetchone()
            conn.close()
            if row:
                return row[0]
            return dest_hash[:8]

    def list_peers(self):
        """Restituisce una lista di tutte le identità (con nome, identity_hash e appearance)"""
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            
            # 🔴 UNA SOLA QUERY CHE PRENDE TUTTO!
            c.execute('''
                SELECT i.identity_hash, i.display_name, i.group_name, d.appearance
                FROM identities i
                LEFT JOIN destinations d ON i.identity_hash = d.identity_hash 
                    AND d.aspect = 'lxmf.delivery'
                GROUP BY i.identity_hash
            ''')
            rows = c.fetchall()
            conn.close()
        
        # Ordina
        def sort_key(row):
            name = row[1] or ""
            clean = re.sub(r'^[^a-zA-Z0-9]+', '', name)
            return clean.lower()
        
        rows.sort(key=sort_key)
        
        # Costruisci risultato con appearance
        result = []
        for row in rows:
            peer = {
                'hash': row[0],
                'name': row[1],
                'group': row[2]
            }
            
            # 🔴 DECODIFICA APPEARANCE SE PRESENTE
            if row[3]:
                try:
                    app_data = json.loads(row[3])
                    peer['appearance'] = [
                        app_data.get('icon', 'account-outline'),
                        app_data.get('fg', '4c9aff'),
                        app_data.get('bg', '1a1f2e')
                    ]
                except:
                    peer['appearance'] = None
            else:
                peer['appearance'] = None
            
            result.append(peer)
        
        return result

    def add_peer_manual(self, identity_hash, name, group=None):
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            now = time.time()
            c.execute('''
                INSERT INTO identities (identity_hash, display_name, first_seen, last_seen, group_name)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(identity_hash) DO UPDATE SET
                    display_name = excluded.display_name,
                    last_seen = excluded.last_seen,
                    group_name = excluded.group_name
            ''', (identity_hash, name, now, now, group))
            conn.commit()
            conn.close()
        print(f"✅ Identità {name} aggiunta con hash {identity_hash}")

    def import_peers_from_cache(self):
        if not os.path.exists(self.announces_cache_db):
            print("📢 Database announces.db non trovato.")
            return 0

        try:
            with self.db_lock:
                src_conn = sqlite3.connect(self.announces_cache_db, timeout=10)
                src_c = src_conn.cursor()
                src_c.execute("SELECT COUNT(*) FROM announces WHERE aspect = 'lxmf.delivery'")
                total = src_c.fetchone()[0]
                print(f"📢 Trovati {total} annunci lxmf.delivery in announces.db")

                src_c.execute('''
                    SELECT a.dest_hash, a.timestamp, a.hops, a.data, a.identity_hash,
                           a.rssi, a.snr, a.q
                    FROM announces a
                    INNER JOIN (
                        SELECT dest_hash, MAX(timestamp) as max_ts
                        FROM announces
                        WHERE aspect = 'lxmf.delivery'
                        GROUP BY dest_hash
                    ) b ON a.dest_hash = b.dest_hash AND a.timestamp = b.max_ts
                    WHERE a.aspect = 'lxmf.delivery'
                ''')
                rows = src_c.fetchall()
                src_conn.close()
                print(f"📢 Selezionati {len(rows)} peer unici da announces.db")

                if not rows:
                    print("📢 Nessun peer da importare.")
                    return 0

                dst_conn = sqlite3.connect(self.peers_db, timeout=10)
                dst_c = dst_conn.cursor()
                imported = 0
                for row in rows:
                    dest_hash, ts, hops, data_hex, identity_hash, rssi, snr, q = row
                    display_name = None
                    if data_hex and len(data_hex) > 0:
                        try:
                            if all(c in '0123456789abcdefABCDEF' for c in data_hex) and len(data_hex) % 2 == 0:
                                app_data = bytes.fromhex(data_hex)
                                display_name = LXMF.display_name_from_app_data(app_data)
                            else:
                                app_data = data_hex.encode('utf-8')
                                display_name = LXMF.display_name_from_app_data(app_data)
                        except Exception as e:
                            RNS.log(f"Errore decoding app_data per {dest_hash}: {e}", RNS.LOG_WARNING)
                    if not display_name:
                        display_name = dest_hash[:8]

                    if identity_hash:
                        dst_c.execute('''
                            INSERT INTO identities (identity_hash, display_name, first_seen, last_seen)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(identity_hash) DO UPDATE SET
                                display_name = excluded.display_name,
                                last_seen = excluded.last_seen
                        ''', (identity_hash, display_name, ts, ts))

                    dst_c.execute('''
                        INSERT INTO destinations (destination_hash, identity_hash, aspect, last_seen, hops, rssi, snr, q, app_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(destination_hash) DO UPDATE SET
                            identity_hash = excluded.identity_hash,
                            last_seen = excluded.last_seen,
                            hops = excluded.hops,
                            rssi = excluded.rssi,
                            snr = excluded.snr,
                            q = excluded.q,
                            app_data = excluded.app_data
                    ''', (dest_hash, identity_hash, 'lxmf.delivery', ts, hops, rssi, snr, q, data_hex))
                    imported += 1

                dst_conn.commit()
                dst_conn.close()
                print(f"📢 Importati {imported} peer da announces.db.")
                return imported

        except Exception as e:
            print(f"❌ Errore durante l'importazione: {e}")
            traceback.print_exc()
            return 0

    def _delivery_callback(self, message):
        if message.hash:
            msg_hash = message.hash.hex()
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    UPDATE sent_messages SET status = ?, delivered_at = ? WHERE hash = ?
                ''', ('delivered', time.time(), msg_hash))
                conn.commit()
                conn.close()
            RNS.log(f"✅ Delivery callback per {msg_hash}", RNS.LOG_INFO)
            if msg_hash in self.delivery_callbacks:
                self.delivery_callbacks[msg_hash]({
                    'hash': msg_hash,
                    'status': 'delivered',
                    'time': time.time()
                })
                del self.delivery_callbacks[msg_hash]
            
            # Rimuovi dal monitoraggio
            if msg_hash in self.sent_messages:
                del self.sent_messages[msg_hash]

    def _failed_callback(self, message):
        if hasattr(message, 'try_propagation_on_fail') and message.try_propagation_on_fail:
            message.desired_method = 0x03
            message.try_propagation_on_fail = False
            message.delivery_attempts = 0
            message.packed = None
            message.state = LXMF.LXMessage.GENERATING
            self.router.handle_outbound(message)
            return

        if message.hash:
            msg_hash = message.hash.hex()
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    UPDATE sent_messages SET status = ?, failed_at = ?, attempts = ? WHERE hash = ?
                ''', ('failed', time.time(), message.delivery_attempts, msg_hash))
                conn.commit()
                conn.close()
            RNS.log(f"❌ Failed callback per {msg_hash}", RNS.LOG_INFO)
            if msg_hash in self.failed_callbacks:
                self.failed_callbacks[msg_hash]({
                    'hash': msg_hash,
                    'status': 'failed',
                    'time': time.time(),
                    'attempts': message.delivery_attempts
                })
                del self.failed_callbacks[msg_hash]
            
            # Rimuovi dal monitoraggio
            if msg_hash in self.sent_messages:
                del self.sent_messages[msg_hash]

class AnnounceHandler:
    def __init__(self, messenger):
        self.messenger = messenger
        self.aspect_filter = "lxmf.delivery"

    def received_announce(self, destination_hash, announced_identity, app_data):
        peer_hash = destination_hash.hex()
        RNS.log(f"📢 Annuncio ricevuto da {peer_hash}", RNS.LOG_INFO)
        display_name = None
        if app_data:
            try:
                display_name = LXMF.display_name_from_app_data(app_data)
                RNS.log(f"   Nome estratto: {display_name}", RNS.LOG_INFO)
            except Exception as e:
                RNS.log(f"   Errore estrazione nome: {e}", RNS.LOG_WARNING)
        if not display_name:
            display_name = peer_hash[:8]
            RNS.log(f"   Nome di default: {display_name}", RNS.LOG_INFO)

        hops = RNS.Transport.hops_to(destination_hash)
        identity_hash = announced_identity.hash.hex() if announced_identity else None

        with self.messenger.db_lock:
            conn = sqlite3.connect(self.messenger.peers_db, timeout=10)
            c = conn.cursor()
            now = time.time()

            if identity_hash:
                c.execute('''
                    INSERT INTO identities (identity_hash, display_name, first_seen, last_seen)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(identity_hash) DO UPDATE SET
                        display_name = excluded.display_name,
                        last_seen = excluded.last_seen
                ''', (identity_hash, display_name, now, now))

            c.execute('''
                INSERT INTO destinations (destination_hash, identity_hash, aspect, last_seen, hops, app_data)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(destination_hash) DO UPDATE SET
                    identity_hash = excluded.identity_hash,
                    last_seen = excluded.last_seen,
                    hops = excluded.hops,
                    app_data = excluded.app_data
            ''', (peer_hash, identity_hash, 'lxmf.delivery', now, hops, app_data.hex() if app_data else None))

            conn.commit()
            conn.close()

