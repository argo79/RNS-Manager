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
from core.aspects import (
    ASPECT_LABELS,
    DEFAULT_ANNOUNCE_ASPECTS,
    DISCOVERY_ASPECT,
    is_discovery_aspect,
    is_map_capable_aspect,
    normalize_announce_aspects,
    parse_interface_discovery_app_data,
)
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

# ============================================
# GRUPPI DISPONIBILI (10 stati)
# ============================================
PEER_GROUPS = {
    'favorite': {'icon': '⭐', 'name': 'Preferiti', 'trust': 3, 'color': '#FFD700'},
    'home': {'icon': '🏠', 'name': 'Casa', 'trust': 3, 'color': '#4CAF50'},
    'work': {'icon': '💼', 'name': 'Lavoro', 'trust': 2, 'color': '#2196F3'},
    'family': {'icon': '👨‍👩‍👧', 'name': 'Famiglia', 'trust': 3, 'color': '#E91E63'},
    'friends': {'icon': '👥', 'name': 'Amici', 'trust': 2, 'color': '#9C27B0'},
    'server': {'icon': '🖥️', 'name': 'Server', 'trust': 1, 'color': '#607D8B'},
    'bot': {'icon': '🤖', 'name': 'Bot', 'trust': 1, 'color': '#795548'},
    'monitor': {'icon': '📡', 'name': 'Monitoraggio', 'trust': 2, 'color': '#FF9800'},
    'research': {'icon': '🔬', 'name': 'Ricerca', 'trust': 2, 'color': '#009688'},
    'public': {'icon': '🌐', 'name': 'Pubblico', 'trust': 0, 'color': '#9E9E9E'}
}

CODEC2_AVAILABLE = audio_codec.detect_codec2()
DEBUG_PACKETS = os.environ.get("RNS_DEBUG_PACKETS", "0") == "1"
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

ICON_RE = re.compile(r'^[A-Za-z0-9-]{1,64}$')
COLOR_RE = re.compile(r'^[0-9a-fA-F]{6}$')


def sanitize_appearance(icon, fg, bg):
    """Normalizza icona e colori provenienti da peer remoti."""
    icon = str(icon or 'account-outline')
    if not ICON_RE.fullmatch(icon):
        icon = 'account-outline'

    def _color(value, fallback):
        value = str(value or '').lstrip('#')
        return value.lower() if COLOR_RE.fullmatch(value) else fallback

    return [icon.lower(), _color(fg, '4c9aff'), _color(bg, '1a1f2e')]


def sanitize_display_name(value, fallback=None):
    text = ''.join(ch for ch in str(value or '') if ch >= ' ' and ch != '')
    text = ' '.join(text.split())[:96]
    return text or fallback


def is_hash_preview_name(name, identity_hash=None):
    """Riconosce nomi che sono in realtà un prefisso dell'hash identità."""
    text = str(name or '').strip().lower()
    if not text or not re.fullmatch(r'[0-9a-f]+', text) or len(text) < 8:
        return False
    if not identity_hash:
        return False
    if isinstance(identity_hash, (bytes, bytearray)):
        identity_hash = bytes(identity_hash).hex()
    return str(identity_hash).lower().startswith(text)


def clean_display_name(value, fallback=None):
    """Pulisce nomi legacy con prefisso di lunghezza e byte di controllo."""
    if value is None:
        return fallback

    if isinstance(value, (bytes, bytearray)):
        try:
            text = bytes(value).decode('utf-8')
        except UnicodeDecodeError:
            text = bytes(value).decode('latin-1', errors='replace')
    else:
        text = str(value)

    # I vecchi annunci potevano salvare: <lunghezza><nome><metadati>.
    if text:
        length = ord(text[0])
        if 0 < length < 0x20 and length <= len(text) - 1:
            candidate = sanitize_display_name(text[1:1 + length], None)
            if candidate:
                return candidate[:96]

    return sanitize_display_name(text, fallback)


class Messenger:
    def __init__(self, storagepath="~/.rns_manager/storage", configpath="~/.rns_manager/config.json", identity_path=None, announce_aspects=None, display_name=None):
        self.storagepath = os.path.expanduser(storagepath)
        self.configpath = os.path.expanduser(configpath)
        self.telemetry_dir = os.path.join(self.storagepath, "telemetry")
        self.attachments_dir = os.path.join(self.storagepath, "attachments")
        self.conversations_dir = os.path.join(self.storagepath, "conversations")
        self.raw_dir = os.path.join(self.storagepath, "raw")
        self.peers_db = os.path.join(self.storagepath, "peers.db")
        self.announces_cache_db = os.path.expanduser("~/.rns_manager/cache/announces.db")
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
            self.identity_path = identity_path
            self.identity = RNS.Identity.from_file(identity_path)
            print(f"✅ Identità caricata da file: {identity_path}")
        else:
            self.identity = self._choose_identity()
        
        explicit_name = clean_display_name(display_name, None)
        if explicit_name and is_hash_preview_name(explicit_name, self.identity.hash.hex()):
            explicit_name = None
        self.display_name = explicit_name or self._load_display_name()
        if explicit_name:
            self._save_display_name(explicit_name)
        self.lxmf_storage = os.path.dirname(self.storagepath)
        os.makedirs(self.lxmf_storage, exist_ok=True)

        self.router = LXMF.LXMRouter(identity=self.identity, storagepath=self.lxmf_storage)
        self.dest = self.router.register_delivery_identity(
            self.identity,
            display_name=self.display_name,
            stamp_cost=self.config.get('stamp_cost')
        )

        self.message_callback = None
        self.announce_callback = None
        configured_aspects = announce_aspects
        if not configured_aspects:
            configured_aspects = self.config.get('announce_aspects')
        self.announce_aspects = normalize_announce_aspects(
            configured_aspects or DEFAULT_ANNOUNCE_ASPECTS,
            ensure_delivery=True,
        )
        self.announce_aspects_lock = threading.Lock()
        self.announce_handlers = []
        self.announce_stats = {
            'total': 0,
            'by_aspect': {},
            'last': None,
            'recent': [],
        }
        self.announce_stats_lock = threading.Lock()
        self.telemetry_stats = {
            'requests_sent': 0,
            'responses_received': 0,
            'responses_sent': 0,
            'requests_ignored': 0,
            'last_request': None,
            'last_response': None,
            'last_ignored': None,
        }
        self.telemetry_stats_lock = threading.Lock()
        self.delivery_callbacks = {}
        self.failed_callbacks = {}
        self.progress_callbacks = {}
        self.sent_messages = {}

        self.router.register_delivery_callback(self._on_message)
        self.propagation_node = None
        self._set_propagation_node()
        self._init_peers_db()

        self._register_announce_handlers()
        # Alias retrocompatibile con il vecchio handler singolo.
        self.announce_handler = self.announce_handlers[0] if self.announce_handlers else None
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

        # Retention dati (startup + ogni 24h)
        self._retention_stop = threading.Event()
        self.retention_thread = threading.Thread(target=self._retention_loop, daemon=True)
        self.retention_thread.start()

    def _request_announce_destination_types(self, aspects=None):
        """Ask a shared Reticulum instance to forward these destination types.

        LXMRouter only requests ``lxmf.delivery`` and ``lxmf.propagation``.
        Interface discovery (and any other aspect added by the user) must be
        requested explicitly when Messenger is attached to a shared instance.
        """
        for aspect in (aspects or self.announce_aspects):
            try:
                reticulum = RNS.Reticulum.get_instance()
                wants = getattr(reticulum, 'wants_destination_type', None)
                if wants:
                    wants(aspect)
            except Exception as e:
                RNS.log(f"Impossibile richiedere l'aspect {aspect}: {e}", RNS.LOG_DEBUG)

    @staticmethod
    def _deregister_announce_handler(handler):
        deregister = getattr(RNS.Transport, 'deregister_announce_handler', None)
        if deregister is None:
            # Compatibilità con vecchie versioni RNS.
            deregister = getattr(RNS.Transport, 'unregister_announce_handler', None)
        if deregister:
            deregister(handler)

    def _register_announce_handlers(self, aspects=None):
        aspects = normalize_announce_aspects(
            aspects if aspects is not None else self.announce_aspects,
            ensure_delivery=True,
        )
        handlers = []
        for aspect in aspects:
            handler = AnnounceHandler(self, aspect)
            try:
                RNS.Transport.register_announce_handler(handler)
                handlers.append(handler)
            except Exception as e:
                RNS.log(f"Errore registrazione handler {aspect}: {e}", RNS.LOG_WARNING)
        self.announce_aspects = aspects
        self.announce_handlers = handlers
        self._request_announce_destination_types(aspects)
        return handlers

    def set_announce_aspects(self, aspects):
        """Aggiorna gli aspect a runtime senza riavviare il processo."""
        aspects = normalize_announce_aspects(aspects, ensure_delivery=True)
        lock = getattr(self, 'announce_aspects_lock', None)
        if lock is None:
            self.announce_aspects_lock = threading.Lock()
            lock = self.announce_aspects_lock
        with lock:
            if aspects == self.announce_aspects:
                return list(self.announce_aspects)
            old_handlers = list(getattr(self, 'announce_handlers', []))
            for handler in old_handlers:
                try:
                    self._deregister_announce_handler(handler)
                except Exception:
                    pass
            handlers = []
            for aspect in aspects:
                handler = AnnounceHandler(self, aspect)
                try:
                    RNS.Transport.register_announce_handler(handler)
                    handlers.append(handler)
                except Exception as e:
                    RNS.log(f"Errore registrazione handler {aspect}: {e}", RNS.LOG_WARNING)
            self.announce_aspects = aspects
            self.announce_handlers = handlers
            self.announce_handler = handlers[0] if handlers else None
            self._request_announce_destination_types(aspects)
        return list(self.announce_aspects)

    def _retention_days(self, key, default):
        try:
            return max(0, int(self.config.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _purge_dir(self, directory, cutoff):
        """Rimuove i file più vecchi di cutoff; ritorna il numero rimosso."""
        if not directory or not os.path.isdir(directory):
            return 0
        removed = 0
        for root, dirs, files in os.walk(directory, topdown=False):
            for name in files:
                path = os.path.join(root, name)
                try:
                    if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                        os.unlink(path)
                        removed += 1
                except OSError:
                    pass
            for name in dirs:
                path = os.path.join(root, name)
                try:
                    if not os.listdir(path):
                        os.rmdir(path)
                except OSError:
                    pass
        return removed

    def cleanup_old_data(self):
        """Applica la retention a DB, raw, telemetria, conversazioni e rangetest."""
        results = {}
        now = time.time()

        message_days = self._retention_days('message_retention_days', 90)
        if message_days > 0:
            cutoff = now - message_days * 86400
            try:
                with self.db_lock:
                    conn = sqlite3.connect(self.peers_db, timeout=10)
                    c = conn.cursor()
                    c.execute('DELETE FROM messages WHERE timestamp < ?', (cutoff,))
                    results['messages'] = c.rowcount
                    c.execute('DELETE FROM sent_messages WHERE sent_at < ?', (cutoff,))
                    results['sent_messages'] = c.rowcount
                    conn.commit()
                    conn.close()
            except Exception as e:
                print(f"⚠️ Retention DB fallita: {e}")

        for key, default, attr in (
            ('raw_retention_days', 30, 'raw_dir'),
            ('telemetry_retention_days', 90, 'telemetry_dir'),
            ('rangetest_retention_days', 90, 'rangetest_dir'),
        ):
            days = self._retention_days(key, default)
            if days > 0:
                results[key] = self._purge_dir(getattr(self, attr, None), now - days * 86400)

        if message_days > 0:
            cutoff = now - message_days * 86400
            results['conversations'] = self._purge_dir(getattr(self, 'conversations_dir', None), cutoff)
            results['attachments'] = self._purge_dir(getattr(self, 'attachments_dir', None), cutoff)

        if any(results.values()):
            print(f"🧹 Retention: {results}")
        return results

    def _retention_loop(self):
        while not self._retention_stop.is_set():
            try:
                self.cleanup_old_data()
            except Exception as e:
                print(f"⚠️ Errore retention: {e}")
            self._retention_stop.wait(86400)

    def cleanup(self):
        """Pulisce le risorse Reticulum prima di distruggere l'istanza"""
        self.running = False
        if hasattr(self, '_retention_stop'):
            self._retention_stop.set()
        try:
            # Aspetta che il thread di progresso finisca
            if hasattr(self, 'progress_thread') and self.progress_thread.is_alive():
                self.progress_thread.join(timeout=1.0)

            # Ferma il thread GPSD (se attivo)
            provider = getattr(self, 'telemetry_provider', None)
            location = getattr(provider, 'location', None) if provider else None
            if location:
                try:
                    location.stop()
                except Exception:
                    pass
            
            # Deregistra la destinazione principale da RNS.Transport.
            # LXMRouter.delivery_destinations è un dict, non una lista.
            dest = getattr(self, 'dest', None)
            router = getattr(self, 'router', None)
            if dest:
                try:
                    dest_hash = getattr(dest, 'hash', None)
                    if router is not None and isinstance(getattr(router, 'delivery_destinations', None), dict) and dest_hash is not None:
                        router.delivery_destinations.pop(dest_hash, None)
                    deregister = getattr(RNS.Transport, 'deregister_destination', None)
                    if deregister:
                        deregister(dest)
                    print("   ✅ Destinazione deregistrata da RNS")
                except Exception as e:
                    print(f"   ⚠️ Errore deregistrazione destinazione: {e}")

            # Rimuovi tutti gli announce handler registrati
            handlers = list(getattr(self, 'announce_handlers', []))
            legacy = getattr(self, 'announce_handler', None)
            if legacy and legacy not in handlers:
                handlers.append(legacy)
            for handler in handlers:
                try:
                    self._deregister_announce_handler(handler)
                except Exception:
                    pass
            self.announce_handlers = []

            # Ferma il router
            if router is not None:
                try:
                    router.delivery_callbacks = []
                except Exception:
                    pass
                self.router = None

            # Pulisci riferimenti
            self.dest = None
            self.local_dest = None
            self.announce_handler = None
            print("🧹 Messenger pulito correttamente")
            
        except Exception as e:
            print(f"⚠️ Errore durante cleanup: {e}")

    def _init_peers_db(self):
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            
            # ============================================
            # TABELLA IDENTITÀ (solo anagrafica)
            # ============================================
            c.execute('''
                CREATE TABLE IF NOT EXISTS identities (
                    identity_hash TEXT PRIMARY KEY,
                    display_name TEXT,
                    first_seen REAL,
                    last_seen REAL,
                    notes TEXT
                )
            ''')
            
            # ============================================
            # TABELLA DESTINAZIONI (QUELLE CHE USIAMO DAVVERO!)
            # ============================================
            c.execute('''
                CREATE TABLE IF NOT EXISTS destinations (
                    destination_hash TEXT PRIMARY KEY,
                    identity_hash TEXT,
                    aspect TEXT,
                    
                    -- Dati anagrafici
                    display_name TEXT,
                    group_name TEXT,
                    notes TEXT,
                    
                    -- Dati ultimo annuncio
                    last_seen REAL,
                    hops INTEGER,
                    rssi REAL,
                    snr REAL,
                    q REAL,
                    app_data TEXT,
                    appearance TEXT,
                    
                    -- 🔴 ULTIMA TELEMETRIA COMPLETA
                    last_latitude REAL,
                    last_longitude REAL,
                    last_altitude REAL,
                    last_speed REAL,
                    last_bearing REAL,
                    last_accuracy REAL,
                    last_battery REAL,
                    last_battery_charging INTEGER,
                    last_temperature REAL,
                    last_humidity REAL,
                    last_pressure REAL,
                    last_light REAL,
                    last_uptime REAL,
                    last_processor REAL,
                    last_ram REAL,
                    last_nvm REAL,
                    last_telemetry_time REAL,
                    
                    -- 🔴 PERMESSI E STATO
                    trust_level INTEGER DEFAULT 0,
                    allow_telemetry INTEGER DEFAULT 0,
                    allow_commands INTEGER DEFAULT 0,
                    last_telemetry_request REAL,
                    last_command_executed REAL,
                    last_command_result TEXT,
                    
                    -- 🔴 STATISTICHE
                    messages_sent INTEGER DEFAULT 0,
                    messages_received INTEGER DEFAULT 0,
                    telemetry_count INTEGER DEFAULT 0,
                    last_message_time REAL,
                    
                    -- 🔴 PREFERITI (sovrascrive gruppo)
                    is_favorite INTEGER DEFAULT 0,
                    
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
            
            # 🔴 TABELLA MESSAGGI PER TUTTI I MESSAGGI (ricevuti e inviati)
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
            
            # ============================================
            # TABELLA GRUPPI (opzionale)
            # ============================================
            c.execute('''
                CREATE TABLE IF NOT EXISTS groups (
                    group_name TEXT PRIMARY KEY,
                    description TEXT,
                    trust_level INTEGER DEFAULT 0,
                    created_at REAL
                )
            ''')

            c.execute('''
                CREATE TABLE IF NOT EXISTS app_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # ============================================
            # 🔴 NUOVA TABELLA PER I GRUPPI MULTIPLI (N:N)
            # ============================================
            c.execute('''
                CREATE TABLE IF NOT EXISTS peer_groups (
                    identity_hash TEXT,
                    group_name TEXT,
                    added_at REAL,
                    PRIMARY KEY (identity_hash, group_name),
                    FOREIGN KEY(identity_hash) REFERENCES identities(identity_hash) ON DELETE CASCADE,
                    FOREIGN KEY(group_name) REFERENCES groups(group_name) ON DELETE CASCADE
                )
            ''')
            
            # Migrazione: preferiti e gruppo anche sulla tabella identities
            # (così un peer è configurabile anche prima di avere una destination)
            c.execute("PRAGMA table_info(identities)")
            ident_cols = {row[1] for row in c.fetchall()}
            prefs_added = False
            if 'is_favorite' not in ident_cols:
                c.execute("ALTER TABLE identities ADD COLUMN is_favorite INTEGER DEFAULT 0")
                prefs_added = True
            if 'group_name' not in ident_cols:
                c.execute("ALTER TABLE identities ADD COLUMN group_name TEXT")
                prefs_added = True
            if prefs_added:
                c.execute('''
                    UPDATE identities SET
                        is_favorite = COALESCE((
                            SELECT MAX(d.is_favorite) FROM destinations d
                            WHERE d.identity_hash = identities.identity_hash
                        ), 0),
                        group_name = COALESCE((
                            SELECT d.group_name FROM destinations d
                            WHERE d.identity_hash = identities.identity_hash
                              AND d.group_name IS NOT NULL
                              AND d.group_name != 'public'
                            LIMIT 1
                        ), NULL)
                ''')

            # Importa i preferiti legacy solo durante la prima migrazione,
            # poi svuota la vecchia tabella per non ripristinare scelte future.
            if prefs_added:
                try:
                    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='favorites'")
                    if c.fetchone():
                        c.execute('''
                            UPDATE identities SET is_favorite = 1
                            WHERE identity_hash IN (SELECT identity_hash FROM favorites)
                        ''')
                        c.execute("DELETE FROM favorites")
                except Exception:
                    pass

            # Reset una tantum dei gruppi: nessun contatto deve partire in "public".
            c.execute("SELECT value FROM app_meta WHERE key = 'groups_reset_v2'")
            if not c.fetchone():
                c.execute('UPDATE identities SET group_name = NULL')
                c.execute('UPDATE destinations SET group_name = NULL')
                c.execute('DELETE FROM peer_groups')
                c.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('groups_reset_v2', '1')")

            # Pulizia una tantum dei nomi legacy (prefissi di lunghezza/byte di controllo).
            c.execute("SELECT value FROM app_meta WHERE key = 'display_names_v1'")
            if not c.fetchone():
                c.execute('SELECT identity_hash, display_name FROM identities')
                cleaned_names = 0
                for identity_hash, raw_name in c.fetchall():
                    cleaned = clean_display_name(raw_name, None)
                    if cleaned and is_hash_preview_name(cleaned, identity_hash):
                        cleaned = None
                    if cleaned != raw_name:
                        c.execute(
                            'UPDATE identities SET display_name = ? WHERE identity_hash = ?',
                            (cleaned, identity_hash),
                        )
                        cleaned_names += 1
                c.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('display_names_v1', '1')")
                if cleaned_names:
                    print(f"🧹 Nomi peer normalizzati: {cleaned_names}")

            # ============================================
            # INDICI PER VELOCITÀ
            # ============================================
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_identity ON destinations(identity_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_group ON destinations(group_name)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_favorite ON destinations(is_favorite)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_last_seen ON destinations(last_seen)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_trust ON destinations(trust_level)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_telemetry ON destinations(last_telemetry_time)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_sent_status ON sent_messages(status)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_sent_dest ON sent_messages(destination_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_messages_dest ON messages(destination_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_messages_source ON messages(source_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_messages_time ON messages(timestamp)')
            
            # 🔴 NUOVI INDICI
            c.execute('CREATE INDEX IF NOT EXISTS idx_peer_groups_identity ON peer_groups(identity_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_peer_groups_group ON peer_groups(group_name)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_identities_favorite ON identities(is_favorite)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_identities_group ON identities(group_name)')
            
            conn.commit()
            conn.close()

    def _load_config(self):
        default = {
            "propagation_node": None,
            "delivery_mode": "opportunistic",
            "preferred_audio": "c2_3200",
            "announce_aspects": list(DEFAULT_ANNOUNCE_ASPECTS),
            "stamp_cost": 0,
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
            "appearance": ["antenna", "4c9aff", "1a1f2e"],
            # Retention dati (0 = disabilitata)
            "message_retention_days": 90,
            "raw_retention_days": 30,
            "telemetry_retention_days": 90,
            "rangetest_retention_days": 90,
            # Sicurezza: comandi/telemetria remoti disabilitati di default
            "allow_remote_commands": False,
            "allow_telemetry_requests": False,
        }
        if os.path.exists(self.configpath):
            try:
                with open(self.configpath, 'r') as f:
                    config = json.load(f)
                if isinstance(config, dict):
                    default.update(config)
                print(f"📄 Config caricato: propagation_node={config.get('propagation_node', 'None')}")
            except Exception as e:
                print(f"⚠️ Config non leggibile, uso i default: {e}")
        else:
            os.makedirs(os.path.dirname(self.configpath), exist_ok=True)
            with open(self.configpath, 'w') as f:
                json.dump(default, f, indent=4)
            print("📄 Creato config di default con opzioni telemetria")
        default['announce_aspects'] = normalize_announce_aspects(
            default.get('announce_aspects'), ensure_delivery=True
        )
        return default

    def _audio_preference(self):
        """Restituisce (mode Codec2, nome preferenza) dalla config."""
        pref = str(self.config.get('preferred_audio', 'c2_3200')).lower()
        mapping = {
            'c2_450': AM_CODEC2_450PWB,
            'c2_700': AM_CODEC2_700C,
            'c2_1200': AM_CODEC2_1200,
            'c2_1300': AM_CODEC2_1300,
            'c2_1400': AM_CODEC2_1400,
            'c2_1600': AM_CODEC2_1600,
            'c2_2400': AM_CODEC2_2400,
            'c2_3200': AM_CODEC2_3200,
        }
        return mapping.get(pref), pref

    def _encode_wav_to_codec2(self, audio_path, mode):
        """Converte un WAV PCM in Codec2 (8 kHz mono). Ritorna None se non possibile."""
        if not CODEC2_AVAILABLE:
            return None
        with wave.open(audio_path, 'rb') as wav_file:
            channels = wav_file.getnchannels()
            rate = wav_file.getframerate()
            width = wav_file.getsampwidth()
            frames = wav_file.readframes(wav_file.getnframes())
        if width != 2:
            return None
        samples = np.frombuffer(frames, dtype=np.int16)
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
        if rate != 8000:
            import audio_codec
            resampled = audio_codec.resample(samples.tobytes(), 2, 1, rate, 8000, True)
            samples = np.frombuffer(resampled, dtype=np.int16)
        import audio_codec
        return audio_codec.encode_codec2(samples, mode)

    def _desired_method(self):
        """Mappa delivery_mode della config sui valori LXMF."""
        mode = str(self.config.get('delivery_mode', 'opportunistic')).lower()
        return {
            'direct': LXMF.LXMessage.DIRECT,
            'opportunistic': LXMF.LXMessage.OPPORTUNISTIC,
            'propagated': LXMF.LXMessage.PROPAGATED,
        }.get(mode, LXMF.LXMessage.OPPORTUNISTIC)

    def _peer_permissions(self, destination_hash):
        # Permessi del peer (default: tutto negato).
        deny = {'allow_commands': False, 'allow_telemetry': False, 'trust_level': 0}
        try:
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    SELECT allow_commands, allow_telemetry, trust_level
                    FROM destinations
                    WHERE destination_hash = ?
                    ORDER BY last_seen DESC LIMIT 1
                ''', (destination_hash,))
                row = c.fetchone()
                conn.close()
            if not row:
                return deny
            return {
                'allow_commands': bool(row[0]),
                'allow_telemetry': bool(row[1]),
                'trust_level': int(row[2] or 0),
            }
        except Exception as e:
            RNS.log(f"Errore lettura permessi peer: {e}", RNS.LOG_WARNING)
            return deny

    def set_propagation_node(self, node_hash):
        """Applica subito un nodo propagation e aggiorna il router LXMF.

        LXMF accetta solo destination hash da 16 byte (32 caratteri hex).
        """
        node_hash = str(node_hash or '').strip().lower()
        if node_hash:
            if not re.fullmatch(r'[0-9a-f]{32}', node_hash):
                raise ValueError('propagation_node deve essere un hash di 32 caratteri hex')
            node_bytes = bytes.fromhex(node_hash)
            self.router.set_outbound_propagation_node(node_bytes)
            self.propagation_node = node_bytes
            self.config['propagation_node'] = node_hash
            try:
                self.router.request_path(node_bytes)
                print(f"📡 Propagation node impostato e path richiesto: {node_hash}")
            except Exception as e:
                print(f"⚠️ Propagation impostato, richiesta path non disponibile: {e}")
            return node_hash

        self.propagation_node = None
        self.config['propagation_node'] = None
        try:
            link = getattr(self.router, 'outbound_propagation_link', None)
            if link:
                link.teardown()
        except Exception:
            pass
        try:
            self.router.outbound_propagation_link = None
            self.router.outbound_propagation_node = None
        except Exception:
            pass
        print("📡 Propagation node rimosso")
        return None

    def _set_propagation_node(self):
        node_hash = self.config.get("propagation_node")
        if node_hash:
            try:
                self.set_propagation_node(node_hash)
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
            self.identity_path = path
            return RNS.Identity.from_file(path)
        
        print("✨ Creazione nuova identità...")
        identity = RNS.Identity()
        path = os.path.join(self.storagepath, identity.hash.hex())
        self.identity_path = path
        identity.to_file(path)
        return identity

    def _name_sidecar_paths(self):
        paths = []
        identity_path = getattr(self, 'identity_path', None)
        if identity_path:
            paths.append(f"{identity_path}.name")
        paths.append(os.path.join(self.storagepath, f"{self.identity.hash.hex()}.name"))
        return paths

    def _save_display_name(self, name):
        name = sanitize_display_name(name, None)
        if not name:
            return
        paths = self._name_sidecar_paths()
        if not paths:
            return
        try:
            with open(paths[0], 'w') as f:
                f.write(name)
        except OSError as e:
            print(f"⚠️ Impossibile salvare il nome identità: {e}")

    def _load_display_name(self):
        identity_hash = self.identity.hash.hex()
        candidates = []

        def add_candidate(value):
            name = clean_display_name(value, None)
            if name and not is_hash_preview_name(name, identity_hash):
                candidates.append(name)

        if isinstance(self.config, dict):
            add_candidate(self.config.get('display_name'))

        for name_file in self._name_sidecar_paths():
            try:
                if os.path.exists(name_file):
                    with open(name_file, 'r', encoding='utf-8', errors='replace') as f:
                        add_candidate(f.read())
            except OSError:
                continue

        identity_path = getattr(self, 'identity_path', None)
        if identity_path:
            base_name = os.path.basename(identity_path)
            if base_name and not re.fullmatch(r'[0-9a-fA-F]{32,64}', base_name):
                add_candidate(base_name)

        if candidates:
            return candidates[0]
        return identity_hash[:8]

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
                
                # Crea JSON con l'appearance sanificata
                fg = appearance[1].hex() if isinstance(appearance[1], bytes) else appearance[1]
                bg = appearance[2].hex() if isinstance(appearance[2], bytes) else appearance[2]
                icon, fg, bg = sanitize_appearance(appearance[0], fg, bg)
                appearance_json = json.dumps({'icon': icon, 'fg': fg, 'bg': bg})
                
                # Aggiorna la tabella destinations
                c.execute('''
                    UPDATE destinations SET appearance = ? WHERE destination_hash = ?
                ''', (appearance_json, peer_hash))
                
                conn.commit()
                conn.close()
                print(f"💾 Appearance salvata per {peer_hash[:8]}: {appearance[0]}")
        except Exception as e:
            print(f"❌ Errore salvataggio appearance: {e}")

    def _update_peer_telemetry(self, source_hash, telemetry_data, msg_data=None):
        """Aggiorna l'ultima telemetria conosciuta di un peer"""
        try:
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                
                # Prepara i dati
                data = {
                    'last_telemetry_time': time.time()
                }
                
                if telemetry_data:
                    if 'location' in telemetry_data:
                        loc = telemetry_data['location']
                        data['last_latitude'] = loc.get('latitude')
                        data['last_longitude'] = loc.get('longitude')
                        data['last_altitude'] = loc.get('altitude')
                        data['last_speed'] = loc.get('speed')
                        data['last_bearing'] = loc.get('bearing')
                        data['last_accuracy'] = loc.get('accuracy')
                    
                    if 'battery' in telemetry_data:
                        bat = telemetry_data['battery']
                        data['last_battery'] = bat.get('charge_percent')
                        data['last_battery_charging'] = 1 if bat.get('charging') else 0
                    
                    if 'temperature' in telemetry_data:
                        data['last_temperature'] = telemetry_data['temperature']
                    
                    if 'humidity' in telemetry_data:
                        data['last_humidity'] = telemetry_data['humidity']
                    
                    if 'pressure' in telemetry_data:
                        data['last_pressure'] = telemetry_data['pressure']
                    
                    if 'ambient_light' in telemetry_data:
                        data['last_light'] = telemetry_data['ambient_light']
                    
                    if 'information' in telemetry_data and 'contents' in telemetry_data['information']:
                        # Prova a estrarre uptime
                        info = telemetry_data['information']['contents']
                        if 'Uptime:' in info:
                            try:
                                uptime_str = info.split('Uptime:')[1].strip().replace('s', '')
                                data['last_uptime'] = float(uptime_str)
                            except:
                                pass
                    
                    if 'processor' in telemetry_data:
                        data['last_processor'] = telemetry_data['processor']
                    
                    if 'ram' in telemetry_data:
                        data['last_ram'] = telemetry_data['ram']
                    
                    if 'nvm' in telemetry_data:
                        data['last_nvm'] = telemetry_data['nvm']
                
                if msg_data:
                    data['last_seen'] = time.time()
                    if hasattr(msg_data, 'hops'):
                        data['hops'] = msg_data.hops
                    if hasattr(msg_data, 'rssi'):
                        data['rssi'] = msg_data.rssi
                    if hasattr(msg_data, 'snr'):
                        data['snr'] = msg_data.snr
                    if hasattr(msg_data, 'q'):
                        data['q'] = msg_data.q
                
                # Costruisci la query dinamica
                set_clause = ', '.join([f"{k}=?" for k in data.keys()])
                values = list(data.values())
                values.append(source_hash)
                
                c.execute(f'''
                    UPDATE destinations 
                    SET {set_clause}
                    WHERE destination_hash = ?
                ''', values)
                
                # Incrementa contatore telemetria
                c.execute('''
                    UPDATE destinations 
                    SET telemetry_count = telemetry_count + 1 
                    WHERE destination_hash = ?
                ''', (source_hash,))
                
                conn.commit()
                conn.close()
                
                print(f"📊 Telemetria aggiornata per {source_hash[:8]}")
                
        except Exception as e:
            print(f"❌ Errore aggiornamento telemetria peer: {e}")

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
                    fg = appearance[1].hex() if isinstance(appearance[1], bytes) else appearance[1]
                    bg = appearance[2].hex() if isinstance(appearance[2], bytes) else appearance[2]
                    icon, fg, bg = sanitize_appearance(appearance[0], fg, bg)
                    appearance_json = json.dumps({'icon': icon, 'fg': fg, 'bg': bg})
                    print(f"🎨 Appearance salvata per {source_hash[:8]}: {icon}")
            
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
                INSERT INTO destinations (
                    destination_hash, identity_hash, aspect, last_seen, hops, rssi, snr, q, app_data, appearance, group_name
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(destination_hash) DO UPDATE SET
                    identity_hash = excluded.identity_hash,
                    last_seen = excluded.last_seen,
                    hops = excluded.hops,
                    rssi = excluded.rssi,
                    snr = excluded.snr,
                    q = excluded.q,
                    app_data = excluded.app_data,
                    appearance = COALESCE(excluded.appearance, appearance)
            ''', (source_hash, identity_hash, 'lxmf.delivery', now, hops, rssi, snr, q, None, appearance_json))
            
            if identity_hash:
                c.execute('''
                    INSERT INTO identities (identity_hash, last_seen, group_name)
                    VALUES (?, ?, NULL)
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

    def _remember_raw(self, msg_hash, raw_hex, raw_ascii):
        """Mantiene in memoria solo gli ultimi raw (max 500)."""
        self.received_raw[msg_hash] = {'hex': raw_hex, 'ascii': raw_ascii}
        while len(self.received_raw) > 500:
            oldest = next(iter(self.received_raw), None)
            if oldest is None:
                break
            self.received_raw.pop(oldest, None)

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
                
            if DEBUG_PACKETS:
                debug_packet(message.packed, "⬇️ RICEVUTO",
                            f"Hash: {message.hash.hex()[:16]}... Da: {message.source_hash.hex()[:16]}...",
                            decrypted)
        
        if DEBUG_PACKETS:
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
                    telemetry_data = telemetry_obj.read_all()
                    self._save_telemetry(message.source_hash.hex(), telemetry_obj, message)

                    self._record_telemetry_event('response_received', {
                        'source': message.source_hash.hex(),
                        'hash': message.hash.hex(),
                        'time': time.time(),
                        'keys': sorted(list(telemetry_data.keys())) if isinstance(telemetry_data, dict) else [],
                    })
                    
                    # 🔴 AGGIORNA L'ULTIMA TELEMETRIA DEL PEER
                    self._update_peer_telemetry(message.source_hash.hex(), telemetry_data, message)
                    
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
                    self._record_telemetry_event('response_received', {
                        'source': message.source_hash.hex(),
                        'hash': message.hash.hex(),
                        'time': time.time(),
                        'keys': ['stream'],
                        'stream_count': len(stream),
                    })
                    print(f"📊 TELEMETRY STREAM: {len(stream)} pacchetti")
                except Exception as e:
                    RNS.log(f"Errore parsing stream telemetria: {e}", RNS.LOG_ERROR)

            if FIELD_ICON_APPEARANCE in message.fields:
                appearance = message.fields[FIELD_ICON_APPEARANCE]
                print(f"🎨 APPEARANCE: {appearance}")
                self._save_peer_appearance(message.source_hash.hex(), appearance)

            if FIELD_COMMANDS in message.fields:
                if getattr(message, 'signature_validated', False):
                    self._handle_commands(message, message.fields[FIELD_COMMANDS])
                else:
                    RNS.log("Comandi ignorati: firma del messaggio non valida", RNS.LOG_WARNING)

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
            self._remember_raw(msg_dict['hash'], raw_hex, raw_ascii)

        if self.message_callback:
            self.message_callback(msg_dict)

    def _handle_commands(self, message, commands):
        if not isinstance(commands, (list, tuple)):
            return
        source = message.source_hash.hex()
        perms = self._peer_permissions(source)
        allow_commands = perms['allow_commands'] or bool(self.config.get('allow_remote_commands', False))
        allow_telemetry = perms['allow_telemetry'] or bool(self.config.get('allow_telemetry_requests', False))
        for cmd_dict in commands:
            if not isinstance(cmd_dict, dict):
                continue
            for cmd_type, cmd_data in cmd_dict.items():
                try:
                    cmd_type = int(cmd_type)
                except (TypeError, ValueError):
                    continue
                if cmd_type == Commands.PING:
                    if not allow_commands:
                        RNS.log(f"PING ignorato da {source[:16]} (non autorizzato)", RNS.LOG_WARNING)
                        continue
                    self.send(source, "Ping reply", callbacks=None)
                elif cmd_type == Commands.ECHO:
                    if not allow_commands:
                        RNS.log(f"ECHO ignorato da {source[:16]} (non autorizzato)", RNS.LOG_WARNING)
                        continue
                    if isinstance(cmd_data, bytes):
                        text = cmd_data.decode('utf-8', errors='replace')
                    else:
                        text = str(cmd_data)
                    self.send(source, f"Echo reply: {text}", callbacks=None)
                elif cmd_type == Commands.SIGNAL_REPORT:
                    if not allow_commands:
                        RNS.log(f"SIGNAL_REPORT ignorato da {source[:16]} (non autorizzato)", RNS.LOG_WARNING)
                        continue
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
                    if not allow_commands:
                        RNS.log(f"CUSTOM ignorato da {source[:16]} (non autorizzato)", RNS.LOG_WARNING)
                        continue
                    print(f"📦 Comando custom ricevuto: {cmd_data}")
                    
                    # Accetta sia "rangetest 15 start" (Sideband) sia "15 start" (UI web).
                    if isinstance(cmd_data, str):
                        parts = cmd_data.lower().split()
                        if parts and parts[0] == 'rangetest':
                            parts = parts[1:]
                        if len(parts) >= 2 and parts[0].isdigit():
                            try:
                                interval = int(parts[0])
                                interval = max(5, min(interval, 3600))
                                action = parts[1]

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
                    if not allow_telemetry:
                        self._record_telemetry_event('request_ignored', {
                            'source': source,
                            'time': time.time(),
                            'reason': 'allow_telemetry_false',
                            'hash': message.hash.hex(),
                        })
                        RNS.log(f"TELEMETRY_REQUEST ignorato da {source[:16]} (non autorizzato)", RNS.LOG_WARNING)
                        continue
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
                        
                        # 🎨 ICON APPEARANCE - DAL CONFIG!
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
                        self._record_telemetry_event('response_sent', {
                            'source': source,
                            'time': time.time(),
                            'keys': sorted(list(fields.keys())),
                        })
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
                if len(self.active_rangetests[peer_hash].get('data_points', [])) >= 10000:
                    RNS.log("Rangetest: limite punti raggiunto, arresto", RNS.LOG_WARNING)
                    self._stop_rangetest(peer_hash)
                    return
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
        try:
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
            
        except Exception as e:
            print(f"❌ Errore esportazione KML: {e}")
            traceback.print_exc()

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
                audio_mode = None
                audio_size = None

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

                # 🎵 AUDIO: Codec2 (preferito) oppure Opus via ffmpeg
                if audio_path:
                    ext = os.path.splitext(audio_path)[1].lower()
                    audio_field_set = False

                    if ext == '.wav':
                        pref_mode, pref_name = self._audio_preference()
                        if pref_mode is not None and CODEC2_AVAILABLE:
                            try:
                                encoded = self._encode_wav_to_codec2(audio_path, pref_mode)
                                if encoded:
                                    fields_dict[FIELD_AUDIO] = [pref_mode, encoded]
                                    audio_field_set = True
                                    audio_mode = pref_mode
                                    audio_size = len(encoded)
                                    print(f"✅ Audio Codec2 ({pref_name}): {len(encoded)} bytes")
                            except Exception as e:
                                print(f"⚠️ Codec2 non disponibile, fallback Opus: {e}")

                        if not audio_field_set:
                            temp_opus = audio_path + "_compressed.opus"
                            try:
                                print("🎵 Conversione WAV con ffmpeg...")
                                cmd = [
                                    'ffmpeg', '-i', audio_path,
                                    '-c:a', 'libopus',
                                    '-b:a', '16k',
                                    '-application', 'voip',
                                    '-frame_duration', '60',
                                    '-packet_loss', '1',
                                    '-ac', '1',
                                    '-ar', '48000',
                                    '-y',
                                    temp_opus
                                ]
                                result = subprocess.run(cmd, capture_output=True)
                                if result.returncode == 0 and os.path.exists(temp_opus):
                                    with open(temp_opus, 'rb') as f:
                                        audio_bytes = f.read()
                                    fields_dict[FIELD_AUDIO] = [AM_OPUS_OGG, audio_bytes]
                                    audio_field_set = True
                                    audio_mode = AM_OPUS_OGG
                                    audio_size = len(audio_bytes)
                                    print(f"✅ Audio Opus compresso con ffmpeg: {len(audio_bytes)} bytes")
                                else:
                                    print(f"❌ FFmpeg errore: {result.stderr.decode(errors='ignore')}")
                                    return {'success': False, 'error': 'FFmpeg compression failed'}
                            except Exception as e:
                                print(f"❌ Conversione audio fallita: {e}")
                                traceback.print_exc()
                                return {'success': False, 'error': f'Conversione audio fallita: {e}'}
                            finally:
                                if os.path.exists(temp_opus):
                                    try:
                                        os.unlink(temp_opus)
                                    except OSError:
                                        pass

                    elif ext in ('.opus', '.ogg', '.oga'):
                        try:
                            with open(audio_path, 'rb') as f:
                                audio_bytes = f.read()
                            fields_dict[FIELD_AUDIO] = [AM_OPUS_OGG, audio_bytes]
                            audio_field_set = True
                            audio_mode = AM_OPUS_OGG
                            audio_size = len(audio_bytes)
                            print(f"✅ Audio Opus allegato: {len(audio_bytes)} bytes")
                        except Exception as e:
                            return {'success': False, 'error': f'Lettura audio fallita: {e}'}
                    else:
                        return {'success': False, 'error': f'Formato audio non supportato: {ext}'}

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
                appearance = self.config.get('appearance', ['antenna', '4c9aff', '1a1f2e'])
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
                    desired_method=self._desired_method()
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
                    'packed_size': msg.packed_size,
                    'title': title,
                    'audio_mode': audio_mode,
                    'audio_size': audio_size,
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
            desired_method = self._desired_method()
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

            desired_method = self._desired_method()
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

            self._record_telemetry_event('request_sent', {
                'destination': dest_hash,
                'hash': msg_hash,
                'time': time.time(),
                'method': method_name,
                'timebase': timebase,
                'collector': bool(is_collector_request),
            })


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

    def _record_announce_event(self, event):
        """Registra ogni announce ricevuto, anche se il peer è già noto."""
        event = dict(event or {})
        aspect = str(event.get('aspect') or 'lxmf.delivery').lower()
        with self.announce_stats_lock:
            self.announce_stats['total'] += 1
            self.announce_stats['by_aspect'][aspect] = self.announce_stats['by_aspect'].get(aspect, 0) + 1
            self.announce_stats['last'] = event
            self.announce_stats['recent'].append(event)
            if len(self.announce_stats['recent']) > 50:
                del self.announce_stats['recent'][:-50]

    def get_announce_stats(self):
        """Statistiche announce in memoria + dati DB per aspect."""
        with self.announce_stats_lock:
            stats = {
                'total': self.announce_stats.get('total', 0),
                'by_aspect': dict(self.announce_stats.get('by_aspect') or {}),
                'last': dict(self.announce_stats['last']) if self.announce_stats.get('last') else None,
                'recent': [dict(item) for item in self.announce_stats.get('recent', [])],
            }

        handlers = []
        for handler in list(getattr(self, 'announce_handlers', [])):
            handlers.append({
                'aspect': getattr(handler, 'aspect', None),
                'aspect_filter': getattr(handler, 'aspect_filter', None),
                'paused': bool(getattr(handler, 'paused', False)),
                'receive_path_responses': bool(getattr(handler, 'receive_path_responses', False)),
            })

        db_by_aspect = {}
        db_total = 0
        db_last = None
        try:
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    SELECT aspect, COUNT(*), MAX(last_seen)
                    FROM destinations
                    WHERE app_data IS NOT NULL
                    GROUP BY aspect
                ''')
                for aspect, count, last_seen in c.fetchall():
                    db_by_aspect[str(aspect or 'unknown')] = {
                        'count': int(count or 0),
                        'last_seen': last_seen,
                    }
                    db_total += int(count or 0)
                    if last_seen and (db_last is None or last_seen > db_last):
                        db_last = last_seen
                conn.close()
        except Exception as exc:
            print(f"⚠️ Errore statistiche announce DB: {exc}")

        stats.update({
            'handlers': handlers,
            'db_total': db_total,
            'db_by_aspect': db_by_aspect,
            'db_last_seen': db_last,
        })
        return stats

    def _record_telemetry_event(self, kind, event=None):
        """Registra eventi telemetria in memoria per la diagnostica UI."""
        event = dict(event or {})
        with self.telemetry_stats_lock:
            if kind == 'request_sent':
                self.telemetry_stats['requests_sent'] += 1
                self.telemetry_stats['last_request'] = event
            elif kind == 'response_received':
                self.telemetry_stats['responses_received'] += 1
                self.telemetry_stats['last_response'] = event
            elif kind == 'response_sent':
                self.telemetry_stats['responses_sent'] += 1
            elif kind == 'request_ignored':
                self.telemetry_stats['requests_ignored'] += 1
                self.telemetry_stats['last_ignored'] = event

    def get_telemetry_stats(self):
        """Diagnostica telemetria: richieste, risposte e permessi."""
        with self.telemetry_stats_lock:
            stats = {
                'requests_sent': self.telemetry_stats.get('requests_sent', 0),
                'responses_received': self.telemetry_stats.get('responses_received', 0),
                'responses_sent': self.telemetry_stats.get('responses_sent', 0),
                'requests_ignored': self.telemetry_stats.get('requests_ignored', 0),
                'last_request': dict(self.telemetry_stats['last_request']) if self.telemetry_stats.get('last_request') else None,
                'last_response': dict(self.telemetry_stats['last_response']) if self.telemetry_stats.get('last_response') else None,
                'last_ignored': dict(self.telemetry_stats['last_ignored']) if self.telemetry_stats.get('last_ignored') else None,
            }

        stats['allow_telemetry_requests'] = bool(self.config.get('allow_telemetry_requests', False))
        stats['allow_remote_commands'] = bool(self.config.get('allow_remote_commands', False))
        stats['last_request_status'] = None

        last_request = stats.get('last_request')
        if last_request and last_request.get('hash'):
            try:
                with self.db_lock:
                    conn = sqlite3.connect(self.peers_db, timeout=10)
                    c = conn.cursor()
                    c.execute('SELECT status FROM sent_messages WHERE hash = ?', (last_request['hash'],))
                    row = c.fetchone()
                    conn.close()
                if row:
                    stats['last_request_status'] = row[0]
            except Exception as exc:
                print(f"⚠️ Errore stato telemetria: {exc}")

        return stats

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
                return clean_display_name(row[0], dest_hash[:8])
            return dest_hash[:8]

    def list_peers(self, aspect='lxmf.delivery'):
        """Restituisce le identità con una destinazione per l'aspect richiesto."""
        aspect = str(aspect or 'lxmf.delivery')
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            
            c.execute('''
                SELECT i.identity_hash, i.display_name,
                       COALESCE(i.is_favorite, 0),
                       MAX(d.appearance),
                       (SELECT GROUP_CONCAT(pg.group_name)
                        FROM peer_groups pg
                        WHERE pg.identity_hash = i.identity_hash) AS groups_csv
                FROM identities i
                JOIN destinations d ON i.identity_hash = d.identity_hash
                    AND d.aspect = ?
                GROUP BY i.identity_hash
            ''', (aspect,))
            rows = c.fetchall()
            conn.close()
        
        # Ordina
        def sort_key(row):
            name = clean_display_name(row[1], '') or ''
            clean = re.sub(r'^[^a-zA-Z0-9]+', '', name)
            return clean.lower()
        
        rows.sort(key=sort_key)
        
        # Costruisci risultato con appearance
        result = []
        for row in rows:
            groups = [group for group in str(row[4] or '').split(',') if group]
            display_name = clean_display_name(row[1], None)
            if display_name and is_hash_preview_name(display_name, row[0]):
                display_name = None
            peer = {
                'hash': row[0],
                'name': display_name,
                'favorite': bool(row[2]),
                'groups': groups,
                'group': groups[0] if groups else None,
                'aspect': aspect
            }
            
            # 🔴 DECODIFICA APPEARANCE SE PRESENTE
            if row[3]:
                try:
                    app_data = json.loads(row[3])
                    peer['appearance'] = sanitize_appearance(
                        app_data.get('icon'), app_data.get('fg'), app_data.get('bg')
                    )
                except:
                    peer['appearance'] = None
            else:
                peer['appearance'] = None
            
            result.append(peer)
        
        return result

    @staticmethod
    def _validate_group(group):
        group = str(group or '').strip()
        if group not in PEER_GROUPS:
            raise ValueError(f'Gruppo non valido: {group}')
        return group

    def set_peer_favorite(self, identity_hash, favorite):
        """Imposta/rimuove il preferito (identities + destinations)."""
        value = 1 if favorite else 0
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            c.execute('UPDATE identities SET is_favorite = ? WHERE identity_hash = ?', (value, identity_hash))
            found = c.rowcount > 0
            c.execute('''
                UPDATE destinations SET is_favorite = ?
                WHERE identity_hash = ? AND aspect = 'lxmf.delivery'
            ''', (value, identity_hash))
            conn.commit()
            conn.close()
        return found

    def set_peer_groups(self, identity_hash, groups):
        """Sostituisce i gruppi N:N e allinea il gruppo primario."""
        if isinstance(groups, str):
            groups = [groups]
        if not isinstance(groups, (list, tuple)):
            raise ValueError('groups deve essere una lista')
        valid = []
        for group in groups:
            group = self._validate_group(group)
            if group not in valid and group != 'favorite':
                valid.append(group)
        primary = valid[0] if valid else None
        now = time.time()
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            c.execute('SELECT 1 FROM identities WHERE identity_hash = ?', (identity_hash,))
            if not c.fetchone():
                conn.close()
                return False, []
            for group in PEER_GROUPS:
                c.execute('INSERT OR IGNORE INTO groups (group_name, description, trust_level, created_at) VALUES (?, ?, ?, ?)',
                          (group, PEER_GROUPS[group].get('name'), PEER_GROUPS[group].get('trust', 0), now))
            c.execute('DELETE FROM peer_groups WHERE identity_hash = ?', (identity_hash,))
            for group in valid:
                c.execute('INSERT OR IGNORE INTO peer_groups (identity_hash, group_name, added_at) VALUES (?, ?, ?)',
                          (identity_hash, group, now))
            c.execute('UPDATE identities SET group_name = ? WHERE identity_hash = ?', (primary, identity_hash))
            found = c.rowcount > 0
            c.execute('UPDATE destinations SET group_name = ? WHERE identity_hash = ?', (primary, identity_hash))
            conn.commit()
            conn.close()
        return found, valid

    def reset_all_groups(self):
        """Rimuove tutti i gruppi da tutti i contatti (i preferiti restano)."""
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            try:
                c = conn.cursor()
                c.execute('UPDATE identities SET group_name = NULL')
                identities = c.rowcount
                c.execute('UPDATE destinations SET group_name = NULL')
                destinations = c.rowcount
                c.execute('DELETE FROM peer_groups')
                links = c.rowcount
                c.execute("INSERT OR REPLACE INTO app_meta (key, value) VALUES ('groups_reset_v2', '1')")
                conn.commit()
            finally:
                conn.close()
        return {'identities': identities, 'destinations': destinations, 'group_links': links}

    def update_peer_settings(self, identity_hash, data):
        """Aggiorna gruppo/permessi/note/preferito di un peer."""
        if not isinstance(data, dict):
            raise ValueError('Dati non validi')
        ident_updates = {}
        dest_updates = {}

        if 'groups' in data:
            found_groups, groups = self.set_peer_groups(identity_hash, data['groups'])
            if not found_groups:
                return False
            ident_updates['group_name'] = groups[0] if groups else None
            dest_updates['group_name'] = groups[0] if groups else None
        elif 'group' in data:
            group = self._validate_group(data['group'])
            found_groups, groups = self.set_peer_groups(identity_hash, [group])
            if not found_groups:
                return False
            ident_updates['group_name'] = groups[0] if groups else None
            dest_updates['group_name'] = groups[0] if groups else None

        if 'trust_level' in data:
            try:
                trust = int(data['trust_level'])
            except (TypeError, ValueError):
                raise ValueError('trust_level non valido')
            if not 0 <= trust <= 3:
                raise ValueError('trust_level fuori range')
            dest_updates['trust_level'] = trust

        if 'allow_telemetry' in data:
            dest_updates['allow_telemetry'] = 1 if data['allow_telemetry'] else 0
        if 'allow_commands' in data:
            dest_updates['allow_commands'] = 1 if data['allow_commands'] else 0
        if 'favorite' in data:
            fav = 1 if data['favorite'] else 0
            ident_updates['is_favorite'] = fav
            dest_updates['is_favorite'] = fav
        if 'notes' in data:
            notes = str(data['notes'] or '')[:2000]
            ident_updates['notes'] = notes
            dest_updates['notes'] = notes

        found = False
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            if ident_updates:
                cols = ', '.join(f'{k}=?' for k in ident_updates)
                c.execute(f'UPDATE identities SET {cols} WHERE identity_hash = ?',
                          list(ident_updates.values()) + [identity_hash])
                found = c.rowcount > 0
            if dest_updates:
                cols = ', '.join(f'{k}=?' for k in dest_updates)
                c.execute(f'UPDATE destinations SET {cols} WHERE identity_hash = ?',
                          list(dest_updates.values()) + [identity_hash])
                found = found or c.rowcount > 0
            conn.commit()
            conn.close()
        return found

    def list_favorite_hashes(self):
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            c.execute('''
                SELECT identity_hash FROM identities
                WHERE is_favorite = 1
                ORDER BY LOWER(display_name) ASC
            ''')
            rows = c.fetchall()
            conn.close()
        return [row[0] for row in rows]

    def add_peer_manual(self, identity_hash, name, group=None):
        group = self._validate_group(group) if group else None
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            now = time.time()
            c.execute('''
                INSERT INTO identities (identity_hash, display_name, first_seen, last_seen, group_name)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(identity_hash) DO UPDATE SET
                    display_name = excluded.display_name,
                    last_seen = excluded.last_seen
            ''', (identity_hash, name, now, now, group))
            if group:
                c.execute('INSERT OR IGNORE INTO groups (group_name, description, trust_level, created_at) VALUES (?, ?, ?, ?)',
                          (group, PEER_GROUPS[group].get('name'), PEER_GROUPS[group].get('trust', 0), now))
                c.execute('INSERT OR REPLACE INTO peer_groups (identity_hash, group_name, added_at) VALUES (?, ?, ?)',
                          (identity_hash, group, now))
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
                    display_name = clean_display_name(display_name, dest_hash[:8])

                    if identity_hash:
                        dst_c.execute('''
                            INSERT INTO identities (identity_hash, display_name, first_seen, last_seen, group_name)
                            VALUES (?, ?, ?, ?, NULL)
                            ON CONFLICT(identity_hash) DO UPDATE SET
                                display_name = excluded.display_name,
                                last_seen = excluded.last_seen
                        ''', (identity_hash, display_name, ts, ts))

                    dst_c.execute('''
                        INSERT INTO destinations (destination_hash, identity_hash, aspect, last_seen, hops, rssi, snr, q, app_data, group_name)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
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
            message.desired_method = LXMF.LXMessage.PROPAGATED
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
    def __init__(self, messenger, aspect='lxmf.delivery'):
        self.messenger = messenger
        self.aspect = str(aspect or 'lxmf.delivery')
        self.aspect_filter = self.aspect
        # RNS only forwards packet.context == PATH_RESPONSE to handlers
        # that opt in explicitly.
        self.receive_path_responses = True

    def received_announce(self, destination_hash, announced_identity, app_data, announce_packet_hash=None, **kwargs):
        peer_hash = destination_hash.hex()
        is_path_response = bool(kwargs.get('is_path_response'))
        RNS.log(f"📢 Annuncio {self.aspect} da {peer_hash}", RNS.LOG_INFO)

        display_name = None
        announce_info = None
        if app_data and is_discovery_aspect(self.aspect):
            announce_info = parse_interface_discovery_app_data(app_data)
            if announce_info:
                display_name = announce_info.get('name')
                RNS.log(
                    f"   Interfaccia scoperta: {announce_info.get('type')} / {display_name}",
                    RNS.LOG_INFO,
                )
        elif app_data:
            try:
                display_name = LXMF.display_name_from_app_data(app_data)
                RNS.log(f"   Nome estratto: {display_name}", RNS.LOG_INFO)
            except Exception as e:
                RNS.log(f"   Errore estrazione nome: {e}", RNS.LOG_WARNING)
        display_name = clean_display_name(display_name, peer_hash[:8])

        identity_hash = announced_identity.hash.hex() if announced_identity else None
        if announce_info is not None and identity_hash:
            announce_info.setdefault('network_id', identity_hash)

        hops = None
        try:
            hops = RNS.Transport.hops_to(destination_hash)
        except Exception:
            pass

        now = time.time()

        rssi = snr = q = None
        if announce_packet_hash:
            try:
                reticulum = RNS.Reticulum.get_instance()
                if reticulum:
                    rssi = reticulum.get_packet_rssi(announce_packet_hash)
                    snr = reticulum.get_packet_snr(announce_packet_hash)
                    q = reticulum.get_packet_q(announce_packet_hash)
            except Exception:
                pass

        event = {
            'aspect': self.aspect,
            'identity_hash': identity_hash,
            'destination_hash': peer_hash,
            'display_name': display_name,
            'hops': hops,
            'rssi': rssi,
            'snr': snr,
            'q': q,
            'time': now,
            'is_path_response': is_path_response,
            'announce_packet_hash': announce_packet_hash.hex() if announce_packet_hash else None,
            'announce_info': announce_info,
        }
        recorder = getattr(self.messenger, '_record_announce_event', None)
        if recorder:
            try:
                recorder(event)
            except Exception as e:
                RNS.log(f"Errore registrazione evento announce: {e}", RNS.LOG_WARNING)

        try:
            with self.messenger.db_lock:
                conn = sqlite3.connect(self.messenger.peers_db, timeout=10)
                try:
                    c = conn.cursor()

                    if identity_hash:
                        c.execute('''
                            INSERT INTO identities (identity_hash, display_name, first_seen, last_seen, group_name)
                            VALUES (?, ?, ?, ?, NULL)
                            ON CONFLICT(identity_hash) DO UPDATE SET
                                display_name = excluded.display_name,
                                last_seen = excluded.last_seen
                        ''', (identity_hash, display_name, now, now))

                    c.execute('''
                        INSERT INTO destinations (destination_hash, identity_hash, aspect, last_seen, hops, app_data, rssi, snr, q, group_name)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        ON CONFLICT(destination_hash) DO UPDATE SET
                            identity_hash = excluded.identity_hash,
                            last_seen = excluded.last_seen,
                            hops = COALESCE(excluded.hops, destinations.hops),
                            app_data = excluded.app_data,
                            rssi = COALESCE(excluded.rssi, destinations.rssi),
                            snr = COALESCE(excluded.snr, destinations.snr),
                            q = COALESCE(excluded.q, destinations.q)
                    ''', (peer_hash, identity_hash, self.aspect, now, hops,
                          app_data.hex() if app_data else None, rssi, snr, q))

                    conn.commit()
                finally:
                    conn.close()
        except Exception as e:
            RNS.log(f"Errore salvataggio annuncio {self.aspect}: {e}", RNS.LOG_ERROR)
            return

        callback = getattr(self.messenger, 'announce_callback', None)
        if callback:
            try:
                callback({
                    'aspect': self.aspect,
                    'identity_hash': identity_hash,
                    'destination_hash': peer_hash,
                    'display_name': display_name,
                    'hops': hops,
                    'rssi': rssi,
                    'snr': snr,
                    'q': q,
                    'app_data': app_data.hex() if app_data else None,
                    'time': now,
                    'is_path_response': is_path_response,
                    'announce_info': announce_info,
                })
            except Exception as e:
                RNS.log(f"Errore callback annuncio {self.aspect}: {e}", RNS.LOG_WARNING)