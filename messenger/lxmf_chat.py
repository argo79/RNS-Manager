#!/usr/bin/env python3
# app.py - Server web per LXMF Messenger

from flask import Flask, render_template, request, jsonify, send_from_directory, Response, g
from flask_socketio import SocketIO, emit
import json
import os
import time
import threading
import queue
import sqlite3
import base64
import traceback
import signal
import uuid
import tempfile
import argparse
import hmac
import ipaddress
import re
import io
import subprocess
import wave
from functools import lru_cache
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from messenger import Messenger, Commands, guess_image_format, CODEC2_AVAILABLE, clean_display_name, is_hash_preview_name
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
import LXMF
import RNS

try:
    from version import APP_VERSION
except ImportError:
    APP_VERSION = "1.2.0"

# 🔴 COSTANTI LXMF
FIELD_TELEMETRY = 0x02
FIELD_TELEMETRY_STREAM = 0x03
FIELD_ICON_APPEARANCE = 0x04
FIELD_FILE_ATTACHMENTS = 0x05
FIELD_IMAGE = 0x06
FIELD_AUDIO = 0x07
FIELD_COMMANDS = 0x09

original_signal = signal.signal

def patched_signal(sig, handler):
    if threading.current_thread() is threading.main_thread():
        return original_signal(sig, handler)
    return None
signal.signal = patched_signal

# ==================== HELPERS DI SICUREZZA ====================

STORAGE_ROOT = os.path.realpath(os.path.expanduser("~/.rns_manager/storage"))
AUTH_TOKEN = os.environ.get('LXMF_AUTH_TOKEN', '')
ANNOUNCE_ASPECTS_ENV = os.environ.get('LXMF_ANNOUNCE_ASPECTS')
ANNOUNCE_ASPECTS = (
    normalize_announce_aspects(ANNOUNCE_ASPECTS_ENV, ensure_delivery=True)
    if ANNOUNCE_ASPECTS_ENV else []
)
LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
IMAGE_FORMAT_MIME = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'gif': 'image/gif',
    'webp': 'image/webp',
}
CONFIG_LOCK = threading.Lock()

CONFIG_BOOL_KEYS = {
    'try_propagation_on_fail',
    'enable_location', 'enable_system', 'allow_remote_commands',
    'allow_telemetry_requests',
}
CONFIG_INT_RANGES = {
    'stamp_cost': (0, 100),
    'gpsd_port': (1, 65535),
    'message_retention_days': (0, 3650),
    'raw_retention_days': (0, 3650),
    'telemetry_retention_days': (0, 3650),
    'rangetest_retention_days': (0, 3650),
}
CONFIG_ENUMS = {
    'delivery_mode': {'direct', 'opportunistic', 'propagated'},
    'location_source': {'fixed', 'gpsd'},
}
CONFIG_ALLOWED_KEYS = set(CONFIG_BOOL_KEYS) | set(CONFIG_INT_RANGES) | set(CONFIG_ENUMS) | {
    'propagation_node', 'preferred_audio', 'fixed_location', 'gpsd_host', 'appearance',
    'announce_aspects',
}


def _load_secret_key():
    """SECRET_KEY da env oppure persistente in ~/.rns_manager/.secret_key (0600)."""
    env_key = os.environ.get('LXMF_SECRET_KEY')
    if env_key:
        return env_key
    path = os.path.expanduser('~/.rns_manager/.secret_key')
    try:
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                data = f.read(64)
            if len(data) >= 32:
                return data
        data = os.urandom(32)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return data
    except Exception:
        return os.urandom(32)


def _auth_ok():
    """Auth token opzionale (env LXMF_AUTH_TOKEN)."""
    if not AUTH_TOKEN:
        return True
    supplied = request.headers.get('X-Auth-Token') or request.cookies.get('lxmf_auth')
    if not supplied:
        supplied = request.args.get('token', '')
        if supplied:
            g.set_auth_cookie = True
    return bool(supplied) and hmac.compare_digest(str(supplied), AUTH_TOKEN)


def _request_host():
    parsed = urlparse('//' + (request.host or ''))
    return (parsed.hostname or '').lower()


def _host_is_allowed(host):
    if host in LOCAL_HOSTS:
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _origin_matches(origin, host):
    parsed = urlparse(origin)
    if parsed.scheme not in ('http', 'https'):
        return False
    origin_host = (parsed.hostname or '').lower()
    if host in LOCAL_HOSTS:
        return origin_host in LOCAL_HOSTS
    return origin_host == host


def _safe_display_name(value, fallback='Utente'):
    text = str(value or '')
    text = ''.join(ch for ch in text if ch >= ' ' and ch != '')
    text = ' '.join(text.split())
    return (text or fallback)[:96]


def _resolve_identity_path(name):
    """Risolve un nome identità solo dentro ~/.rns_manager/storage."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if not name or os.path.basename(name) != name or name in ('.', '..'):
        return None
    path = os.path.realpath(os.path.join(STORAGE_ROOT, name))
    if not path.startswith(STORAGE_ROOT + os.sep) or not os.path.isfile(path):
        return None
    return path


def _is_hex_hash(value, lengths=(32, 64)):
    if not isinstance(value, str):
        return False
    value = value.strip()
    return len(value) in lengths and bool(re.fullmatch(r'[0-9a-fA-F]+', value))


def _parse_aspects(value):
    """Normalizza una lista di aspect separati da virgola."""
    return [a.strip().lower() for a in str(value or '').split(',') if a.strip()]


def _is_valid_aspect(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-z0-9_.-]{1,64}', value))


def _normalize_propagation_node(value):
    """Normalizza un destination hash propagation (16 byte / 32 hex)."""
    text = str(value or '').strip().lower()
    if not text:
        return None
    if not _is_hex_hash(text, (32,)):
        raise ValueError('propagation_node deve essere un hash di 32 caratteri hex')
    return text


def _sanitize_appearance(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('appearance non valido')
    icon = str(value[0] or '')
    fg = str(value[1] or '').lstrip('#')
    bg = str(value[2] or '').lstrip('#')
    if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', icon):
        raise ValueError('icona non valida')
    if not re.fullmatch(r'[0-9a-fA-F]{6}', fg) or not re.fullmatch(r'[0-9a-fA-F]{6}', bg):
        raise ValueError('colori non validi')
    return [icon.lower(), fg.lower(), bg.lower()]


def _sanitize_fixed_location(value):
    if not isinstance(value, dict):
        raise ValueError('fixed_location non valido')
    lat = float(value.get('latitude'))
    lon = float(value.get('longitude'))
    alt = float(value.get('altitude', 0))
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise ValueError('coordinate non valide')
    if not (-1000.0 <= alt <= 100000.0):
        raise ValueError('altitudine non valida')
    return {'latitude': lat, 'longitude': lon, 'altitude': alt}


def _sanitize_config_update(data):
    """Valida e normalizza un sottoinsieme di config.json."""
    if not isinstance(data, dict):
        raise ValueError('Config non valida')
    unknown = set(data.keys()) - CONFIG_ALLOWED_KEYS
    if unknown:
        raise ValueError('Chiavi non consentite: ' + ', '.join(sorted(unknown)))
    clean = {}
    for key, value in data.items():
        if key in CONFIG_BOOL_KEYS:
            clean[key] = bool(value)
        elif key in CONFIG_INT_RANGES:
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                raise ValueError(f'{key} deve essere un intero')
            lo, hi = CONFIG_INT_RANGES[key]
            if not (lo <= ivalue <= hi):
                raise ValueError(f'{key} fuori range ({lo}-{hi})')
            clean[key] = ivalue
        elif key in CONFIG_ENUMS:
            svalue = str(value)
            if svalue not in CONFIG_ENUMS[key]:
                raise ValueError(f'{key} non valido')
            clean[key] = svalue
        elif key == 'propagation_node':
            clean[key] = _normalize_propagation_node(value)
        elif key == 'preferred_audio':
            svalue = str(value or '')
            if not re.fullmatch(r'[a-z0-9_]{1,32}', svalue):
                raise ValueError('preferred_audio non valido')
            clean[key] = svalue
        elif key == 'fixed_location':
            clean[key] = _sanitize_fixed_location(value)
        elif key == 'announce_aspects':
            clean[key] = normalize_announce_aspects(
                value, ensure_delivery=True, strict=True
            )
        elif key == 'gpsd_host':
            svalue = _safe_display_name(value, 'localhost')
            clean[key] = svalue
        elif key == 'appearance':
            clean[key] = _sanitize_appearance(value)
    return clean


def _enrich_peers_for_aspect(messenger_obj, peers, aspect):
    """Aggiunge telemetria, annunci discovery e hash destinazione ai peer."""
    with messenger_obj.db_lock:
        conn = sqlite3.connect(messenger_obj.peers_db, timeout=10)
        c = conn.cursor()
        for peer in peers:
            c.execute('''
                SELECT last_latitude, last_longitude, last_altitude,
                       last_speed, last_bearing, last_accuracy,
                       last_battery, last_battery_charging,
                       last_temperature, last_humidity, last_pressure,
                       last_light, last_uptime, last_processor,
                       last_ram, last_nvm, last_telemetry_time,
                       trust_level, allow_telemetry, allow_commands,
                       messages_sent, messages_received, telemetry_count,
                       is_favorite, group_name, notes,
                       destination_hash, last_seen, hops, rssi, snr, q,
                       app_data
                FROM destinations
                WHERE identity_hash = ? AND aspect = ?
                ORDER BY last_seen DESC LIMIT 1
            ''', (peer['hash'], aspect))

            row = c.fetchone()
            if row:
                announce_info = None
                if is_discovery_aspect(aspect) and row[32]:
                    try:
                        announce_info = parse_interface_discovery_app_data(
                            bytes.fromhex(row[32])
                        )
                    except Exception:
                        announce_info = None

                last_location = None
                if row[0] is not None and row[1] is not None:
                    last_location = {
                        'latitude': row[0],
                        'longitude': row[1],
                        'altitude': row[2],
                        'speed': row[3],
                        'bearing': row[4],
                        'accuracy': row[5],
                        'source': 'telemetry',
                    }
                elif (announce_info
                      and announce_info.get('latitude') is not None
                      and announce_info.get('longitude') is not None):
                    last_location = {
                        'latitude': announce_info['latitude'],
                        'longitude': announce_info['longitude'],
                        'altitude': announce_info.get('height'),
                        'speed': None,
                        'bearing': None,
                        'accuracy': None,
                        'source': 'discovery',
                    }

                peer.update({
                    'dest_hash': row[26],
                    'last_seen': row[27],
                    'hops': row[28],
                    'rssi': row[29],
                    'snr': row[30],
                    'q': row[31],
                    'last_location': last_location,
                    'announce_info': announce_info,
                    'last_battery': {
                        'percent': row[6],
                        'charging': bool(row[7])
                    } if row[6] is not None else None,
                    'last_temperature': row[8],
                    'last_humidity': row[9],
                    'last_pressure': row[10],
                    'last_light': row[11],
                    'last_uptime': row[12],
                    'last_processor': row[13],
                    'last_ram': row[14],
                    'last_nvm': row[15],
                    'last_telemetry_time': row[16],
                    'trust_level': row[17],
                    'allow_telemetry': bool(row[18]),
                    'allow_commands': bool(row[19]),
                    'messages_sent': row[20],
                    'messages_received': row[21],
                    'telemetry_count': row[22],
                    'notes': row[25]
                })
            else:
                peer['favorite'] = False
                peer.setdefault('groups', [])
                peer.setdefault('group', None)
            peer['aspect'] = aspect
        conn.close()
    return peers


def _save_config(messenger):
    """Scrittura atomica di config.json."""
    with CONFIG_LOCK:
        tmp_path = messenger.configpath + '.tmp'
        with open(tmp_path, 'w') as f:
            json.dump(messenger.config, f, indent=4)
        os.replace(tmp_path, messenger.configpath)


app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = _load_secret_key()
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024  # 64 MB
socketio = SocketIO(app)  # default: solo same-origin (niente CORS "*")


@app.before_request
def _enforce_local_origin():
    host = _request_host()
    if not _host_is_allowed(host):
        return jsonify({'success': False, 'error': 'Host non consentito'}), 403
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('Origin')
        referer = request.headers.get('Referer')
        if origin and not _origin_matches(origin, host):
            return jsonify({'success': False, 'error': 'Origine non consentita'}), 403
        if not origin and referer and not _origin_matches(referer, host):
            return jsonify({'success': False, 'error': 'Origine non consentita'}), 403

    if not request.path.startswith('/static/') and not _auth_ok():
        if request.path.startswith('/api/') or 'application/json' in (request.headers.get('Accept') or ''):
            return jsonify({'success': False, 'error': 'Autenticazione richiesta'}), 401
        return Response(
            "<h1>Autenticazione richiesta</h1>"
            "<p>Aggiungi <code>?token=...</code> all'URL oppure invia l'header "
            "<code>X-Auth-Token</code>.</p>",
            status=401, mimetype='text/html'
        )
    return None


@app.after_request
def _security_headers(response):
    if getattr(g, 'set_auth_cookie', False) and AUTH_TOKEN:
        response.set_cookie('lxmf_auth', AUTH_TOKEN, httponly=True, samesite='Strict',
                            secure=request.is_secure, max_age=7 * 86400)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' data: https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
        "worker-src 'self' blob:; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


@app.errorhandler(RequestEntityTooLarge)
def _request_too_large(_e):
    return jsonify({'success': False, 'error': 'File troppo grande'}), 413

# Istanza globale del messenger
messenger = None
message_queue = queue.Queue()
messenger_lock = threading.Lock()

def on_progress(info):
    """Callback quando il progresso di invio cambia"""
    socketio.emit('progress_update', info)

def message_callback(msg):
    """Callback per messaggi in arrivo - invia via websocket"""
    try:
        # Estrai comandi se presenti
        commands = None
        if msg.get('fields') and FIELD_COMMANDS in msg['fields']:
            commands = msg['fields'][FIELD_COMMANDS]
            print(f"🎮 Comandi nel messaggio: {commands}")
        
        # 🔴 CONVERTI APPEARANCE DA BYTES A STRINGA
        appearance = msg.get('appearance')
        if appearance and len(appearance) >= 3:
            fg = appearance[1]
            bg = appearance[2]
            
            if isinstance(fg, bytes):
                fg = fg.hex()
            if isinstance(bg, bytes):
                bg = bg.hex()
            
            appearance = [
                appearance[0],
                fg,
                bg
            ]
            print(f"🎨 APPEARANCE convertita: {appearance}")
        
        # 🔴 ESTRAI TELEMETRIA
        telemetry_data = None
        if msg.get('telemetry'):
            if hasattr(msg['telemetry'], 'read_all'):
                try:
                    telemetry_data = msg['telemetry'].read_all()
                    print(f"📊 TELEMETRIA RICEVUTA: {telemetry_data}")
                except Exception as e:
                    print(f"❌ Errore lettura telemetria: {e}")
                    telemetry_data = {'error': str(e)}
            else:
                telemetry_data = {'info': 'Telemetria non decodificabile'}
        
        # 🔴 CONVERTI ATTACHMENTS
        attachments = msg.get('attachments', [])
        serializable_attachments = []
        for att in attachments:
            if len(att) == 2 and att[0] in ['image', 'audio', 'file']:
                att_type = att[0]
                att_data = att[1]
                
                if att_type == 'image':
                    serializable_attachments.append({
                        'type': 'image',
                        'format': att_data.get('type', 'jpg'),
                        'size': att_data.get('size', 0),
                        'data': att_data.get('bytes')
                    })
                elif att_type == 'audio':
                    serializable_attachments.append({
                        'type': 'audio',
                        'mode': att_data.get('mode', 16),
                        'size': att_data.get('size', 0),
                        'data': att_data.get('bytes')
                    })
                elif att_type == 'file':
                    serializable_attachments.append({
                        'type': 'file',
                        'name': att_data.get('name', 'file.bin'),
                        'size': att_data.get('size', 0),
                        'data': att_data.get('bytes')
                    })
        
        serializable_msg = {
            'from': msg.get('from'),
            'to': msg.get('to'),
            'content': msg.get('content', ''),
            'title': msg.get('title', ''),
            'time': msg.get('time'),
            'hash': msg.get('hash'),
            'size': msg.get('size', 0),
            'packed_size': msg.get('packed_size', 0),
            'method': msg.get('method'),
            'signature_valid': msg.get('signature_valid', False),
            'attachments': serializable_attachments,
            'appearance': appearance,
            'rssi': msg.get('rssi'),
            'snr': msg.get('snr'),
            'q': msg.get('q'),
            'hops': msg.get('hops'),
            'raw_hex': msg.get('raw_hex'),
            'raw_ascii': msg.get('raw_ascii'),
            'commands': commands,
            'telemetry_data': telemetry_data,
        }
        
        socketio.emit('new_message', serializable_msg)
        message_queue.put(serializable_msg)
    except Exception as e:
        print(f"Errore in message_callback: {e}")
        traceback.print_exc()

def announce_callback(peer):
    """Inoltra al frontend un annuncio/peer aggiornato."""
    try:
        socketio.emit('peer_update', peer)
    except Exception as e:
        print(f"Errore in announce_callback: {e}")
        traceback.print_exc()

def create_messenger(identity_file=None, display_name=None):
    """Crea una nuova istanza del messenger con l'identità specificata"""
    global messenger
    try:
        if messenger is not None:
            print("⚠️ Messenger già inizializzato, uso quello esistente")
            return True
            
        print(f"🔄 Inizializzazione messenger con identità: {identity_file}")
        
        with messenger_lock:
            if identity_file:
                identity_path = _resolve_identity_path(identity_file)
                if not identity_path:
                    raise ValueError('Identità non valida')
                messenger = Messenger(
                    identity_path=identity_path,
                    announce_aspects=ANNOUNCE_ASPECTS,
                    display_name=display_name,
                )
            else:
                messenger = Messenger(
                    announce_aspects=ANNOUNCE_ASPECTS,
                    display_name=display_name,
                )
            
            messenger.local_dest = messenger.dest
            messenger.message_callback = message_callback
            messenger.announce_callback = announce_callback
            messenger.announce()
        
        print(f"✅ Messenger inizializzato: {messenger.display_name}")
        print(f"🆔 Identity hash: {messenger.get_hash()}")
        return True
        
    except Exception as e:
        print(f"❌ Errore inizializzazione messenger: {e}")
        traceback.print_exc()
        return False

# ==================== API ROUTES ====================

@app.route('/')
@app.route('/chat')
def index():
    """Serve la pagina principale"""
    return render_template('lxmf_chat.html', version=APP_VERSION)

@app.route('/api/status')
def get_status():
    """Restituisce lo stato corrente"""
    if not messenger:
        return jsonify({'status': 'not_initialized', 'message': 'Nessuna identità selezionata'})
    
    aspect_hash = None
    try:
        # Usa direttamente la destinazione già registrata: non crearne una nuova.
        aspect_hash = messenger.get_delivery_hash()
    except Exception:
        aspect_hash = messenger.dest.hash.hex() if hasattr(messenger, 'dest') and messenger.dest else None
    
    return jsonify({
        'status': 'ok',
        'identity': messenger.get_hash(),
        'aspect': 'lxmf.delivery',
        'aspect_hash': aspect_hash,
        'name': messenger.display_name,
        'propagation_node': messenger.config.get('propagation_node'),
        'connected': True
    })

@app.route('/api/identities')
def list_identities():
    """Lista le identità disponibili"""
    storagepath = os.path.expanduser("~/.rns_manager/storage")
    identities = []
    
    if not os.path.exists(storagepath):
        return jsonify([])
    
    for f in os.listdir(storagepath):
        fpath = os.path.join(storagepath, f)
        if os.path.isfile(fpath) and os.path.getsize(fpath) == 64:
            name_file = os.path.join(storagepath, f"{f}.name")
            name = None
            if os.path.exists(name_file):
                with open(name_file, 'r') as nf:
                    name = nf.read().strip()
            
            try:
                # Non leggere mai i byte del file identità (contiene la chiave privata).
                ident = RNS.Identity.from_file(fpath)
                identity_hash = ident.hash.hex() if ident else None
                hash_preview = identity_hash[:16] if identity_hash else f[:16]
            except Exception:
                identity_hash = None
                hash_preview = f[:16]

            name = clean_display_name(name, None)
            if name and is_hash_preview_name(name, identity_hash):
                name = None

            identities.append({
                'file': f,
                'hash': identity_hash or (f[:64] if len(f) >= 64 else f),
                'identity_hash': identity_hash,
                'preview': hash_preview,
                'name': name or f[:8]
            })
    
    return jsonify(identities)

@app.route('/api/identity/select', methods=['POST'])
def select_identity():
    """Seleziona un'identità e inizializza il messenger"""
    global messenger
    data = request.get_json(silent=True) or {}
    identity_file = data.get('identity')
    display_name = _safe_display_name(data.get('name', ''), '')
    
    if not identity_file:
        return jsonify({'success': False, 'error': 'Nessuna identità specificata'})

    resolved_identity = _resolve_identity_path(identity_file)
    if not resolved_identity:
        return jsonify({'success': False, 'error': 'Identità non valida'}), 400
    if os.path.getsize(resolved_identity) != 64:
        return jsonify({'success': False, 'error': 'File identità non valido'}), 400
    identity_file = os.path.basename(identity_file)
    
    try:
        if messenger is not None:
            print("⚠️ Messenger già attivo, procedo con shutdown...")
            shutdown_result = shutdown_messenger()
            if not shutdown_result.json['success']:
                print("⚠️ Shutdown precedente non completato, procedo comunque...")
        
        time.sleep(1.0)
        
        print(f"🔄 Inizializzazione messenger con identità: {identity_file}")
        success = create_messenger(identity_file, display_name=display_name)
        
        if success and messenger:
            if display_name:
                name_file = os.path.join(STORAGE_ROOT, f"{identity_file}.name")
                with open(name_file, 'w') as f:
                    f.write(display_name)
            
            return jsonify({
                'success': True,
                'identity': messenger.get_hash(),
                'aspect_hash': messenger.get_delivery_hash(),
                'name': messenger.display_name
            })
        else:
            if messenger is not None:
                try:
                    _cleanup_messenger()
                except Exception:
                    pass
            return jsonify({'success': False, 'error': 'Inizializzazione fallita'}), 500
    
    except Exception as e:
        print(f"❌ Errore in select_identity: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/aspects')
def get_announce_aspects():
    """Aspetti annunci supportati (estendibile per futuri tool)."""
    aspects = getattr(messenger, 'announce_aspects', None) if messenger else None
    if not aspects:
        aspects = ANNOUNCE_ASPECTS or DEFAULT_ANNOUNCE_ASPECTS
    aspects = normalize_announce_aspects(aspects, ensure_delivery=True)
    labels = {aspect: ASPECT_LABELS.get(aspect, aspect) for aspect in aspects}
    return jsonify({
        'aspects': aspects,
        'default': aspects[0] if aspects else 'lxmf.delivery',
        'labels': labels,
        'map_capable': [aspect for aspect in aspects if is_map_capable_aspect(aspect)],
    })

@app.route('/api/peers')
def get_peers():
    """Restituisce la lista dei peer con tutti i dati"""
    if not messenger:
        return jsonify([])

    default_aspects = getattr(messenger, 'announce_aspects', None) or ANNOUNCE_ASPECTS or DEFAULT_ANNOUNCE_ASPECTS
    default_aspect = default_aspects[0]
    aspect = (request.args.get('aspect') or default_aspect).strip().lower()
    if not _is_valid_aspect(aspect):
        return jsonify({'success': False, 'error': 'Aspect non valido'}), 400

    try:
        # Usa direttamente list_peers() che ora restituisce tutto
        peers = messenger.list_peers(aspect)
        
        # 🔴 AGGIUNGI I DATI DELL'ULTIMA TELEMETRIA
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            for peer in peers:
                # Recupera gli ultimi dati telemetria per questo peer
                c.execute('''
                    SELECT last_latitude, last_longitude, last_altitude,
                           last_speed, last_bearing, last_accuracy,
                           last_battery, last_battery_charging,
                           last_temperature, last_humidity, last_pressure,
                           last_light, last_uptime, last_processor,
                           last_ram, last_nvm, last_telemetry_time,
                           trust_level, allow_telemetry, allow_commands,
                           messages_sent, messages_received, telemetry_count,
                           is_favorite, group_name, notes,
                           destination_hash, last_seen, hops, rssi, snr, q,
                           app_data
                    FROM destinations 
                    WHERE identity_hash = ? AND aspect = ?
                    ORDER BY last_seen DESC LIMIT 1
                ''', (peer['hash'], aspect))
                
                row = c.fetchone()
                if row:
                    announce_info = None
                    if is_discovery_aspect(aspect) and row[32]:
                        try:
                            announce_info = parse_interface_discovery_app_data(
                                bytes.fromhex(row[32])
                            )
                        except Exception:
                            announce_info = None

                    last_location = None
                    if row[0] is not None and row[1] is not None:
                        last_location = {
                            'latitude': row[0],
                            'longitude': row[1],
                            'altitude': row[2],
                            'speed': row[3],
                            'bearing': row[4],
                            'accuracy': row[5],
                            'source': 'telemetry',
                        }
                    elif (announce_info
                          and announce_info.get('latitude') is not None
                          and announce_info.get('longitude') is not None):
                        last_location = {
                            'latitude': announce_info['latitude'],
                            'longitude': announce_info['longitude'],
                            'altitude': announce_info.get('height'),
                            'speed': None,
                            'bearing': None,
                            'accuracy': None,
                            'source': 'discovery',
                        }

                    peer.update({
                        'dest_hash': row[26],
                        'last_seen': row[27],
                        'hops': row[28],
                        'rssi': row[29],
                        'snr': row[30],
                        'q': row[31],
                        'last_location': last_location,
                        'announce_info': announce_info,
                        'last_battery': {
                            'percent': row[6],
                            'charging': bool(row[7])
                        } if row[6] is not None else None,
                        'last_temperature': row[8],
                        'last_humidity': row[9],
                        'last_pressure': row[10],
                        'last_light': row[11],
                        'last_uptime': row[12],
                        'last_processor': row[13],
                        'last_ram': row[14],
                        'last_nvm': row[15],
                        'last_telemetry_time': row[16],
                        'trust_level': row[17],
                        'allow_telemetry': bool(row[18]),
                        'allow_commands': bool(row[19]),
                        'messages_sent': row[20],
                        'messages_received': row[21],
                        'telemetry_count': row[22],
                        'notes': row[25]
                    })
                else:
                    peer['favorite'] = False
                    peer.setdefault('groups', [])
                    peer.setdefault('group', None)
                peer['aspect'] = aspect
            
            conn.close()
        
        return jsonify(peers)
    
    except Exception as e:
        print(f"Errore in get_peers: {e}")
        traceback.print_exc()
        return jsonify([])

@app.route('/api/map/peers')
def get_map_peers():
    """Marker mappa: contatti LXMF con posizione + interfacce discovery."""
    if not messenger:
        return jsonify([])
    try:
        configured = getattr(messenger, 'announce_aspects', None) or ANNOUNCE_ASPECTS or DEFAULT_ANNOUNCE_ASPECTS
        interface_aspects = [
            aspect for aspect in configured
            if is_discovery_aspect(aspect)
        ]
        interface_aspects = list(dict.fromkeys(interface_aspects))
        if not interface_aspects:
            interface_aspects = [DISCOVERY_ASPECT]

        result = []
        sources = [('lxmf.delivery', 'contact')]
        sources += [(aspect, 'interface') for aspect in interface_aspects]
        for aspect, source in sources:
            peers = messenger.list_peers(aspect)
            peers = _enrich_peers_for_aspect(messenger, peers, aspect)
            for peer in peers:
                if not peer.get('last_location'):
                    continue
                peer['source'] = source
                if source == 'interface':
                    info = peer.get('announce_info') or {}
                    peer['interface_type'] = info.get('type') or 'Other'
                else:
                    peer['interface_type'] = None
                result.append(peer)
        return jsonify(result)
    except Exception as e:
        print(f"Errore in get_map_peers: {e}")
        traceback.print_exc()
        return jsonify([])

@app.route('/api/conversation/<dest_hash>')
def get_conversation(dest_hash):
    """Restituisce i messaggi con un peer (dal database)"""
    if not messenger:
        return jsonify([])
    if not _is_hex_hash(dest_hash, (32, 64)):
        return jsonify([])
    
    messages = []
    
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
            if not c.fetchone():
                conn.close()
                return jsonify([])
            
            c.execute('''
                SELECT hash, source_hash, destination_hash, content, title, timestamp,
                       method, packed_size, rssi, snr, q, hops, incoming,
                       raw_hex, raw_ascii, attachments
                FROM messages 
                WHERE source_hash = ? OR destination_hash = ?
                ORDER BY timestamp ASC
            ''', (dest_hash, dest_hash))
            
            rows = c.fetchall()
            conn.close()
        
        print(f"📁 Caricati {len(rows)} messaggi dal database per {dest_hash[:8]}...")
        
        for row in rows:
            hash_msg, source, dest, content, title, ts, method, packed_size, rssi, snr, q, hops, incoming, raw_hex, raw_ascii, attachments_json = row
            
            msg_attachments = []
            if attachments_json:
                try:
                    atts = json.loads(attachments_json)
                    for att in atts:
                        if att['type'] == 'image':
                            msg_attachments.append({
                                'type': 'image',
                                'format': att['format'],
                                'size': att['size']
                            })
                        elif att['type'] == 'audio':
                            msg_attachments.append({
                                'type': 'audio',
                                'mode': att['mode'],
                                'size': att['size']
                            })
                        elif att['type'] == 'file':
                            msg_attachments.append({
                                'type': 'file',
                                'name': att['name'],
                                'size': att['size']
                            })
                except Exception as e:
                    print(f"❌ Errore parsing attachments: {e}")
            
            messages.append({
                'hash': hash_msg,
                'from': source,
                'to': dest,
                'content': content or '',
                'title': title or '',
                'time': ts,
                'incoming': bool(incoming),
                'method': method,
                'packed_size': packed_size,
                'rssi': rssi,
                'snr': snr,
                'q': q,
                'hops': hops,
                'raw_hex': raw_hex,
                'raw_ascii': raw_ascii,
                'attachments': msg_attachments,
                'status': 'delivered' if incoming else 'sent'
            })
        
        messages.sort(key=lambda x: x['time'])
        print(f"✅ Totale messaggi caricati: {len(messages)}")
        
    except Exception as e:
        print(f"❌ Errore caricamento conversazione: {e}")
        traceback.print_exc()
    
    return jsonify(messages)

@app.route('/api/send', methods=['POST'])
def send_message():
    """Invia un messaggio - STANDARD LXMF"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    temp_files = []
    
    try:
        dest = (request.form.get('destination') or '').strip()
        content = (request.form.get('content') or '')[:20000]
        title = (request.form.get('title') or '')[:256]
        file = request.files.get('file')
        
        if not dest:
            return jsonify({'success': False, 'error': 'Destinazione mancante'})
        if not _is_hex_hash(dest, (32, 64)):
            return jsonify({'success': False, 'error': 'Destinazione non valida'}), 400
        
        image_path = None
        audio_path = None
        file_paths = None
        file_type = request.form.get('type', 'file')
        if file_type not in ('image', 'audio', 'file'):
            file_type = 'file'
        
        if file:
            upload_dir = os.path.expanduser("~/.rns_manager/uploads")
            os.makedirs(upload_dir, exist_ok=True)
            
            unique_id = str(uuid.uuid4())[:8]
            original_filename = _safe_display_name(file.filename, 'file.bin')
            safe_basename = secure_filename(original_filename) or 'file.bin'
            safe_filename = f"{int(time.time())}_{unique_id}_{safe_basename}"
            upload_root = os.path.realpath(upload_dir)
            temp_path = os.path.realpath(os.path.join(upload_dir, safe_filename))
            if not temp_path.startswith(upload_root + os.sep):
                return jsonify({'success': False, 'error': 'Nome file non valido'}), 400
            file.save(temp_path)
            temp_files.append(temp_path)
            
            if file_type == 'image':
                image_path = temp_path
            elif file_type == 'audio':
                audio_path = temp_path
            else:
                file_paths = [(original_filename, temp_path)]
        
        def on_delivery(info):
            print(f"✅ Consegna riuscita per {info.get('hash')}")
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except:
                        pass
            socketio.emit('delivery_update', info)
        
        def on_failed(info):
            print(f"❌ Invio fallito per {info.get('hash')}")
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except:
                        pass
            socketio.emit('delivery_update', info)
        
        def on_progress(info):
            socketio.emit('progress_update', info)

        callbacks = {
            'delivery': on_delivery, 
            'failed': on_failed,
            'progress': on_progress
        }
        
        result = messenger.send(
            dest, content, title=title,
            image_path=image_path,
            audio_path=audio_path,
            file_attachments=file_paths,
            callbacks=callbacks
        )

        if not result.get('success'):
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except OSError:
                        pass
        
        return jsonify(result)
    
    except Exception as e:
        traceback.print_exc()
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except:
                    pass
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/command', methods=['POST'])
def send_command():
    """Invia un comando (PING, ECHO, SIGNAL, RANGETEST)"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    data = request.get_json(silent=True) or {}
    dest = str(data.get('destination') or '').strip()
    cmd = str(data.get('command') or '').strip()
    text = data.get('text')
    
    if not dest or not cmd:
        return jsonify({'success': False, 'error': 'Parametri mancanti'})
    if not _is_hex_hash(dest, (32, 64)):
        return jsonify({'success': False, 'error': 'Destinazione non valida'}), 400
    if text is not None and len(str(text)) > 2048:
        return jsonify({'success': False, 'error': 'Testo troppo lungo'}), 400
    
    cmd_map = {
        'ping': Commands.PING,
        'echo': Commands.ECHO,
        'signal': Commands.SIGNAL_REPORT,
        'rangetest': Commands.CUSTOM,
    }
    
    if cmd not in cmd_map:
        return jsonify({'success': False, 'error': 'Comando sconosciuto'})
    
    cmd_type = cmd_map[cmd]
    if cmd == 'echo':
        cmd_data = text.encode('utf-8') if text else None
    elif cmd == 'rangetest':
        payload = str(text or '').strip()
        if not re.match(r'^\d+\s+(start|stop)$', payload):
            return jsonify({'success': False, 'error': 'Payload rangetest non valido'}), 400
        cmd_data = f'rangetest {payload}'
    else:
        cmd_data = None
    
    result = messenger.send_command(dest, cmd_type, cmd_data)
    return jsonify(result)

@app.route('/api/telemetry/request', methods=['POST'])
def request_telemetry():
    """Richiede telemetria"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    data = request.get_json(silent=True) or {}
    dest = str(data.get('destination') or '').strip()
    
    if not dest:
        return jsonify({'success': False, 'error': 'Destinazione mancante'})
    if not _is_hex_hash(dest, (32, 64)):
        return jsonify({'success': False, 'error': 'Destinazione non valida'}), 400
    
    result = messenger.request_telemetry(dest)
    return jsonify(result)

# ==================== ENDPOINT PER TELEMETRIA ====================

@app.route('/api/telemetry/stats')
def telemetry_stats():
    """Diagnostica richieste/risposte telemetria."""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'}), 404
    try:
        stats = messenger.get_telemetry_stats()
        stats['success'] = True
        return jsonify(stats)
    except Exception as e:
        print(f"❌ Errore statistiche telemetria: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/telemetry/config', methods=['GET', 'POST'])
def handle_telemetry_config():
    """Gestisce la configurazione della telemetria"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'})
    
    if request.method == 'GET':
        config = {
            'location_source': messenger.config.get('location_source', 'fixed'),
            'fixed_location': messenger.config.get('fixed_location', {
                'latitude': 45.6052,
                'longitude': 12.2430,
                'altitude': 12
            }),
            'enable_location': messenger.config.get('enable_location', True),
            'enable_system': messenger.config.get('enable_system', True),
            'gpsd_host': messenger.config.get('gpsd_host', 'localhost'),
            'gpsd_port': messenger.config.get('gpsd_port', 2947),
            'appearance': messenger.config.get('appearance', ['antenna', '4c9aff', '1a1f2e'])
        }
        return jsonify(config)
    
    else:
        try:
            data = request.get_json(silent=True) or {}
            clean = _sanitize_config_update(data)
            messenger.config.update(clean)
            _save_config(messenger)
            
            if hasattr(messenger, 'telemetry_provider'):
                messenger.telemetry_provider.config = messenger.config
            
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

@app.route('/api/telemetry/history/<peer_hash>')
def get_telemetry_history(peer_hash):
    """Restituisce lo storico telemetria per un peer"""
    if not messenger:
        return jsonify([])
    if not _is_hex_hash(peer_hash, (32, 64)):
        return jsonify([])
    
    history = []
    telemetry_dir = messenger.telemetry_dir
    
    if os.path.exists(telemetry_dir):
        for filename in os.listdir(telemetry_dir):
            if filename.startswith(peer_hash) and filename.endswith('.json'):
                filepath = os.path.join(telemetry_dir, filename)
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                        ts = int(filename.split('_')[1].split('.')[0])
                        data['timestamp'] = ts
                        # Compatibilità con vecchi record (valori dict)
                        for key, sub in (('temperature', 'c'),
                                         ('humidity', 'percent_relative'),
                                         ('pressure', 'mbar')):
                            if isinstance(data.get(key), dict):
                                data[key] = data[key].get(sub)
                        history.append(data)
                except Exception as e:
                    print(f"❌ Errore lettura {filename}: {e}")
                    continue
    
    history.sort(key=lambda x: x['timestamp'], reverse=True)
    print(f"📊 Caricati {len(history)} record di telemetria per {peer_hash[:8]}...")
    return jsonify(history[:50])

@app.route('/api/telemetry/test', methods=['POST'])
def test_telemetry():
    """Testa e restituisce i dati telemetria correnti"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'})
    
    if not hasattr(messenger, 'telemetry_provider') or not messenger.telemetry_provider:
        return jsonify({'error': 'Telemetry provider non disponibile'})
    
    try:
        data = messenger.telemetry_provider.get_telemetry_data()
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/peer/<identity_hash>/telemetry/last')
def get_last_telemetry(identity_hash):
    """Restituisce l'ultima telemetria conosciuta di un peer"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'}), 404
    if not _is_hex_hash(identity_hash, (32, 64)):
        return jsonify({'error': 'Hash non valido'}), 400
    
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            c.execute('''
                SELECT last_latitude, last_longitude, last_altitude,
                       last_speed, last_bearing, last_accuracy,
                       last_battery, last_battery_charging,
                       last_temperature, last_humidity, last_pressure,
                       last_light, last_uptime, last_processor,
                       last_ram, last_nvm, last_telemetry_time
                FROM destinations 
                WHERE identity_hash = ? AND aspect = 'lxmf.delivery'
                ORDER BY last_seen DESC LIMIT 1
            ''', (identity_hash,))
            
            row = c.fetchone()
            conn.close()
        
        if not row or not row[16]:
            return jsonify({'error': 'Nessuna telemetria disponibile'}), 404
        
        telemetry = {
            'location': {
                'latitude': row[0],
                'longitude': row[1],
                'altitude': row[2],
                'speed': row[3],
                'bearing': row[4],
                'accuracy': row[5]
            } if row[0] is not None and row[1] is not None else None,
            'battery': {
                'percent': row[6],
                'charging': bool(row[7])
            } if row[6] is not None else None,
            'temperature': row[8],
            'humidity': row[9],
            'pressure': row[10],
            'light': row[11],
            'uptime': row[12],
            'processor': row[13],
            'ram': row[14],
            'nvm': row[15],
            'timestamp': row[16]
        }
        
        return jsonify(telemetry)
        
    except Exception as e:
        print(f"❌ Errore get_last_telemetry: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== ENDPOINT PER RAW ====================

@app.route('/api/raw/history')
def get_raw_history_all():
    """Restituisce lo storico dei pacchetti raw (globale)"""
    if not messenger:
        return jsonify([])
    
    history = []
    try:
        raw_files = sorted(os.listdir(messenger.raw_dir), reverse=True)[:50]
        for filename in raw_files:
            if filename.endswith('.raw'):
                filepath = os.path.join(messenger.raw_dir, filename)
                with open(filepath, 'rb') as f:
                    raw_data = f.read()
                
                json_file = filename.replace('.raw', '.json')
                json_path = os.path.join(messenger.raw_dir, json_file)
                metadata = {}
                if os.path.exists(json_path):
                    with open(json_path, 'r') as jf:
                        metadata = json.load(jf)
                
                history.append({
                    'hash': filename.replace('.raw', ''),
                    'time': metadata.get('time', os.path.getmtime(filepath)),
                    'from': metadata.get('from', '?'),
                    'to': metadata.get('to', '?'),
                    'method': metadata.get('method', '?'),
                    'packed_size': len(raw_data),
                    'raw_hex': raw_data.hex()[:128] + '...' if len(raw_data) > 64 else raw_data.hex(),
                    'raw_ascii': ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw_data[:64])
                })
    except Exception as e:
        print(f"❌ Errore caricamento storico raw: {e}")
    
    return jsonify(history)

@app.route('/api/raw/history/<dest_hash>')
def get_raw_history(dest_hash):
    """Restituisce lo storico dei raw per una conversazione"""
    if not messenger:
        return jsonify([])
    if not _is_hex_hash(dest_hash, (32, 64)):
        return jsonify([])
    
    try:
        conn = sqlite3.connect(messenger.peers_db)
        c = conn.cursor()
        
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if not c.fetchone():
            conn.close()
            return jsonify([])
        
        c.execute('''
            SELECT hash, raw_hex, raw_ascii, timestamp, source_hash, method, packed_size
            FROM messages 
            WHERE (source_hash = ? OR destination_hash = ?) 
              AND raw_hex IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 50
        ''', (dest_hash, dest_hash))
        
        rows = c.fetchall()
        conn.close()
        
        raw_messages = []
        for row in rows:
            raw_messages.append({
                'hash': row[0],
                'raw_hex': row[1],
                'raw_ascii': row[2],
                'time': row[3],
                'from': row[4],
                'method': row[5],
                'packed_size': row[6]
            })
        return jsonify(raw_messages)
    except Exception as e:
        print(f"❌ Errore get_raw_history: {e}")
        return jsonify([])

@app.route('/api/raw/<hash>')
def get_raw(hash):
    """Restituisce il raw di un messaggio"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'})
    if not _is_hex_hash(hash, (64,)):
        return jsonify({'error': 'Hash non valido'}), 400
    
    if hasattr(messenger, 'received_raw') and hash in messenger.received_raw:
        data = messenger.received_raw[hash]
        return jsonify({
            'hex': data['hex'],
            'ascii': data['ascii'],
            'source': 'memory'
        })
    
    hex_path = os.path.join(messenger.raw_dir, f"{hash}.hex")
    raw_path = os.path.join(messenger.raw_dir, f"{hash}.raw")
    
    if os.path.exists(hex_path):
        with open(hex_path, 'r') as f:
            hex_data = f.read().strip()
        
        if os.path.exists(raw_path):
            with open(raw_path, 'rb') as f:
                raw = f.read()
            ascii_data = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw)
        else:
            ascii_data = None
        
        return jsonify({
            'hex': hex_data,
            'ascii': ascii_data,
            'source': 'disk'
        })
    elif os.path.exists(raw_path):
        with open(raw_path, 'rb') as f:
            raw = f.read()
        hex_data = raw.hex()
        ascii_data = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw)
        return jsonify({
            'hex': hex_data,
            'ascii': ascii_data,
            'source': 'disk'
        })
    
    return jsonify({'error': 'Messaggio non trovato'}), 404

# ==================== ENDPOINT PER CONFIG ====================

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    """Gestisce la configurazione"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'})
    
    if request.method == 'GET':
        config = messenger.config
        config['codec2_available'] = CODEC2_AVAILABLE
        return jsonify(config)
    else:
        try:
            data = request.get_json(silent=True) or {}
            clean = _sanitize_config_update(data)
            messenger.config.update(clean)
            _save_config(messenger)
            
            if 'propagation_node' in clean:
                if hasattr(messenger, 'set_propagation_node'):
                    messenger.set_propagation_node(clean['propagation_node'])
                else:
                    node_hash = clean['propagation_node']
                    messenger.propagation_node = bytes.fromhex(node_hash) if node_hash else None
                    if node_hash:
                        messenger.router.set_outbound_propagation_node(messenger.propagation_node)

            if 'announce_aspects' in clean and hasattr(messenger, 'set_announce_aspects'):
                messenger.set_announce_aspects(clean['announce_aspects'])
            
            return jsonify({'success': True, 'announce_aspects': messenger.config.get('announce_aspects')})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

@app.route('/api/propagation', methods=['POST'])
def update_propagation_node():
    """Imposta o rimuove immediatamente il nodo propagation configurato."""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'}), 404
    data = request.get_json(silent=True) or {}
    raw = data.get('node', data.get('propagation_node'))
    try:
        node_hash = _normalize_propagation_node(raw)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400

    try:
        if hasattr(messenger, 'set_propagation_node'):
            current = messenger.set_propagation_node(node_hash)
        else:
            current = node_hash
            messenger.propagation_node = bytes.fromhex(node_hash) if node_hash else None
            if hasattr(messenger, 'router') and node_hash:
                messenger.router.set_outbound_propagation_node(messenger.propagation_node)
        messenger.config['propagation_node'] = current
        _save_config(messenger)
        return jsonify({
            'success': True,
            'propagation_node': current,
            'label': 'Nodo propagation impostato' if current else 'Nodo propagation rimosso',
        })
    except Exception as e:
        print(f"❌ Errore impostazione propagation: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sync', methods=['POST'])
def sync_messages():
    """Sincronizza messaggi dal propagation node"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    result = messenger.sync_messages()
    
    if result['success']:
        socketio.emit('sync_complete', {'new': result['new']})
    
    return jsonify(result)

@app.route('/api/announce/stats')
def announce_stats():
    """Diagnostica announce: eventi ricevuti, handler e dati DB."""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'}), 404
    try:
        stats = messenger.get_announce_stats()
        stats['success'] = True
        return jsonify(stats)
    except Exception as e:
        print(f"❌ Errore statistiche announce: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/announce', methods=['POST'])
def send_announce():
    """Invia annuncio"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'}), 404

    try:
        messenger.announce()
        return jsonify({'success': True, 'identity': messenger.get_hash()})
    except Exception as e:
        print(f"❌ Errore invio annuncio: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/peers/import', methods=['POST'])
def import_peers():
    """Importa peer dalla cache"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    count = messenger.import_peers_from_cache()
    return jsonify({'success': True, 'count': count})

def _cleanup_messenger():
    """Ripulisce il messenger corrente. Usata da shutdown e da exit."""
    global messenger
    try:
        if messenger:
            print("🛑 Arresto del messenger in corso...")

            messenger.delivery_callbacks.clear()
            messenger.failed_callbacks.clear()
            messenger.progress_callbacks.clear()

            messenger.cleanup()

            messenger = None

            time.sleep(1.0)

            print("🧹 Messenger e risorse pulite correttamente")

        return True, None
    except Exception as e:
        print(f"❌ Errore durante lo shutdown: {e}")
        traceback.print_exc()
        return False, str(e)


@app.route('/api/shutdown', methods=['POST'])
def shutdown_messenger():
    """Pulisce le risorse PRIMA di inizializzare una nuova identità"""
    ok, error = _cleanup_messenger()
    if ok:
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': error})


@app.route('/api/exit', methods=['POST'])
def exit_app():
    """Arresta completamente il server (pulsante ESCI)."""
    if os.environ.get('LXMF_EXIT_DRY_RUN') == '1':
        return jsonify({'success': True, 'dry_run': True})

    ok, error = _cleanup_messenger()
    if not ok:
        return jsonify({'success': False, 'error': error}), 500

    def _stop_process():
        time.sleep(0.5)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(0)

    threading.Thread(target=_stop_process, daemon=True).start()
    return jsonify({'success': True})

@app.route('/api/cleanup_temp', methods=['POST'])
def cleanup_temp_files():
    """Pulisce i file temporanei più vecchi di 1 ora"""
    try:
        upload_dir = os.path.expanduser("~/.rns_manager/uploads")
        if not os.path.exists(upload_dir):
            return jsonify({'success': True, 'cleaned': 0})
        
        now = time.time()
        count = 0
        for f in os.listdir(upload_dir):
            fpath = os.path.join(upload_dir, f)
            if os.path.isfile(fpath):
                if now - os.path.getmtime(fpath) > 3600:
                    os.unlink(fpath)
                    count += 1
        
        return jsonify({'success': True, 'cleaned': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ==================== ENDPOINT PER ATTACHMENT ====================

def _pcm_to_wav_bytes(pcm, sample_rate=8000):
    """Crea un WAV PCM 16-bit mono in memoria."""
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


@lru_cache(maxsize=16)
def _transcode_to_ogg(audio_bytes, suffix='.webm'):
    """Converte un contenitore audio (es. WebM/Opus) in Ogg/Opus."""
    input_path = None
    output_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as source:
            source.write(audio_bytes)
            input_path = source.name
        output_path = input_path + '.ogg'
        result = subprocess.run(
            [
                'ffmpeg', '-hide_banner', '-loglevel', 'error',
                '-i', input_path, '-vn', '-c:a', 'libopus', '-b:a', '32k',
                '-ar', '48000', '-ac', '1', '-f', 'ogg', '-y', output_path,
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0 and os.path.exists(output_path):
            with open(output_path, 'rb') as output:
                converted = output.read()
            if converted:
                return converted
    except Exception as exc:
        print(f"⚠️ Conversione audio in Ogg fallita: {exc}")
    finally:
        for path in (input_path, output_path):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
    return None


def _prepare_audio_attachment(audio_bytes, mode):
    """Restituisce (bytes, mime, filename) riproducibili dal browser."""
    try:
        mode = int(mode)
    except (TypeError, ValueError):
        mode = 16
    if mode < 16:
        try:
            pcm = audio_codec.decode_codec2(audio_bytes, mode)
            if pcm:
                return _pcm_to_wav_bytes(pcm), 'audio/wav', f'audio_c2_{mode}.wav'
        except Exception as exc:
            print(f"⚠️ Decodifica Codec2 mode={mode} fallita: {exc}")
        return audio_bytes, 'application/octet-stream', f'audio_c2_{mode}.c2'

    if audio_bytes.startswith(b'OggS'):
        return audio_bytes, 'audio/ogg', 'audio.opus'

    # Alcuni client (es. Sideband) inviano Opus dentro un contenitore WebM.
    if audio_bytes.startswith(b'\x1a\x45\xdf\xa3'):
        converted = _transcode_to_ogg(audio_bytes, '.webm')
        if converted:
            return converted, 'audio/ogg', 'audio.opus'
        return audio_bytes, 'audio/webm', 'audio.webm'

    return audio_bytes, 'application/octet-stream', 'audio.opus'


@app.route('/api/attachment/<hash>')
def get_attachment(hash):
    """Recupera un attachment dal database per visualizzazione/download"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'}), 404
    if not _is_hex_hash(hash, (64,)):
        return jsonify({'error': 'Hash non valido'}), 400
    
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
            if not c.fetchone():
                conn.close()
                return jsonify({'error': 'Tabella non trovata'}), 404
            
            c.execute('SELECT attachments FROM messages WHERE hash = ?', (hash,))
            row = c.fetchone()
            conn.close()
        
        if not row or not row[0]:
            return jsonify({'error': 'Attachment non trovato'}), 404
        
        attachments = json.loads(row[0])
        
        if not attachments:
            return jsonify({'error': 'Nessun attachment'}), 404
        
        att = attachments[0]
        
        file_bytes = None
        filename = None
        mime_type = None
        is_inline = False
        
        if isinstance(att, dict):
            if 'bytes' in att:
                file_bytes = bytes.fromhex(att['bytes'])
                
                if att.get('type') == 'image':
                    img_format = str(att.get('format') or 'jpg').lower()
                    if img_format in IMAGE_FORMAT_MIME:
                        mime_type = IMAGE_FORMAT_MIME[img_format]
                        filename = f"image.{img_format}"
                        is_inline = True
                    else:
                        mime_type = 'application/octet-stream'
                        filename = 'image.bin'
                        is_inline = False
                
                elif att.get('type') == 'audio':
                    mode = att.get('mode', 16)
                    file_bytes, mime_type, filename = _prepare_audio_attachment(file_bytes, mode)
                    is_inline = mime_type in ('audio/wav', 'audio/ogg', 'audio/webm')
                
                elif att.get('type') == 'file':
                    mime_type = "application/octet-stream"
                    filename = secure_filename(str(att.get('name') or 'file.bin')) or 'file.bin'
        
        elif isinstance(att, list) and len(att) == 2:
            att_type = att[0]
            att_data = att[1]
            
            if 'bytes' in att_data:
                file_bytes = bytes.fromhex(att_data['bytes'])
                
                if att_type == 'image':
                    img_format = str(att_data.get('format') or 'jpg').lower()
                    if img_format in IMAGE_FORMAT_MIME:
                        mime_type = IMAGE_FORMAT_MIME[img_format]
                        filename = f"image.{img_format}"
                        is_inline = True
                    else:
                        mime_type = 'application/octet-stream'
                        filename = 'image.bin'
                        is_inline = False
                
                elif att_type == 'audio':
                    mode = att_data.get('mode', 16)
                    file_bytes, mime_type, filename = _prepare_audio_attachment(file_bytes, mode)
                    is_inline = mime_type in ('audio/wav', 'audio/ogg', 'audio/webm')
                
                elif att_type == 'file':
                    mime_type = "application/octet-stream"
                    filename = secure_filename(str(att_data.get('name') or 'file.bin')) or 'file.bin'
        
        if not file_bytes:
            return jsonify({'error': 'Formato attachment non supportato'}), 400
        
        from flask import Response
        disposition = "inline" if is_inline else "attachment"
        safe_filename = secure_filename(filename) or 'attachment.bin'
        return Response(
            file_bytes,
            mimetype=mime_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{safe_filename}"',
                "Content-Length": str(len(file_bytes)),
                "Cache-Control": "public, max-age=3600"
            }
        )
        
    except Exception as e:
        print(f"❌ Errore get_attachment: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ==================== ENDPOINT PER PREFERITI ====================

@app.route('/api/favorites', methods=['GET'])
def get_favorites():
    """Restituisce la lista degli identity_hash preferiti."""
    if not messenger:
        return jsonify([])
    try:
        return jsonify(messenger.list_favorite_hashes())
    except Exception as e:
        print(f"❌ Errore get_favorites: {e}")
        return jsonify([])


@app.route('/api/favorites/add', methods=['POST'])
def add_favorite():
    """Aggiunge un preferito."""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})

    data = request.get_json(silent=True) or {}
    peer_hash = str(data.get('peer_hash') or '').strip()
    if not _is_hex_hash(peer_hash, (32, 64)):
        return jsonify({'success': False, 'error': 'peer_hash non valido'}), 400

    try:
        found = messenger.set_peer_favorite(peer_hash, True)
        if not found:
            return jsonify({'success': False, 'error': 'Peer non trovato'}), 404
        return jsonify({'success': True, 'favorite': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/favorites/remove', methods=['POST'])
def remove_favorite():
    """Rimuove un preferito."""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})

    data = request.get_json(silent=True) or {}
    peer_hash = str(data.get('peer_hash') or '').strip()
    if not _is_hex_hash(peer_hash, (32, 64)):
        return jsonify({'success': False, 'error': 'peer_hash non valido'}), 400

    try:
        found = messenger.set_peer_favorite(peer_hash, False)
        if not found:
            return jsonify({'success': False, 'error': 'Peer non trovato'}), 404
        return jsonify({'success': True, 'favorite': False})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ==================== ENDPOINT PER GESTIONE PEER ====================

@app.route('/api/peer/<identity_hash>/favorite', methods=['POST'])
def peer_set_favorite(identity_hash):
    """Endpoint usato dalla UI per il toggle dei preferiti."""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    if not _is_hex_hash(identity_hash, (32, 64)):
        return jsonify({'success': False, 'error': 'identity_hash non valido'}), 400

    data = request.get_json(silent=True) or {}
    if 'favorite' not in data:
        return jsonify({'success': False, 'error': 'favorite mancante'}), 400
    favorite = bool(data.get('favorite'))

    try:
        found = messenger.set_peer_favorite(identity_hash, favorite)
        if not found:
            return jsonify({'success': False, 'error': 'Peer non trovato'}), 404
        return jsonify({'success': True, 'favorite': favorite})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/peer/<identity_hash>/groups', methods=['POST'])
def peer_set_groups(identity_hash):
    """Endpoint usato dalla UI per aggiornare i gruppi N:N."""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    if not _is_hex_hash(identity_hash, (32, 64)):
        return jsonify({'success': False, 'error': 'identity_hash non valido'}), 400

    data = request.get_json(silent=True) or {}
    groups = data.get('groups')
    if not isinstance(groups, (list, tuple)):
        return jsonify({'success': False, 'error': 'groups deve essere una lista'}), 400

    try:
        found, valid = messenger.set_peer_groups(identity_hash, groups)
        if not found:
            return jsonify({'success': False, 'error': 'Peer non trovato'}), 404
        return jsonify({'success': True, 'groups': valid})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/groups/reset', methods=['POST'])
def reset_groups():
    """Rimuove tutti i gruppi da tutti i contatti (i preferiti restano)."""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'}), 404
    try:
        result = messenger.reset_all_groups()
        return jsonify({'success': True, **result})
    except Exception as e:
        print(f"Errore reset gruppi: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/peer/update', methods=['POST'])
def update_peer():
    """Aggiorna gruppo, permessi e note di un peer."""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})

    data = request.get_json(silent=True) or {}
    identity_hash = str(data.get('identity_hash') or '').strip()
    if not _is_hex_hash(identity_hash, (32, 64)):
        return jsonify({'success': False, 'error': 'identity_hash non valido'}), 400

    try:
        found = messenger.update_peer_settings(identity_hash, data)
        if not found:
            return jsonify({'success': False, 'error': 'Peer non trovato'}), 404
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        print(f"Errore aggiornamento peer: {e}")
        return jsonify({'success': False, 'error': str(e)})


# ==================== SOCKET.IO EVENTS ====================

@socketio.on('connect')
def handle_connect():
    if AUTH_TOKEN and not _auth_ok():
        print('❌ Socket.IO: autenticazione fallita')
        return False
    print('✅ Client web connesso')
    emit('connected', {'status': 'ok', 'identity': messenger.get_hash() if messenger else None})

@socketio.on('disconnect')
def handle_disconnect():
    print('❌ Client web disconnesso')

@socketio.on('ping_server')
def handle_ping():
    emit('pong', {'time': time.time()})

# ==================== PARSER ARGOMENTI ====================

def parse_arguments():
    """Parsing degli argomenti da riga di comando"""
    parser = argparse.ArgumentParser(description='LXMF Web Chat Server')
    parser.add_argument('-i', '--identity', 
                        type=str,
                        help='Percorso al file identità da caricare (es: ~/.rns_manager/storage/Prova1)')
    parser.add_argument('-p', '--port',
                        type=int,
                        default=5012,
                        help='Porta su cui avviare il server (default: 5012)')
    parser.add_argument('--host',
                        type=str,
                        default='127.0.0.1',
                        help='Host su cui avviare il server (default: 127.0.0.1)')
    return parser.parse_args()

# ==================== MAIN ====================

if __name__ == '__main__':
    args = parse_arguments()
    
    upload_dir = os.path.expanduser("~/.rns_manager/uploads")
    os.makedirs(upload_dir, exist_ok=True)
    
    if args.identity:
        print(f"🔑 Tentativo di caricamento identità: {args.identity}")
        
        if os.path.exists(args.identity):
            identity_file = os.path.basename(args.identity)
            storagepath = os.path.expanduser("~/.rns_manager/storage")
            
            expected_path = os.path.join(storagepath, identity_file)
            if os.path.exists(expected_path):
                print(f"✅ Trovato file identità: {expected_path}")
                
                def init_identity():
                    time.sleep(2)
                    print(f"🔄 Inizializzazione automatica con identità: {identity_file}")
                    from flask import request
                    with app.test_request_context():
                        with app.test_client() as client:
                            response = client.post('/api/identity/select', 
                                                  json={'identity': identity_file, 'name': ''})
                            if response.status_code == 200:
                                data = response.get_json()
                                if data.get('success'):
                                    print(f"✅ Identità {identity_file} caricata automaticamente")
                                    socketio.emit('identity_loaded', data)
                                else:
                                    print(f"❌ Errore caricamento identità: {data.get('error')}")
                            else:
                                print(f"❌ Errore HTTP: {response.status_code}")
                
                thread = threading.Thread(target=init_identity)
                thread.daemon = True
                thread.start()
            else:
                print(f"❌ Il file {identity_file} non si trova in {storagepath}")
                print(f"   Copia il file in {storagepath} o usa il selettore nel browser")
        else:
            print(f"❌ File non trovato: {args.identity}")
    
    port = args.port
    host = args.host
    
    print("="*50)
    print(f"🚀 LXMF WEB SERVER v{APP_VERSION}")
    print("="*50)
    print(f"📡 Server in ascolto su http://{host}:{port}")
    if args.identity:
        print(f"🔑 Identità specificata: {args.identity}")
    print(f"🆔 Identity attuale: {messenger.get_hash() if messenger else 'N/D'}")
    print(f"📛 Nome: {messenger.display_name if messenger else 'N/D'}")
    print(f"📁 Upload directory: {upload_dir}")
    print("="*50)
    print("Premi CTRL+C per fermare il server")
    print("="*50)
    
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)