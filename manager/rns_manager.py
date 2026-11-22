#!/usr/bin/env python3 
import subprocess
import os
import uuid
import tempfile
import shutil
import binascii
import base64
import shlex
import time
import threading
import queue
import json
import re
import multiprocessing
import socket
import signal
import sys
import webbrowser
import hmac
import ipaddress
from urllib.parse import urlparse
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from flask import Flask, request, jsonify, render_template, Response, stream_with_context, g

# Importa il modulo monitor
import modules.rns_monitor as rns_monitor

# ============================================
# === LEGGI VERSIONE DA version.py ===
# ============================================
try:
    from version import APP_VERSION, APP_NAME, APP_AUTHOR, APP_URL, DONATION_ADDRESSES
except ImportError:
    APP_VERSION = "1.2.0"
    APP_NAME = "RNS Manager"
    APP_AUTHOR = "argo79"
    APP_URL = "https://github.com/argo79/RNS-Manager"
    DONATION_ADDRESSES = {}

print(f"[✓] Versione: {APP_VERSION}")

# ============================================
# === UTILITY PER TROVARE PERCORSI ===
# ============================================

def get_base_dir():
    """Restituisce la directory dove si trova l'eseguibile o lo script."""
    if getattr(sys, 'frozen', False):
        # Siamo in un eseguibile PyInstaller (.run)
        return os.path.dirname(sys.executable)
    else:
        # Siamo in uno script Python normale
        return os.path.dirname(os.path.abspath(__file__))

def get_rnid_path():
    """Restituisce il percorso del comando rnid."""
    return shutil.which('rnid') or 'rnid'

def get_rnpath_path():
    """Restituisce il percorso del comando rnpath."""
    return shutil.which('rnpath') or 'rnpath'

def get_rnprobe_path():
    """Restituisce il percorso del comando rnprobe."""
    return shutil.which('rnprobe') or 'rnprobe'

def get_rnstatus_path():
    """Restituisce il percorso del comando rnstatus."""
    return shutil.which('rnstatus') or 'rnstatus'


def check_rns_commands():
    """Verifica che i comandi RNS essenziali siano disponibili."""
    missing = []
    commands = {
        'rnid': get_rnid_path(),
        'rnpath': get_rnpath_path(),
        'rnprobe': get_rnprobe_path(),
        'rnstatus': get_rnstatus_path()
    }
    
    for cmd, path in commands.items():
        # Se il percorso è solo il nome del comando (senza percorso assoluto) e non esiste come eseguibile
        if path == cmd and shutil.which(cmd) is None:
            missing.append(cmd)
    
    return missing

def print_rns_warning(missing_commands):
    """Stampa un avviso dettagliato se mancano i comandi RNS."""
    print("\n" + "=" * 60)
    print("⚠️  ATTENZIONE: Comandi RNS non trovati!")
    print("=" * 60)
    print("\nI seguenti comandi non sono disponibili nel sistema:")
    for cmd in missing_commands:
        print(f"  ❌ {cmd}")
    print("\nPer far funzionare correttamente l'applicazione, è necessario")
    print("installare Reticulum Network Stack.")
    print("\n👉 Per installare, esegui:")
    print("    pip install rns")
    print("\nOppure, se usi un ambiente virtuale:")
    print("    source venv/bin/activate")
    print("    pip install rns")
    print("\nDopo l'installazione, riavvia l'applicazione.")
    print("=" * 60)
    print()

# ============================================
# === CARTELLA LOCALE DI ESECUZIONE ===
# ============================================

BASE_DIR = get_base_dir()
LOCAL_TMP = os.path.join(BASE_DIR, "tmp")
LOCAL_DOWNLOADS = os.path.join(BASE_DIR, "downloads")
# Cache condivisa con il Messenger: ~/.rns_manager/cache/announces.db
SHARED_DATA_DIR = os.path.expanduser("~/.rns_manager")
CUSTOM_CACHE_DIR = os.environ.get("RNS_MANAGER_CACHE_DIR")
LOCAL_CACHE = CUSTOM_CACHE_DIR or os.path.join(SHARED_DATA_DIR, "cache")
LEGACY_CACHE = os.path.join(BASE_DIR, "cache")

# Crea tutte le cartelle necessarie
os.makedirs(LOCAL_TMP, exist_ok=True)
os.makedirs(LOCAL_DOWNLOADS, exist_ok=True)
os.makedirs(LOCAL_CACHE, exist_ok=True)

# Migra la vecchia cache locale (manager/cache) verso quella condivisa
_legacy_db = os.path.join(LEGACY_CACHE, "announces.db")
_shared_db = os.path.join(LOCAL_CACHE, "announces.db")
if not CUSTOM_CACHE_DIR and os.path.exists(_legacy_db) and not os.path.exists(_shared_db):
    try:
        shutil.copy2(_legacy_db, _shared_db)
        print(f"[✓] Cache migrata in {_shared_db}")
    except OSError as e:
        print(f"[!] Impossibile migrare la cache: {e}")

print(f"[DEBUG] BASE_DIR: {BASE_DIR}")
print(f"[DEBUG] LOCAL_TMP: {LOCAL_TMP}")
print(f"[DEBUG] LOCAL_DOWNLOADS: {LOCAL_DOWNLOADS}")
print(f"[DEBUG] LOCAL_CACHE: {LOCAL_CACHE}")

def _unlink(*paths):
    """Rimuove uno o più file senza passare dalla shell."""
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


def _relocate(source, destination, action='move'):
    """Sposta o copia un file limitando i percorsi consentiti.

    La sorgente può essere in LOCAL_TMP o LOCAL_DOWNLOADS; la destinazione
    deve essere in LOCAL_DOWNLOADS. Nessuna shell, nessun path arbitrario.
    """
    src = os.path.realpath(source)
    dst = os.path.realpath(destination)

    tmp = os.path.realpath(LOCAL_TMP)
    dld = os.path.realpath(LOCAL_DOWNLOADS)

    if not (src.startswith(tmp + os.sep) or src.startswith(dld + os.sep)):
        raise ValueError('Sorgente non consentita')
    if not dst.startswith(dld + os.sep):
        raise ValueError('Destinazione non consentita')
    if os.path.isdir(src):
        raise ValueError('La sorgente non può essere una directory')
    if os.path.isdir(dst):
        raise ValueError('La destinazione non può essere una directory')

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if action == 'copy':
        shutil.copy2(src, dst)
    else:
        shutil.move(src, dst)
    return dst


def _safe_identity_name(raw_name, fallback='identity'):
    """Restituisce un nome file identità sicuro (niente path, niente traversal)."""
    raw_name = (raw_name or '').strip()
    base = os.path.basename(raw_name.replace(chr(92), '/'))
    base = secure_filename(base)
    if not base or base in ('.', '..'):
        base = secure_filename(fallback) or 'identity'
    return base[:120]


def _identity_dest_path(name):
    """Percorso di destinazione garantito dentro RNS_MANAGER_STORAGE."""
    root = os.path.realpath(RNS_MANAGER_STORAGE)
    path = os.path.realpath(os.path.join(root, name))
    if not path.startswith(root + os.sep):
        raise ValueError('Percorso identità non consentito')
    return path


def _identity_roots():
    roots = []
    for value in (RETICULUM_STORAGE, NOMADNET_STORAGE, LXMF_STORAGE, RNS_MANAGER_STORAGE,
                  RNPHONE_STORAGE, MESHCHAT_STORAGE):
        if value:
            roots.append(os.path.realpath(value))
    return roots


def _is_allowed_identity_path(path):
    """True solo se il path è dentro una delle directory identità note."""
    if not isinstance(path, str) or not path:
        return False
    real = os.path.realpath(os.path.expanduser(path))
    for root in _identity_roots():
        if real.startswith(root + os.sep) and os.path.isfile(real):
            return True
    return False


def _hex_arg(value, min_len=4, max_len=64):
    """Valida un hash/prefix esadecimale."""
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(rf'[0-9a-fA-F]{{{min_len},{max_len}}}', value))


_RNID_ALLOWED_FLAGS = {
    '-h', '--help', '-R', '-i', '-H', '-a', '-e', '-d', '-s', '-V',
    '-x', '-X', '-b', '-B', '-P', '-F', '-f', '--print-identity',
}

# ============================================
# === PERCORSI CONFIGURATI DALL'UTENTE ===
# ============================================

USER_DIRECTORIES = {
    'reticulum': "~/.reticulum",
    'nomadnet': "~/.nomadnetwork", 
    'lxmf': "~/.lxmf",
    'rnphone': "~/.rnphone",
    'meshchat': "~/.reticulum-meshchat",
    'rns_manager': "~/.rns_manager"
}

# === CODICE ORIGINALE - AUTOMATICO ===
execution_vars = {}
for key, path in USER_DIRECTORIES.items():
    expanded = os.path.expanduser(path)
    execution_vars[f"{key.upper()}_DIR"] = expanded
    
    if key == 'rnphone' or key == 'meshchat':
        execution_vars[f"{key.upper()}_STORAGE"] = expanded if os.path.exists(expanded) else None
    else:
        storage_path = os.path.join(expanded, "storage")
        execution_vars[f"{key.upper()}_STORAGE"] = storage_path
    
    if expanded:
        os.makedirs(expanded, exist_ok=True)
        if key != 'rnphone' and key != 'meshchat':
            os.makedirs(storage_path, exist_ok=True)

globals().update(execution_vars)

# === USA CARTELLE LOCALI PER DOWNLOAD E CACHE ===
DOWNLOADS_DIR = LOCAL_DOWNLOADS
CACHE_DIR = LOCAL_CACHE

print(f"[✓] Downloads: {DOWNLOADS_DIR}")
print(f"[✓] Cache: {CACHE_DIR}")

# ============================================
# === APP FLASK ===
# ============================================

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB

_LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}
AUTH_TOKEN = os.environ.get("RNS_AUTH_TOKEN", "")


def _auth_ok():
    """Auth token opzionale (env RNS_AUTH_TOKEN)."""
    if not AUTH_TOKEN:
        return True
    supplied = request.headers.get('X-Auth-Token') or request.cookies.get('rns_auth')
    if not supplied:
        supplied = request.args.get('token', '')
        if supplied:
            g.set_auth_cookie = True
    return bool(supplied) and hmac.compare_digest(str(supplied), AUTH_TOKEN)


def _request_host():
    """Host della richiesta senza porta (supporta IPv6)."""
    parsed = urlparse('//' + (request.host or ''))
    return (parsed.hostname or '').lower()


def _host_is_allowed(host):
    # Solo localhost o indirizzi IP letterali: blocca DNS rebinding via domini.
    if host in _LOCAL_HOSTS:
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
    if host in _LOCAL_HOSTS:
        return origin_host in _LOCAL_HOSTS
    return origin_host == host


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
        response.set_cookie('rns_auth', AUTH_TOKEN, httponly=True, samesite='Strict',
                            secure=request.is_secure, max_age=7 * 86400)
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'no-referrer')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "media-src 'self' blob:; "
        "connect-src 'self'; "
        "worker-src 'self' blob:; "
        "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    )
    return response


@app.errorhandler(RequestEntityTooLarge)
def _request_too_large(_e):
    return jsonify({'success': False, 'error': 'File troppo grande'}), 413


# ============================================
# === INIZIALIZZA MONITOR RNS ===
# ============================================

# Socket in cartella locale
if getattr(rns_monitor, 'SOCKET_PATH', None):
    rns_monitor.SOCKET_PATH = os.path.join(BASE_DIR, "rns_monitor.sock")
    print(f"[✓] Socket path: {rns_monitor.SOCKET_PATH}")

# Crea istanza del monitor manager
monitor_manager = rns_monitor.RNSMonitorManager(
    socket_path=rns_monitor.SOCKET_PATH,
    aspects=rns_monitor.RNS_ASPECTS,
    cache_dir=CACHE_DIR,
    host=rns_monitor.SOCKET_HOST,
    port=rns_monitor.SOCKET_PORT
)

# Avvia processi (può essere disabilitato per WSGI/multi-worker o test)
SKIP_MONITOR = os.environ.get('RNS_MANAGER_SKIP_MONITOR', '0') == '1'
if not SKIP_MONITOR:
    monitor_manager.start_monitor_process()
    monitor_manager.start_listener()
else:
    print("[i] Monitor process/listener disabilitati (RNS_MANAGER_SKIP_MONITOR=1)")

# Registra blueprint del monitor
app.register_blueprint(rns_monitor.create_monitor_blueprint(monitor_manager))

# Route di redirect per compatibilità con /monitor
@app.route('/monitor')
def redirect_monitor():
    from flask import redirect
    return redirect('/api/monitor')

# ============================================
# === CACHE IDENTITÀ (server-side) ===
# ============================================

class IdentityCache:
    """Cache server-side per le identità"""
    def __init__(self, cache_duration=300):
        self.cache = {}
        self.timestamps = {}
        self.cache_duration = cache_duration
        self.lock = threading.Lock()
        
    def get(self, key='all_identities'):
        with self.lock:
            if key in self.cache:
                age = time.time() - self.timestamps.get(key, 0)
                if age < self.cache_duration:
                    print(f"[Cache ID] Hit per {key} (età: {age:.1f}s)")
                    return self.cache[key]
                else:
                    print(f"[Cache ID] Scaduta per {key} (età: {age:.1f}s)")
            return None
    
    def set(self, data, key='all_identities'):
        with self.lock:
            self.cache[key] = data
            self.timestamps[key] = time.time()
            print(f"[Cache ID] Salvati {len(data)} elementi per {key}")
    
    def clear(self, key=None):
        with self.lock:
            if key:
                self.cache.pop(key, None)
                self.timestamps.pop(key, None)
                print(f"[Cache ID] Pulito {key}")
            else:
                self.cache.clear()
                self.timestamps.clear()
                print(f"[Cache ID] Pulita tutta la cache")
    
    def get_stats(self, key='all_identities'):
        with self.lock:
            if key in self.cache:
                age = time.time() - self.timestamps[key]
                return {
                    'exists': True,
                    'age': age,
                    'size': len(self.cache[key]),
                    'timestamp': self.timestamps[key]
                }
            return {'exists': False}

# Inizializza cache identità
IDENTITY_CACHE_TTL = int(os.environ.get("RNS_IDENTITY_CACHE_TTL", "300"))
identity_cache = IdentityCache(cache_duration=IDENTITY_CACHE_TTL)

# ============================================
# === TUTTE LE ROUTE IDENTITY MANAGER ===
# ============================================

@app.route('/')
def index():
    missing = check_rns_commands()
    return render_template('index.html', 
                         version=APP_VERSION,
                         author=APP_AUTHOR,
                         repo_url=APP_URL,
                         donations=DONATION_ADDRESSES,
                         rns_missing=missing)

@app.route('/api/rnid', methods=['POST'])
def execute_rnid():
    try:
        data = request.get_json(silent=True) or {}
        command = str(data.get('command', '')).strip()
        
        if not command:
            return jsonify({'success': False, 'error': 'No command provided'})
        
        if command.startswith('rnid'):
            try:
                parts = shlex.split(command)
            except:
                parts = command.split()
            
            if parts and parts[0] == 'rnid':
                parts = parts[1:]

            for token in parts:
                if token.startswith('-') and token.split('=', 1)[0] not in _RNID_ALLOWED_FLAGS:
                    return jsonify({
                        'success': False,
                        'error': f'Opzione rnid non consentita: {token.split("=", 1)[0]}'
                    }), 403
            
            rnid_cmd = get_rnid_path()
            result = subprocess.run(
                [rnid_cmd] + parts,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            stdout = result.stdout
            stderr = result.stderr
            
            # PULIZIA COMPLETA
            stdout = re.sub(r'.\x08', '', stdout)
            stdout = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stdout)
            stdout = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', stdout)
            stdout = re.sub(r'[\u2800-\u28FF]', '', stdout)      # Rimuove caratteri braille
            stdout = re.sub(r'[\u2580-\u259F]', '', stdout)      # Rimuove caratteri blocchi
            
            stderr = re.sub(r'.\x08', '', stderr)
            stderr = re.sub(r'[\u2800-\u28FF]', '', stderr)      # Rimuove caratteri braille
            stderr = re.sub(r'[\u2580-\u259F]', '', stderr)      # Rimuove caratteri blocchi
            
            if len(stdout) > 1000 or '4U+6vC' in stdout:
                lines = []
                for line in stdout.split('\n'):
                    if len(line) > 80:
                        for i in range(0, len(line), 80):
                            lines.append(line[i:i+80])
                    else:
                        lines.append(line)
                stdout = '\n'.join(lines)
            
            return jsonify({
                'success': result.returncode == 0,
                'output': stdout,
                'error': stderr,
                'return_code': result.returncode
            })
            
        else:
            return jsonify({'success': False, 'error': 'Comando non permesso (sono ammessi solo comandi rnid)'}), 403
        
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Timeout del comando'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/list', methods=['GET'])
def list_identities():
    force_refresh = request.args.get('force', 'false').lower() == 'true'
    
    if not force_refresh:
        cached = identity_cache.get()
        if cached is not None:
            return jsonify({
                'identities': cached,
                'from_cache': True,
                'cache_age': time.time() - identity_cache.timestamps.get('all_identities', 0)
            })
    
    print(f"[Cache ID] Scansione completa delle identità (force={force_refresh})")
    identities = []
    
    storage_dirs = [
        (RETICULUM_STORAGE, 'reticulum'),
        (NOMADNET_STORAGE, 'nomadnet'),
        (LXMF_STORAGE, 'lxmf'),
        (RNS_MANAGER_STORAGE, 'rns_manager')
    ]
    
    if RNPHONE_STORAGE and os.path.exists(RNPHONE_STORAGE):
        storage_dirs.append((RNPHONE_STORAGE, 'rnphone'))

    if MESHCHAT_STORAGE and os.path.exists(MESHCHAT_STORAGE):
        storage_dirs.append((MESHCHAT_STORAGE, 'meshchat'))
    
    for storage_path, app_name in storage_dirs:
        if storage_path and os.path.exists(storage_path):
            for item in os.listdir(storage_path):
                item_path = os.path.join(storage_path, item)
                
                if os.path.isfile(item_path):
                    file_size = os.path.getsize(item_path)
                    if file_size == 64:
                        identity = {
                            'name': item,
                            'path': item_path,
                            'app': app_name,
                            'size': file_size,
                            'rns_hash': None,
                            'aspect_hashes': {},
                            'valid': False
                        }
                        
                        try:
                            rnid_cmd = get_rnid_path()
                            result = subprocess.run(
                                [rnid_cmd, '-i', item_path, '--print-identity'],
                                capture_output=True,
                                text=True,
                                timeout=3
                            )
                            
                            if result.returncode == 0:
                                identity['valid'] = True
                                
                                for line in result.stdout.split('\n'):
                                    if 'Loaded Identity <' in line:
                                        start = line.find('<') + 1
                                        end = line.find('>', start)
                                        if start > 0 and end > start:
                                            identity['rns_hash'] = line[start:end]
                                            break
                            
                            aspects_to_check = rns_monitor.RNS_ASPECTS[:5]
                            for aspect in aspects_to_check:
                                try:
                                    hash_result = subprocess.run(
                                        [rnid_cmd, '-i', item_path, '-H', aspect],
                                        capture_output=True,
                                        text=True,
                                        timeout=2
                                    )
                                    
                                    if hash_result.returncode == 0:
                                        pattern = f"The {aspect} destination for this Identity is <"
                                        for line in hash_result.stdout.split('\n'):
                                            if pattern in line:
                                                start = line.find('<') + 1
                                                end = line.find('>', start)
                                                if start > 0 and end > start:
                                                    hash_value = line[start:end]
                                                    identity['aspect_hashes'][aspect] = hash_value
                                                    break
                                        
                                        if aspect not in identity['aspect_hashes']:
                                            for line in hash_result.stdout.split('\n'):
                                                if 'Destination hash:' in line:
                                                    hash_value = line.split('Destination hash:')[-1].strip()
                                                    identity['aspect_hashes'][aspect] = hash_value
                                                    break
                                except:
                                    continue
                            
                        except Exception:
                            continue
                        
                        identities.append(identity)
    
    identities.sort(key=lambda x: (not x['valid'], x['name']))
    
    identity_cache.set(identities)
    
    return jsonify({
        'identities': identities,
        'from_cache': False,
        'cache_saved': True
    })

# ============================================
# === ENDPOINT GESTIONE CACHE IDENTITÀ ===
# ============================================

@app.route('/api/cache/identities/clear', methods=['POST'])
def cache_identities_clear():
    identity_cache.clear('all_identities')
    return jsonify({
        'success': True,
        'message': 'Cache identità pulita'
    })

@app.route('/api/cache/identities/status')
def cache_identities_status():
    stats = identity_cache.get_stats()
    return jsonify({
        'success': True,
        'cache': stats
    })

@app.route('/api/cache/identities/refresh', methods=['POST'])
def cache_identities_refresh():
    identity_cache.clear('all_identities')
    return jsonify({
        'success': True,
        'message': 'Cache invalidata, prossima richiesta farà scansione'
    })

# ============================================
# === TUTTE LE ALTRE ROUTE IDENTITY ===
# ============================================

@app.route('/api/identities/import/file', methods=['POST'])
def import_identity_file():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'})
        
        suggested_name = _safe_identity_name(
            request.form.get('suggested_name', '') or file.filename,
            f"imported_{int(time.time())}"
        )
        dest_path = _identity_dest_path(suggested_name)
        if os.path.exists(dest_path):
            return jsonify({'success': False, 'error': f"Esiste già un'identità chiamata {suggested_name}"})
        file.save(dest_path)
        
        file_size = os.path.getsize(dest_path)
        if file_size != 64:
            os.remove(dest_path)
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        rnid_cmd = get_rnid_path()
        try:
            result = subprocess.run(
                [rnid_cmd, '-i', dest_path, '--print-identity'],
                capture_output=True,
                text=True,
                timeout=5
            )
        except Exception as e:
            _unlink(dest_path)
            return jsonify({'success': False, 'error': f'Impossibile verificare l\'identità: {e}'})
        
        if result.returncode != 0:
            os.remove(dest_path)
            return jsonify({'success': False, 'error': 'File non è un\'identità RNS valida'})
        
        rns_hash = None
        for line in result.stdout.split('\n'):
            if 'Loaded Identity <' in line:
                start = line.find('<') + 1
                end = line.find('>', start)
                if start > 0 and end > start:
                    rns_hash = line[start:end]
                    break
        
        identity_cache.clear('all_identities')
        
        return jsonify({
            'success': True,
            'message': f'Identità importata come {suggested_name}',
            'name': suggested_name,
            'path': dest_path,
            'info': result.stdout,
            'size': file_size,
            'rns_hash': rns_hash
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/import/data', methods=['POST'])
def import_identity_data():
    try:
        data = request.get_json(silent=True) or {}
        identity_data = str(data.get('data', '')).strip()
        format_type = data.get('format', 'hex')
        suggested_name = data.get('suggested_name', '').strip()
        is_private = data.get('private', False)
        
        if not identity_data:
            return jsonify({'success': False, 'error': 'No identity data provided'})
        
        suggested_name = _safe_identity_name(suggested_name, f"imported_{int(time.time())}")
        dest_path = _identity_dest_path(suggested_name)
        if os.path.exists(dest_path):
            return jsonify({'success': False, 'error': f"Esiste già un'identità chiamata {suggested_name}"})
        
        try:
            if format_type == 'hex':
                identity_data = ''.join(identity_data.split())
                identity_bytes = bytes.fromhex(identity_data)
            elif format_type == 'base64':
                identity_bytes = base64.b64decode(identity_data)
            elif format_type == 'base32':
                identity_bytes = base64.b32decode(identity_data)
            else:
                return jsonify({'success': False, 'error': f'Formato non supportato: {format_type}'})
            
            print(f"[DEBUG] Decodificati {len(identity_bytes)} bytes")
            
        except Exception as e:
            return jsonify({
                'success': False, 
                'error': f'Errore nella decodifica dei dati: {str(e)}'
            })
        
        if len(identity_bytes) not in [64, 128]:
            return jsonify({
                'success': False,
                'error': f'Dati di {len(identity_bytes)} bytes, deve essere 64 (public) o 128 (private) bytes'
            })
        
        actual_is_private = (len(identity_bytes) == 128)
        
        with open(dest_path, 'wb') as f:
            f.write(identity_bytes)
        
        print(f"[DEBUG] Identity salvata in {dest_path} ({len(identity_bytes)} bytes)")
        
        verify_cmd = [get_rnid_path(), '-i', dest_path, '--print-identity']
        if actual_is_private:
            verify_cmd.append('-P')
        
        verify_result = subprocess.run(
            verify_cmd,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if verify_result.returncode != 0:
            os.remove(dest_path)
            return jsonify({
                'success': False, 
                'error': f'I dati non rappresentano un\'identità RNS valida: {verify_result.stderr[:200]}'
            })
        
        rns_hash = None
        for line in verify_result.stdout.split('\n'):
            if 'Loaded Identity <' in line:
                start = line.find('<') + 1
                end = line.find('>', start)
                if start > 0 and end > start:
                    rns_hash = line[start:end]
                    break
        
        identity_cache.clear('all_identities')
        print("[DEBUG] Cache identità invalidata dopo import")
        
        return jsonify({
            'success': True,
            'message': f'Identità {"privata" if actual_is_private else "pubblica"} importata come {suggested_name}',
            'name': suggested_name,
            'path': dest_path,
            'info': verify_result.stdout,
            'size': len(identity_bytes),
            'format': format_type,
            'rns_hash': rns_hash,
            'type': 'private' if actual_is_private else 'public'
        })
        
    except Exception as e:
        print(f"[DEBUG] Errore import: {str(e)}")
        return jsonify({'success': False, 'error': f'Errore: {str(e)}'})

@app.route('/api/identities/export', methods=['POST'])
def export_identity():
    try:
        data = request.get_json(silent=True) or {}
        identity_path = data.get('path', '')
        format_type = data.get('format', 'hex')
        export_private = data.get('private', False)

        if not _is_allowed_identity_path(identity_path):
            return jsonify({'success': False, 'error': 'Percorso identità non consentito'}), 403
        
        if not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'File identità non trovato'})
        
        file_size = os.path.getsize(identity_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        rnid_cmd = get_rnid_path()
        cmd = [rnid_cmd, '-i', identity_path]
        
        if export_private:
            cmd.append('-X')
            cmd.append('-P')
        else:
            cmd.append('-x')
        
        if format_type == 'base64':
            cmd.append('-b')
        elif format_type == 'base32':
            cmd.append('-B')
        
        print(f"[DEBUG] Export command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({
                'success': False, 
                'error': f'Errore: {result.stderr[:200]}'
            })
        
        output = result.stdout
        
        output = re.sub(r'.\x08', '', output)
        output = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', output)
        output = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', output)
        
        key_data = None
        key_type = "Private" if export_private else "Public"
        
        for line in output.split('\n'):
            line = line.strip()
            if f"{key_type} Identity Keys" in line:
                if ": " in line:
                    key_data = line.split(": ", 1)[-1].strip()
                    break
        
        if not key_data:
            for line in reversed(output.split('\n')):
                line = line.strip()
                if line and not line.startswith('Loaded') and not line.startswith('Exported') and not line.startswith('['):
                    if re.match(r'^[0-9a-fA-F]+$', line) or re.match(r'^[A-Za-z0-9+/=]+$', line):
                        key_data = line
                        break
        
        if not key_data:
            for line in reversed(output.split('\n')):
                line = line.strip()
                if line and not line.startswith('Loaded') and not line.startswith('Exported'):
                    key_data = line
                    break
        
        if not key_data:
            return jsonify({
                'success': False,
                'error': f'Impossibile estrarre la chiave {key_type}'
            })
        
        return jsonify({
            'success': True,
            'data': key_data,
            'format': format_type,
            'length': len(key_data),
            'type': 'private' if export_private else 'public'
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Timeout del comando'})
    except Exception as e:
        print(f"[DEBUG] Errore export: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/generate', methods=['POST'])
def generate_identity():
    try:
        data = request.get_json(silent=True) or {}
        name = _safe_identity_name(data.get('name', ''), 'identity')

        dest_path = _identity_dest_path(name)
        if os.path.exists(dest_path):
            return jsonify({'success': False, 'error': f"Esiste già un'identità chiamata {name}"})
        
        rnid_cmd = get_rnid_path()
        result = subprocess.run(
            [rnid_cmd, '-g', dest_path, '--print-identity', '-P'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({'success': False, 'error': 'Errore nella generazione'})
        
        file_size = os.path.getsize(dest_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File generato di {file_size} bytes (dovrebbe essere 64)'})
        
        identity_cache.clear('all_identities')
        
        return jsonify({
            'success': True,
            'message': f'Identità generata in {dest_path}',
            'path': dest_path,
            'info': result.stdout,
            'size': file_size
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/info', methods=['POST'])
def get_identity_info():
    try:
        data = request.get_json(silent=True) or {}
        identity_path = data.get('path', '')
        aspect = data.get('aspect', '')

        if not _is_allowed_identity_path(identity_path):
            return jsonify({'success': False, 'error': 'Percorso identità non consentito'}), 403
        
        if not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'File identità non trovato'})
        
        file_size = os.path.getsize(identity_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        rnid_cmd = get_rnid_path()
        result = subprocess.run(
            [rnid_cmd, '-i', identity_path, '--print-identity', '-P'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        info_text = ""
        rns_hash = None
        
        if result.returncode == 0:
            info_text = result.stdout
            for line in result.stdout.split('\n'):
                if 'Loaded Identity <' in line:
                    start = line.find('<') + 1
                    end = line.find('>', start)
                    if start > 0 and end > start:
                        rns_hash = line[start:end]
                        break
        
        aspect_hashes = {}
        
        if aspect and aspect in rns_monitor.RNS_ASPECTS:
            hash_result = subprocess.run(
                [rnid_cmd, '-i', identity_path, '-H', aspect],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if hash_result.returncode == 0:
                pattern = f"The {aspect} destination for this Identity is <"
                for line in hash_result.stdout.split('\n'):
                    if pattern in line:
                        start = line.find('<') + 1
                        end = line.find('>', start)
                        if start > 0 and end > start:
                            aspect_hashes[aspect] = line[start:end]
                            break
                
                if aspect not in aspect_hashes:
                    for line in hash_result.stdout.split('\n'):
                        if 'Destination hash:' in line:
                            aspect_hashes[aspect] = line.split('Destination hash:')[-1].strip()
                            break
        
        return jsonify({
            'success': True,
            'info': info_text,
            'path': identity_path,
            'size': file_size,
            'rns_hash': rns_hash,
            'aspect_hashes': aspect_hashes
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === ROUTE PER CIFRATURA TESTO ===
# ============================================

@app.route('/api/identities/encrypt', methods=['POST'])
def encrypt_text():
    try:
        data = request.get_json(silent=True) or {}
        identity_path = data.get('identity_path', '')
        text = data.get('text', '')
        
        if not identity_path:
            return jsonify({'success': False, 'error': 'Identità non trovata'})

        # Le identità pubbliche hanno formato "public:<hash>": non sono file su disco
        if not identity_path.startswith('public:'):
            if not _is_allowed_identity_path(identity_path):
                return jsonify({'success': False, 'error': 'Percorso identità non consentito'}), 403
        elif not _hex_arg(identity_path.replace('public:', '', 1), 32, 64):
            return jsonify({'success': False, 'error': 'Hash pubblico non valido'}), 400
        
        if not text:
            return jsonify({'success': False, 'error': 'Nessun testo da cifrare'})
        
        temp_input = os.path.join(LOCAL_TMP, "web_input.txt")
        temp_output = os.path.join(LOCAL_TMP, "web_input.txt.rfe")
        
        _unlink(temp_input, temp_output)
        
        with open(temp_input, 'w') as f:
            f.write(text)
        
        # Cifra usando rnid
        rnid_cmd = get_rnid_path()
        if identity_path.startswith('public:'):
            rns_hash = identity_path.replace('public:', '')
            cmd = [rnid_cmd, '-R', '-i', rns_hash, '-e', temp_input]
        else:
            cmd = [rnid_cmd, '-i', identity_path, '-e', temp_input]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            _unlink(temp_input, temp_output)
            return jsonify({'success': False, 'error': f'Errore cifratura: {result.stderr[:200]}'})
        
        if not os.path.exists(temp_output):
            _unlink(temp_input, temp_output)
            return jsonify({'success': False, 'error': 'File cifrato non creato'})
        
        with open(temp_output, 'rb') as f:
            encrypted_bytes = f.read()
        encrypted_base64 = base64.b64encode(encrypted_bytes).decode('ascii')
        
        _unlink(temp_input, temp_output)
        
        return jsonify({
            'success': True,
            'encrypted': encrypted_base64
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === ROUTE PER DECIFRATURA TESTO ===
# ============================================

@app.route('/api/identities/decrypt', methods=['POST'])
def decrypt_text():
    try:
        data = request.get_json(silent=True) or {}
        identity_path = data.get('identity_path', '')
        encrypted_text = data.get('encrypted_text', '')
        
        if not _is_allowed_identity_path(identity_path) or not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'Identità non trovata'}), 403
        
        if not encrypted_text:
            return jsonify({'success': False, 'error': 'Nessun testo da decifrare'})
        
        if identity_path.startswith('public:'):
            return jsonify({'success': False, 'error': 'Per DECIFRARE serve un\'identità PRIVATA!'})
        
        clean_input = encrypted_text.strip()
        
        temp_input = os.path.join(LOCAL_TMP, "web_encrypted.rfe")
        # rnid -d rimuove l'estensione .rfe: l'output è "web_encrypted"
        temp_output = os.path.join(LOCAL_TMP, "web_encrypted")
        
        _unlink(temp_input, temp_output)
        
        try:
            encrypted_bytes = base64.b64decode(clean_input)
            with open(temp_input, 'wb') as f:
                f.write(encrypted_bytes)
        except Exception as e:
            _unlink(temp_input, temp_output)
            return jsonify({'success': False, 'error': f'Base64 non valido: {str(e)}'})
        
        rnid_cmd = get_rnid_path()
        cmd = [rnid_cmd, '-i', identity_path, '-d', temp_input, '-f']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            _unlink(temp_input, temp_output)
            return jsonify({'success': False, 'error': f'Errore decifratura: {result.stderr[:200]}'})
        
        decrypted_text = ""
        if os.path.exists(temp_output):
            with open(temp_output, 'r') as f:
                decrypted_text = f.read().strip()
            
            _unlink(temp_input, temp_output)
            
            return jsonify({
                'success': True,
                'decrypted': decrypted_text
            })
        else:
            _unlink(temp_input, temp_output)
            return jsonify({'success': False, 'error': 'File decifrato non creato'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === ROUTE PER FIRMA ===
# ============================================

@app.route('/api/identities/sign', methods=['POST'])
def sign_text():
    try:
        data = request.get_json(silent=True) or {}
        identity_path = data.get('identity_path', '')
        text = data.get('text', '')
        
        if not _is_allowed_identity_path(identity_path) or not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'Identità non trovata'}), 403
        
        if not text:
            return jsonify({'success': False, 'error': 'Nessun testo da firmare'})
        
        if identity_path.startswith('public:'):
            return jsonify({'success': False, 'error': 'Per FIRMARE serve un\'identità PRIVATA!'})
        
        temp_file = os.path.join(LOCAL_TMP, "web_sign.txt")
        temp_sig = os.path.join(LOCAL_TMP, "web_sign.txt.rsg")
        
        _unlink(temp_file, temp_sig)
        
        with open(temp_file, 'w') as f:
            f.write(text)
        
        rnid_cmd = get_rnid_path()
        cmd = [rnid_cmd, '-i', identity_path, '-s', temp_file, '-f']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            _unlink(temp_file, temp_sig)
            return jsonify({'success': False, 'error': f'Errore firma: {result.stderr[:200]}'})
        
        if not os.path.exists(temp_sig):
            _unlink(temp_file, temp_sig)
            return jsonify({'success': False, 'error': 'File firma non creato'})
        
        with open(temp_sig, 'rb') as f:
            sig_bytes = f.read()
        sig_base64 = base64.b64encode(sig_bytes).decode('ascii')
        
        hash_result = subprocess.run(
            [rnid_cmd, '-i', identity_path, '--print-identity'],
            capture_output=True,
            text=True,
            timeout=5
        )
        rns_hash = None
        if hash_result.returncode == 0:
            match = re.search(r'Loaded Identity <([0-9a-f]+)>', hash_result.stdout)
            if match:
                rns_hash = match.group(1)
        
        _unlink(temp_file, temp_sig)
        
        return jsonify({
            'success': True,
            'signature': sig_base64,
            'rns_hash': rns_hash,
            'message': 'Firma creata con successo'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === ROUTE PER VERIFICA FIRMA ===
# ============================================

@app.route('/api/identities/verify', methods=['POST'])
def verify_signature():
    try:
        data = request.get_json(silent=True) or {}
        identity_path = data.get('identity_path', '')
        text = data.get('text', '')
        signature = data.get('signature', '')
        
        if not identity_path:
            return jsonify({'success': False, 'error': 'Identità non specificata'})
        if not identity_path.startswith('public:') and not _is_allowed_identity_path(identity_path):
            return jsonify({'success': False, 'error': 'Percorso identità non consentito'}), 403
        if identity_path.startswith('public:') and not _hex_arg(identity_path.replace('public:', '', 1), 32, 64):
            return jsonify({'success': False, 'error': 'Hash pubblico non valido'}), 400
        
        if not text:
            return jsonify({'success': False, 'error': 'Nessun testo da verificare'})
        
        if not signature:
            return jsonify({'success': False, 'error': 'Nessuna firma da verificare'})
        
        temp_file = os.path.join(LOCAL_TMP, "web_verify.txt")
        temp_sig = os.path.join(LOCAL_TMP, "web_verify.txt.rsg")
        
        _unlink(temp_file, temp_sig)
        
        with open(temp_file, 'w') as f:
            f.write(text)
        
        try:
            sig_bytes = base64.b64decode(signature)
            with open(temp_sig, 'wb') as f:
                f.write(sig_bytes)
        except Exception as e:
            _unlink(temp_file, temp_sig)
            return jsonify({'success': False, 'error': f'Firma base64 non valida: {str(e)}'})
        
        rnid_cmd = get_rnid_path()
        if identity_path.startswith('public:'):
            rns_hash = identity_path.replace('public:', '')
            cmd = [rnid_cmd, '-R', '-i', rns_hash, '-V', temp_sig]
        else:
            cmd = [rnid_cmd, '-i', identity_path, '-V', temp_sig]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        _unlink(temp_file, temp_sig)
        
        if result.returncode != 0:
            return jsonify({
                'success': False,
                'error': f'Errore verifica: {result.stderr[:200]}',
                'output': result.stdout
            })
        
        if 'is valid' in result.stdout.lower():
            return jsonify({
                'success': True,
                'valid': True,
                'message': '✅ FIRMA VALIDA',
                'output': result.stdout
            })
        elif 'is invalid' in result.stdout.lower():
            return jsonify({
                'success': True,
                'valid': False,
                'message': '❌ FIRMA NON VALIDA',
                'output': result.stdout
            })
        else:
            return jsonify({
                'success': True,
                'valid': None,
                'message': '⚠️ Verifica completata, risultato ambiguo',
                'output': result.stdout
            })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === UPLOAD E CLEANUP ===
# ============================================

@app.route('/api/upload/temp', methods=['POST'])
def upload_temp_file():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'})
        
        # USA LA CARTELLA LOCALE
        temp_dir = os.path.join(LOCAL_TMP, 'uploads')
        os.makedirs(temp_dir, exist_ok=True)
        
        unique_id = str(uuid.uuid4())[:8]
        original_name = file.filename
        safe_name = ''.join(c for c in original_name if c.isalnum() or c in '._- ')
        safe_name = safe_name.strip(' .')[:120] or 'file'
        
        temp_filename = f"{unique_id}_{safe_name}"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        file.save(temp_path)
        
        return jsonify({
            'success': True,
            'temp_path': temp_path,
            'original_name': original_name,
            'output_dir': LOCAL_DOWNLOADS,
            'message': f'File salvato in: {temp_path}'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cleanup/temp', methods=['POST'])
def cleanup_temp_file():
    try:
        data = request.get_json(silent=True) or {}
        temp_path = data.get('temp_path', '')
        
        if not temp_path:
            return jsonify({'success': False, 'error': 'Percorso mancante'}), 400

        real = os.path.realpath(os.path.expanduser(str(temp_path)))
        tmp_root = os.path.realpath(LOCAL_TMP)
        if not real.startswith(tmp_root + os.sep) or not os.path.isfile(real):
            return jsonify({'success': False, 'error': 'Percorso non consentito'}), 400

        os.remove(real)
        return jsonify({'success': True, 'message': 'File temporaneo rimosso'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/files/relocate', methods=['POST'])
def relocate_file():
    """Sposta/copia un file da tmp a downloads (sicuro, senza shell)."""
    try:
        data = request.get_json(silent=True) or {}
        action = data.get('action', 'move')
        source = data.get('source', '')
        destination = data.get('destination', '')

        if action not in ('move', 'copy'):
            return jsonify({'success': False, 'error': 'Azione non valida'}), 400
        if not source or not destination:
            return jsonify({'success': False, 'error': 'Percorsi mancanti'}), 400

        result = _relocate(source, destination, action)
        return jsonify({'success': True, 'path': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

# ============================================
# === COMANDI RNS (rnstatus, rnpath, rnprobe) ===
# ============================================

@app.route('/api/rns/status')
def rns_status():
    try:
        rnstatus_cmd = get_rnstatus_path()
        result = subprocess.run(
            [rnstatus_cmd],
            capture_output=True,
            text=True,
            timeout=10
        )
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rns/paths')
def rns_paths():
    try:
        destination = request.args.get('dest', '')

        if destination and not _hex_arg(destination.strip(), 4, 64):
            return jsonify({'success': False, 'error': 'Hash destinazione non valido'}), 400
        
        print(f"[DEBUG] rnpath richiesto per destinazione: '{destination}'")
        
        rnpath_cmd = get_rnpath_path()
        if destination and destination.strip():
            cmd = [rnpath_cmd, destination.strip()]
            print(f"[DEBUG] Esecuzione comando: {' '.join(cmd)}")
        else:
            cmd = [rnpath_cmd]
            print(f"[DEBUG] Esecuzione comando: rnpath (senza argomenti)")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        print(f"[DEBUG] rnpath returncode: {result.returncode}")
        print(f"[DEBUG] rnpath stdout: {result.stdout[:200]}...")
        print(f"[DEBUG] rnpath stderr: {result.stderr[:200]}...")
        
        stdout = result.stdout
        stderr = result.stderr
        
        stdout = re.sub(r'.\x08', '', stdout)
        stdout = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stdout)
        stdout = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', stdout)
        
        stderr = re.sub(r'.\x08', '', stderr)
        stderr = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stderr)
        
        return jsonify({
            'success': result.returncode == 0,
            'output': stdout,
            'error': stderr,
            'destination': destination,
            'cmd': ' '.join(cmd) if destination else 'rnpath'
        })
    except subprocess.TimeoutExpired:
        print("[DEBUG] rnpath timeout scaduto")
        return jsonify({'success': False, 'error': 'Timeout del comando'})
    except Exception as e:
        print(f"[DEBUG] Errore rnpath: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rns/probe', methods=['POST'])
def rns_probe():
    try:
        data = request.get_json(silent=True) or {}
        destination = data.get('destination', '')
        aspect = data.get('aspect', 'rnstransport.probe')
        
        if not destination:
            return jsonify({'success': False, 'error': 'Nessuna destinazione specificata'})
        if not _hex_arg(str(destination).strip(), 32, 64):
            return jsonify({'success': False, 'error': 'Hash destinazione non valido'}), 400
        if aspect not in rns_monitor.RNS_ASPECTS:
            return jsonify({'success': False, 'error': 'Aspect non valido'}), 400
        
        rnprobe_cmd = get_rnprobe_path()
        cmd = [rnprobe_cmd, aspect, destination]
        
        print(f"[DEBUG] Esecuzione probe: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        print(f"[DEBUG] Probe returncode: {result.returncode}")
        print(f"[DEBUG] Probe stdout: {result.stdout[:200]}")
        
        stdout = result.stdout
        stderr = result.stderr
        
        stdout = re.sub(r'.\x08', '', stdout)
        stdout = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stdout)
        stdout = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', stdout)
        
        stderr = re.sub(r'.\x08', '', stderr)
        stderr = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stderr)
        
        return jsonify({
            'success': result.returncode == 0,
            'output': stdout,
            'error': stderr,
            'destination': destination,
            'aspect': aspect,
            'cmd': ' '.join(cmd)
        })
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Timeout del comando (30s)'})
    except Exception as e:
        print(f"[DEBUG] Errore probe: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rns/probe/aspect', methods=['POST'])
def rns_probe_aspect():
    try:
        data = request.get_json(silent=True) or {}
        identity_path = data.get('identity_path', '')
        aspect = data.get('aspect', 'rnstransport.probe')
        
        if not _is_allowed_identity_path(identity_path) or not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'Identità non trovata'}), 403
        
        if aspect not in rns_monitor.RNS_ASPECTS:
            return jsonify({'success': False, 'error': f'Aspect non valido: {aspect}'})
        
        rnid_cmd = get_rnid_path()
        hash_result = subprocess.run(
            [rnid_cmd, '-i', identity_path, '-H', aspect],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if hash_result.returncode != 0:
            return jsonify({'success': False, 'error': 'Impossibile calcolare hash aspect'})
        
        dest_hash = None
        for line in hash_result.stdout.split('\n'):
            if 'Destination hash:' in line:
                dest_hash = line.split('Destination hash:')[-1].strip()
                break
        
        if not dest_hash:
            return jsonify({'success': False, 'error': 'Hash non trovato'})
        
        rnprobe_cmd = get_rnprobe_path()
        probe_result = subprocess.run(
            [rnprobe_cmd, dest_hash],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        return jsonify({
            'success': probe_result.returncode == 0,
            'output': probe_result.stdout,
            'error': probe_result.stderr,
            'dest_hash': dest_hash,
            'aspect': aspect,
            'identity_path': identity_path
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rns/paths/blackhole', methods=['POST'])
def rns_paths_blackhole():
    try:
        data = request.get_json(silent=True) or {}
        identity_hash = str(data.get('destination', '')).strip()
        
        if not identity_hash:
            return jsonify({'success': False, 'error': 'Nessuna identità specificata'})
        if not _hex_arg(identity_hash, 32, 64):
            return jsonify({'success': False, 'error': 'Hash identità non valido'}), 400
        
        rnpath_cmd = get_rnpath_path()
        cmd = [rnpath_cmd, '-p', identity_hash]
        
        print(f"[DEBUG] Esecuzione rnpath -p per identità: {identity_hash}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr,
            'identity': identity_hash,
            'cmd': ' '.join(cmd)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/find/by-hash')
def find_identity_by_hash():
    try:
        hash_value = (request.args.get('hash', '') or '').strip()
        if not hash_value:
            return jsonify({'success': False, 'error': 'Nessun hash specificato'})
        if not _hex_arg(hash_value, 8, 64):
            return jsonify({'success': False, 'error': 'Hash non valido'}), 400
        
        storage_dirs = [
            (RETICULUM_STORAGE, 'reticulum'),
            (NOMADNET_STORAGE, 'nomadnet'),
            (LXMF_STORAGE, 'lxmf'),
            (RNS_MANAGER_STORAGE, 'rns_manager')
        ]
        
        if RNPHONE_STORAGE and os.path.exists(RNPHONE_STORAGE):
            storage_dirs.append((RNPHONE_STORAGE, 'rnphone'))
        
        if MESHCHAT_STORAGE and os.path.exists(MESHCHAT_STORAGE):
            storage_dirs.append((MESHCHAT_STORAGE, 'meshchat'))
        
        rnid_cmd = get_rnid_path()
        for storage_path, app_name in storage_dirs:
            if storage_path and os.path.exists(storage_path):
                for item in os.listdir(storage_path):
                    item_path = os.path.join(storage_path, item)
                    
                    if os.path.isfile(item_path) and os.path.getsize(item_path) == 64:
                        result = subprocess.run(
                            [rnid_cmd, '-i', item_path, '--print-identity'],
                            capture_output=True,
                            text=True,
                            timeout=3
                        )
                        
                        if result.returncode == 0:
                            for line in result.stdout.split('\n'):
                                if 'Loaded Identity <' in line:
                                    start = line.find('<') + 1
                                    end = line.find('>', start)
                                    if start > 0 and end > start:
                                        identity_hash = line[start:end]
                                        if identity_hash.startswith(hash_value):
                                            return jsonify({
                                                'success': True,
                                                'identity_path': item_path,
                                                'identity_name': item,
                                                'app': app_name,
                                                'full_hash': identity_hash
                                            })
        
        return jsonify({'success': False, 'error': 'Identità non trovata'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === ROUTE PER CHIUSURA SICURA DAL WEB ===
# ============================================

@app.route('/api/shutdown', methods=['POST'])
def shutdown_server():
    """Ferma il server e tutti i processi in modo sicuro (chiamata dal web)"""
    print("\n" + "="*60)
    print("🛑 ARRESTO RICHIESTO DAL WEB")
    print("="*60)
    
    def shutdown():
        import time
        time.sleep(1)
        
        print("[✓] Arresto server Flask...")
        
        # Ferma il monitor manager
        try:
            monitor_manager.stop()
            print("[✓] Monitor fermato")
        except Exception as e:
            print(f"[!] Errore fermo monitor: {e}")
        
        # Pulisci file temporanei
        try:
            if os.path.exists(LOCAL_TMP):
                shutil.rmtree(LOCAL_TMP)
                print(f"[✓] Cartella temporanea pulita: {LOCAL_TMP}")
        except Exception as e:
            print(f"[!] Errore pulizia file temporanei: {e}")
        
        print("="*60)
        print("✅ Server fermato correttamente")
        print("="*60)
        
        # Ferma il server Flask
        os._exit(0)
    
    # Avvia lo shutdown in un thread separato
    threading.Thread(target=shutdown, daemon=True).start()
    
    return jsonify({
        'success': True,
        'message': 'Server in arresto...'
    })

# ============================================
# === GESTIONE CHIUSURA SICURA (Ctrl+C) ===
# ============================================

def signal_handler(sig, frame):
    """Gestisce Ctrl+C e SIGTERM per fermare il server in modo sicuro"""
    print("\n" + "=" * 60)
    print("🛑 Arresto server in corso...")
    print("=" * 60)
    
    # Ferma il monitor manager
    try:
        monitor_manager.stop()
        print("[✓] Monitor fermato")
    except Exception as e:
        print(f"[!] Errore fermo monitor: {e}")
    
    # Pulisci file temporanei
    try:
        if os.path.exists(LOCAL_TMP):
            shutil.rmtree(LOCAL_TMP)
            print(f"[✓] Cartella temporanea pulita: {LOCAL_TMP}")
    except Exception as e:
        print(f"[!] Errore pulizia file temporanei: {e}")
    
    print("=" * 60)
    print("✅ Server fermato correttamente")
    print("=" * 60)
    sys.exit(0)

def open_browser():
    """Apre il browser dopo un breve ritardo"""
    time.sleep(2)
    try:
        webbrowser.open('http://127.0.0.1:5010')
        print("🌐 Browser aperto su http://127.0.0.1:5010")
    except Exception as e:
        print(f"[!] Impossibile aprire il browser: {e}")

# ============================================
# === AVVIO SERVER ===
# ============================================

if __name__ == '__main__':
    from multiprocessing import freeze_support
    freeze_support()
    # Registra il gestore di segnali per chiusura sicura
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Controlla i comandi RNS
    missing = check_rns_commands()
    if missing:
        print_rns_warning(missing)
    else:
        print("✅ Tutti i comandi RNS trovati.")

    print("=" * 60)
    print("RNID Web Interface + RNS Aspect Monitor")
    print("=" * 60)
    print(f"\n📁 Directory temporanea locale: {LOCAL_TMP}")
    print(f"📁 Downloads: {LOCAL_DOWNLOADS}")
    print(f"📁 Cache: {LOCAL_CACHE}")
    print("\n📁 Directory configurate:")
    for key, path in USER_DIRECTORIES.items():
        expanded = os.path.expanduser(path)
        print(f"  {key}: {expanded}")
    
    print("\n🌐 Accesso:")
    print(f"  http://localhost:5010/ - Identity Manager")
    print(f"  http://localhost:5010/monitor - Aspect Monitor")
    print("\n🚀 Avvio server...")
    print("   Premi Ctrl+C per fermare\n")
    
    # Avvia il thread per aprire il browser
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()
    
    try:
        app.run(host='127.0.0.1', port=5010, debug=False, use_reloader=False, threaded=True)
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)
    except Exception as e:
        print(f"\n[!] Errore imprevisto: {e}")
        signal_handler(signal.SIGTERM, None)