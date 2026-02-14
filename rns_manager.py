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
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, Response, stream_with_context

app = Flask(__name__)

# === PERCORSI CONFIGURATI DALL'UTENTE ===
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

# Crea directory per Downloads e Cache
DOWNLOADS_DIR = os.path.expanduser("~/.rns_manager/Downloads")
CACHE_DIR = os.path.expanduser("~/.rns_manager/Cache")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ============================================
# === CACHE PERSISTENTE PER ANNUNCI ===
# ============================================

class PersistentAnnounceCache:
    """Cache persistente su disco per annunci RNS"""
    
    def __init__(self, cache_file='announce_cache.json', save_interval=60, max_age_days=7, max_size=10000):
        self.cache_file = os.path.join(CACHE_DIR, cache_file)
        self.save_interval = save_interval
        self.max_age = timedelta(days=max_age_days)
        self.max_size = max_size
        
        # Cache in memoria
        self.cache = []
        self.lock = threading.Lock()
        self.stats = {
            'total_saved': 0,
            'last_save': None,
            'save_count': 0,
            'load_count': 0
        }
        
        # Carica cache esistente
        self._load_cache()
        
        # Avvia thread di salvataggio automatico
        self.running = True
        self.save_thread = threading.Thread(target=self._auto_save, daemon=True)
        self.save_thread.start()
        
        print(f"[Cache] Inizializzata: {self.cache_file}")
        print(f"[Cache] {len(self.cache)} annunci caricati, salvataggio ogni {save_interval}s")
    
    def _load_cache(self):
        """Carica cache dal disco all'avvio"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r') as f:
                    data = json.load(f)
                    
                    # Filtra annunci vecchi
                    now = time.time()
                    self.cache = [
                        item for item in data 
                        if now - item.get('timestamp', 0) < self.max_age.total_seconds()
                    ]
                    
                    # Ordina per timestamp (più recenti prima)
                    self.cache.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                    
                self.stats['load_count'] += 1
                print(f"[Cache] Caricati {len(self.cache)} annunci storici (rimossi {len(data) - len(self.cache)} vecchi)")
            else:
                print(f"[Cache] Nessun file cache esistente, partenza vuota")
        except Exception as e:
            print(f"[Cache] Errore caricamento: {e}")
            self.cache = []
    
    def add_announce(self, announce):
        """Aggiunge annuncio alla cache"""
        with self.lock:
            # Aggiungi timestamp se mancante
            if 'timestamp' not in announce:
                announce['timestamp'] = time.time()
            
            # Evita duplicati (controlla packet_hash)
            packet_hash = announce.get('packet_hash', '')
            if packet_hash:
                # Rimuovi eventuale duplicato esistente
                self.cache = [a for a in self.cache if a.get('packet_hash') != packet_hash]
            
            # Inserisci in testa (più recente)
            self.cache.insert(0, announce)
            
            # Limita dimensione
            if len(self.cache) > self.max_size:
                self.cache = self.cache[:self.max_size]
            
            self.stats['total_saved'] += 1
    
    def add_multiple(self, announces):
        """Aggiunge multipli annunci alla cache"""
        with self.lock:
            for announce in announces:
                if 'timestamp' not in announce:
                    announce['timestamp'] = time.time()
            
            # Filtra duplicati
            existing_packets = {a.get('packet_hash') for a in self.cache if a.get('packet_hash')}
            new_announces = [a for a in announces if a.get('packet_hash') not in existing_packets]
            
            self.cache = new_announces + self.cache
            self.cache = self.cache[:self.max_size]
            self.stats['total_saved'] += len(new_announces)
    
    def _auto_save(self):
        """Salva automaticamente ogni X secondi"""
        while self.running:
            time.sleep(self.save_interval)
            self.save()
    
    def save(self):
        """Salva cache su disco"""
        try:
            with self.lock:
                # Crea copia per salvare
                cache_copy = self.cache.copy()
            
            # Scrivi su file temporaneo poi rinomina (per evitare corruzione)
            temp_file = f"{self.cache_file}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(cache_copy, f, indent=2)
            
            # Rinomina file temporaneo
            os.replace(temp_file, self.cache_file)
            
            self.stats['last_save'] = time.time()
            self.stats['save_count'] += 1
            
            print(f"[Cache] Salvati {len(cache_copy)} annunci")
            
        except Exception as e:
            print(f"[Cache] Errore salvataggio: {e}")
    
    def get_all(self, filter_func=None, limit=None, offset=0):
        """Recupera annunci con filtro opzionale"""
        with self.lock:
            if filter_func:
                filtered = [a for a in self.cache if filter_func(a)]
            else:
                filtered = self.cache.copy()
        
        if offset > 0 or limit is not None:
            end = offset + limit if limit else None
            return filtered[offset:end]
        return filtered
    
    def get_stats(self):
        """Restituisce statistiche cache"""
        with self.lock:
            # Calcola statistiche aggiuntive
            if self.cache:
                oldest = min((a.get('timestamp', 0) for a in self.cache), default=0)
                newest = max((a.get('timestamp', 0) for a in self.cache), default=0)
                aspects = {}
                for a in self.cache:
                    asp = a.get('aspect', 'unknown')
                    aspects[asp] = aspects.get(asp, 0) + 1
            else:
                oldest = newest = 0
                aspects = {}
            
            return {
                'size': len(self.cache),
                'max_size': self.max_size,
                'oldest': oldest,
                'newest': newest,
                'age_days': (time.time() - oldest) / 86400 if oldest else 0,
                'aspects': aspects,
                'stats': self.stats,
                'cache_file': self.cache_file
            }
    
    def cleanup_old(self):
        """Rimuove annunci vecchi"""
        with self.lock:
            now = time.time()
            old_count = len(self.cache)
            self.cache = [
                a for a in self.cache 
                if now - a.get('timestamp', 0) < self.max_age.total_seconds()
            ]
            removed = old_count - len(self.cache)
            if removed:
                print(f"[Cache] Rimossi {removed} annunci vecchi")
                self.save()
            return removed
    
    def clear(self):
        """Pulisce tutta la cache"""
        with self.lock:
            self.cache = []
            self.save()
        print(f"[Cache] Cache pulita")
    
    def stop(self):
        """Ferma thread e salva"""
        self.running = False
        self.save_thread.join(timeout=5)
        self.save()
        print(f"[Cache] Cache fermata")

# ============================================
# === CACHE IDENTITÀ (server-side) ===
# ============================================

class IdentityCache:
    """Cache server-side per le identità"""
    def __init__(self, cache_duration=300):  # 5 minuti default
        self.cache = {}
        self.timestamps = {}
        self.cache_duration = cache_duration
        self.lock = threading.Lock()
        
    def get(self, key='all_identities'):
        """Recupera dalla cache se non scaduta"""
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
        """Salva in cache"""
        with self.lock:
            self.cache[key] = data
            self.timestamps[key] = time.time()
            print(f"[Cache ID] Salvati {len(data)} elementi per {key}")
    
    def clear(self, key=None):
        """Pulisce cache"""
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
        """Statistiche cache"""
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
identity_cache = IdentityCache(cache_duration=3600)  # 6 ore

# ============================================
# === ASPECTS DEFINITI UNA SOLA VOLTA ===
# ============================================
RNS_ASPECTS = [
    "lxmf.delivery","nomadnetwork.node","lxst.telephony","call.audio","retibbs.bbs","rrc.hub","lxmf.propagation",
    "rnstransport.probe","rnstransport.info.blackhole","rnsh","rncp","rncp.receive","rnsh.listen","rnsh.default",
    "rns_unit_tests.link.establish",
    "rnstransport.discovery.interface",
    "rnstransport.tunnel.synthesize",
    "rnstransport.path.request",
    "rnstransport.remote.management",    
    "rnstransport.network.instance",
    "rnstransport.network",
    "example_utilities.minimalsample",
    "example_utilities.echo.request",
    "example_utilities.broadcast",
    "example_utilities.bufferexample",
    "example_utilities.channelexample",
    "example_utilities.filetransfer.server",
    "example_utilities.identifyexample",
    "example_utilities.linkexample",
    "example_utilities.ratchet.echo.request",
    "example_utilities.requestexample",
    "example_utilities.resourceexample",
    "example_utilities.speedtest","discovery.interface",    
]

# ============================================
# === MONITOR ANNUNCI RNS ===
# ============================================

SOCKET_PATH = "/tmp/rns_monitor.sock"
announce_history = []
MAX_HISTORY = 1000
announce_counter = 0
history_lock = threading.Lock()
monitor_process = None
announce_queue = queue.Queue(maxsize=1000)

# Inizializza cache persistente
announce_cache = PersistentAnnounceCache(
    cache_file='announce_cache.json',
    save_interval=60,      # Salva ogni minuto
    max_age_days=7,         # Mantieni 7 giorni
    max_size=10000          # Massimo 10000 annunci
)

def run_rns_monitor():
    """Processo separato con il monitor RNS"""
    import RNS
    import socket
    import json
    import time
    import os
    import traceback
    from datetime import datetime
    
    class AnnounceMonitor:
        aspect_filter = None
        receive_path_responses = False
        
        def __init__(self, sock):
            self.count = 0
            self.cache = {}  # identity_hash -> {dest_hash: aspect}
            self.seen_packets = set()
            self.socket = sock
            # Usa gli ASPECT dalla variabile globale
            self.ASPECTS = RNS_ASPECTS
        
        def send_announce(self, data):
            """Invia annuncio via socket"""
            try:
                message = json.dumps(data) + "\n"
                self.socket.send(message.encode('utf-8'))
            except Exception as e:
                pass
        
        def received_announce(self, destination_hash, announced_identity, app_data, announce_packet_hash):
            try:
                self.count += 1
                ts = datetime.now().strftime("%H:%M:%S")
                
                dest_hex = destination_hash.hex()
                packet_hex = announce_packet_hash.hex()[:32]
                
                # App data
                app_text = ""
                if app_data:
                    try:
                        text = app_data.decode('utf-8', errors='ignore').strip()
                        if text:
                            app_text = ' '.join(text.split())[:100]
                    except:
                        app_text = f"[{len(app_data)}b]"
                
                # Calcola aspect
                aspect = "unknown"
                identity_hash = ""
                
                if announced_identity:
                    identity_hash = announced_identity.hash.hex()
                    aspect = self._calculate_aspect_rnid(announced_identity, dest_hex)
                
                # Ottieni hops e interfaccia
                hops = "?"
                interface = "?"
                
                if RNS.Transport.has_path(destination_hash):
                    entry = RNS.Transport.path_table.get(destination_hash)
                    if entry:
                        hops = str(entry[2])
                        if entry[5]:
                            iface_str = str(entry[5])
                            if "[" in iface_str:
                                interface = iface_str.split("[")[0]
                            else:
                                interface = iface_str[:20]
                
                # Output
                print(f"[{ts}] #{self.count:04d} id: {identity_hash[:32] if identity_hash else '?'*32} dest: {dest_hex[:32]}... hops: {hops} aspect: {aspect} iface: {interface} data: '{app_text[:50]}'")
                
                # Prepara dati per Flask
                announce_data = {
                    'id': self.count,
                    'time': ts,
                    'timestamp': time.time(),
                    'dest_hash': dest_hex,
                    'dest_short': dest_hex[:16] + "...",
                    'dest_full': dest_hex,
                    'packet_hash': packet_hex,
                    'packet_short': packet_hex[:16],
                    'packet_full': packet_hex,
                    'identity_hash': identity_hash,
                    'identity_short': identity_hash[:32] if identity_hash else "?",
                    'aspect': aspect,
                    'hops': hops,
                    'interface': interface,
                    'data': app_text,
                    'data_length': len(app_data) if app_data else 0,
                    'has_identity': announced_identity is not None
                }
                
                self.send_announce(announce_data)
                
            except Exception as e:
                print(f"[MONITOR] Errore: {e}")
        
        def _calculate_aspect_rnid(self, identity, target_dest_hex):
            """Calcola l'aspect per un dato identity e destination hash"""
            identity_hash = identity.hash.hex()
            
            # Controlla cache
            if identity_hash in self.cache:
                if target_dest_hex in self.cache[identity_hash]:
                    return self.cache[identity_hash][target_dest_hex]
            
            # Inizializza cache
            if identity_hash not in self.cache:
                self.cache[identity_hash] = {}
            
            # Prova ogni aspect
            for aspect in self.ASPECTS:
                try:
                    parts = aspect.split(".")
                    if len(parts) == 0:
                        continue
                    
                    app_name = parts[0]
                    aspect_parts = parts[1:] if len(parts) > 1 else []
                    
                    destination = RNS.Destination(
                        identity,
                        RNS.Destination.OUT,
                        RNS.Destination.SINGLE,
                        app_name,
                        *aspect_parts
                    )
                    
                    calculated_hex = destination.hash.hex()
                    
                    # Salva in cache
                    self.cache[identity_hash][calculated_hex] = aspect
                    
                    # Controlla corrispondenza
                    if calculated_hex == target_dest_hex:
                        return aspect
                        
                except Exception:
                    continue
            
            # Controlla se è l'identity hash stesso
            if len(target_dest_hex) == 32:
                if identity_hash.startswith(target_dest_hex[:len(identity_hash)]):
                    return "identity_hash"
            
            return "unknown"
    
    # === SETUP SOCKET ===
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass
    
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    server.listen(1)
    os.chmod(SOCKET_PATH, 0o666)
    
    print("[MONITOR] In attesa di connessione Flask...")
    
    try:
        client_socket, _ = server.accept()
        print("[MONITOR] ✅ Connesso a Flask")
        
        print("[MONITOR] Avvio Reticulum...")
        
        # Disabilita signal handling
        try:
            import RNS._runtime
            RNS._runtime.RNS_SIGNAL_HANDLING = False
        except:
            pass
        
        reticulum = RNS.Reticulum()
        monitor = AnnounceMonitor(client_socket)
        RNS.Transport.register_announce_handler(monitor)
        
        print("[MONITOR] ✅ In ascolto annunci...")
        print("[MONITOR] " + "="*80)
        
        while True:
            time.sleep(1)
            
    except Exception as e:
        print(f"[MONITOR] ❌ Errore: {e}")
        traceback.print_exc()
    finally:
        server.close()

def start_monitor_process():
    global monitor_process
    try:
        os.unlink(SOCKET_PATH)
    except OSError:
        pass
    
    monitor_process = multiprocessing.Process(
        target=run_rns_monitor,
        daemon=True
    )
    monitor_process.start()
    print(f"[Flask] Monitor avviato (PID: {monitor_process.pid})")
    time.sleep(2)
    return True

start_monitor_process()

def socket_listener():
    """Thread che ascolta gli annunci dal socket"""
    global announce_counter, announce_history, announce_queue
    
    sock = None
    buffer = ""
    
    print("[Flask] Connessione al monitor...")
    
    while True:
        try:
            if sock is None:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(5)
                sock.connect(SOCKET_PATH)
                sock.settimeout(None)
                print("[Flask] ✅ Connesso al monitor socket")
            
            data = sock.recv(16384).decode('utf-8')
            if not data:
                sock.close()
                sock = None
                time.sleep(2)
                continue
            
            buffer += data
            lines = buffer.split('\n')
            buffer = lines[-1]
            
            for line in lines[:-1]:
                if line.strip():
                    try:
                        announce = json.loads(line)
                        with history_lock:
                            announce_counter += 1
                            announce['id'] = announce_counter
                            announce_history.insert(0, announce)
                            
                            # Aggiungi alla cache persistente
                            announce_cache.add_announce(announce)
                            
                            if len(announce_history) > MAX_HISTORY:
                                announce_history.pop()
                        
                        try:
                            announce_queue.put_nowait(announce)
                        except queue.Full:
                            pass
                        
                        print(f"[Flask] ✅ Annuncio #{announce_counter} - Aspect: {announce.get('aspect', 'unknown')}")
                        
                    except json.JSONDecodeError:
                        continue
                        
        except (ConnectionRefusedError, FileNotFoundError):
            if sock:
                sock.close()
                sock = None
            time.sleep(2)
        except Exception as e:
            print(f"[Flask] Errore socket: {e}")
            if sock:
                sock.close()
                sock = None
            time.sleep(2)

socket_thread = threading.Thread(target=socket_listener, daemon=True)
socket_thread.start()
time.sleep(1)

# ============================================
# === ROUTE MONITOR ===
# ============================================

@app.route('/monitor')
def monitor_page():
    return render_template('monitor.html')

@app.route('/api/monitor/stats')
def monitor_stats_api():
    with history_lock:
        cache_stats = announce_cache.get_stats()
        
        return jsonify({
            'success': True,
            'total_announces': announce_counter,
            'history_size': len(announce_history),
            'monitor_alive': monitor_process.is_alive() if monitor_process else False,
            'unique_sources': len({a.get('identity_hash', '') for a in announce_history if a.get('identity_hash')}) if announce_history else 0,
            'cache': cache_stats
        })

@app.route('/api/monitor/history')
def monitor_history_api():
    aspect_filter = request.args.get('aspect', 'all')
    limit = int(request.args.get('limit', 100))
    offset = int(request.args.get('offset', 0))
    search = request.args.get('search', '').lower()
    sort = request.args.get('sort', 'time_desc')
    source = request.args.get('source', 'memory')  # 'memory' o 'cache'
    
    if source == 'cache':
        # Usa cache persistente
        def filter_func(a):
            if aspect_filter != 'all':
                if aspect_filter == 'unknown' and a.get('aspect') not in ['unknown', None]:
                    return False
                elif aspect_filter == 'known' and a.get('aspect') in ['unknown', None, 'identity_hash']:
                    return False
                elif aspect_filter not in ['all', 'unknown', 'known'] and a.get('aspect') != aspect_filter:
                    return False
            
            if search:
                return (search in a.get('dest_hash', '').lower() or
                       search in a.get('identity_hash', '').lower() or
                       search in a.get('aspect', '').lower() or
                       search in a.get('data', '').lower())
            return True
        
        filtered = announce_cache.get_all(filter_func)
        total = len(filtered)
        
        # Ordina
        sort_functions = {
            'time_desc': (lambda x: x.get('timestamp', 0), True),
            'time_asc': (lambda x: x.get('timestamp', 0), False),
            'hops_desc': (lambda x: int(x.get('hops', 0)) if str(x.get('hops', '0')).isdigit() else 0, True),
            'hops_asc': (lambda x: int(x.get('hops', 0)) if str(x.get('hops', '0')).isdigit() else 999, False),
        }
        key_func, reverse = sort_functions.get(sort, (lambda x: x.get('timestamp', 0), True))
        filtered.sort(key=key_func, reverse=reverse)
        
        paginated = filtered[offset:offset + limit]
        
    else:
        # Usa memoria (comportamento originale)
        with history_lock:
            filtered = announce_history.copy()
        
        # Filtra per aspect
        if aspect_filter != 'all':
            if aspect_filter == 'unknown':
                filtered = [a for a in filtered if a.get('aspect') in ['unknown', None]]
            elif aspect_filter == 'known':
                filtered = [a for a in filtered if a.get('aspect') not in ['unknown', None] and a.get('aspect') != 'identity_hash']
            else:
                filtered = [a for a in filtered if a.get('aspect') == aspect_filter]
        
        # Filtra per search
        if search:
            filtered = [a for a in filtered if 
                       search in a.get('dest_hash', '').lower() or
                       search in a.get('identity_hash', '').lower() or
                       search in a.get('aspect', '').lower() or
                       search in a.get('data', '').lower()]
        
        # ORDINAMENTO
        sort_functions = {
            'time_asc': (lambda x: x.get('timestamp', 0), False),
            'time_desc': (lambda x: x.get('timestamp', 0), True),
            'hops_asc': (lambda x: int(x.get('hops', 0)) if str(x.get('hops', '0')).isdigit() else 999, False),
            'hops_desc': (lambda x: int(x.get('hops', 0)) if str(x.get('hops', '0')).isdigit() else 0, True),
            'aspect_asc': (lambda x: x.get('aspect', ''), False),
            'aspect_desc': (lambda x: x.get('aspect', ''), True),
            'identity_asc': (lambda x: x.get('identity_hash', ''), False),
            'identity_desc': (lambda x: x.get('identity_hash', ''), True),
        }
        
        key_func, reverse = sort_functions.get(sort, (lambda x: x.get('timestamp', 0), True))
        filtered.sort(key=key_func, reverse=reverse)
        
        total = len(filtered)
        paginated = filtered[offset:offset + limit]
    
    return jsonify({
        'success': True,
        'announces': paginated,
        'total': total,
        'source': source
    })

@app.route('/api/monitor/history/persistent')
def persistent_history_api():
    """Endpoint specifico per storico persistente"""
    days = int(request.args.get('days', 7))
    aspect = request.args.get('aspect', 'all')
    limit = int(request.args.get('limit', 500))
    offset = int(request.args.get('offset', 0))
    
    # Filtra per data
    cutoff = time.time() - (days * 86400)
    
    def filter_func(a):
        if a.get('timestamp', 0) < cutoff:
            return False
        if aspect != 'all' and a.get('aspect') != aspect:
            return False
        return True
    
    announces = announce_cache.get_all(filter_func, limit=limit, offset=offset)
    total = len(announce_cache.get_all(filter_func))
    
    return jsonify({
        'success': True,
        'announces': announces,
        'total': total,
        'days': days,
        'aspect': aspect
    })

@app.route('/api/monitor/stream')
def monitor_stream():
    def generate():
        client_id = str(uuid.uuid4())[:8]
        print(f"[SSE] Client {client_id} connesso")
        
        last_id = 0
        with history_lock:
            if announce_history:
                for ann in reversed(announce_history[:10]):
                    if ann['id'] > last_id:
                        last_id = ann['id']
                        yield f"data: {json.dumps(ann)}\n\n"
        
        while True:
            try:
                announce = announce_queue.get(timeout=30)
                if announce['id'] > last_id:
                    last_id = announce['id']
                    yield f"data: {json.dumps(announce)}\n\n"
                    print(f"[SSE] Inviato #{announce['id']} a {client_id}")
            except queue.Empty:
                yield ":\n\n"
                continue
            except GeneratorExit:
                print(f"[SSE] Client {client_id} disconnesso")
                break
            except Exception as e:
                print(f"[SSE] Errore: {e}")
                time.sleep(1)
    
    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive'
        }
    )

@app.route('/api/monitor/clear', methods=['POST'])
def monitor_clear_api():
    global announce_history, announce_counter
    with history_lock:
        announce_history = []
        announce_counter = 0
        while not announce_queue.empty():
            try:
                announce_queue.get_nowait()
            except:
                break
    return jsonify({'success': True})

@app.route('/api/monitor/cache/clear', methods=['POST'])
def monitor_cache_clear_api():
    """Pulisce la cache persistente"""
    announce_cache.clear()
    return jsonify({'success': True, 'message': 'Cache persistente pulita'})

@app.route('/api/monitor/cache/cleanup', methods=['POST'])
def monitor_cache_cleanup_api():
    """Rimuove annunci vecchi dalla cache"""
    removed = announce_cache.cleanup_old()
    return jsonify({
        'success': True, 
        'removed': removed,
        'message': f'Rimossi {removed} annunci vecchi'
    })

@app.route('/api/monitor/cache/save', methods=['POST'])
def monitor_cache_save_api():
    """Salva forzatamente la cache"""
    announce_cache.save()
    return jsonify({'success': True, 'message': 'Cache salvata'})

@app.route('/api/monitor/cache/stats')
def monitor_cache_stats_api():
    """Statistiche dettagliate cache"""
    stats = announce_cache.get_stats()
    return jsonify({
        'success': True,
        'stats': stats
    })

@app.route('/api/monitor/aspects')
def monitor_aspects_api():
    return jsonify({
        'success': True,
        'aspects': RNS_ASPECTS
    })

# ============================================
# === TUTTE LE ROUTE IDENTITY MANAGER ===
# ============================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/rnid', methods=['POST'])
def execute_rnid():
    try:
        data = request.json
        command = data.get('command', '').strip()
        
        if not command:
            return jsonify({'success': False, 'error': 'No command provided'})
        
        if command.startswith('rnid'):
            try:
                parts = shlex.split(command)
            except:
                parts = command.split()
            
            if parts[0] == 'rnid':
                parts = parts[1:]
            
            result = subprocess.run(
                ['rnid'] + parts,
                capture_output=True,
                text=True,
                timeout=30
            )
            
        elif command.startswith(('rm -f ', 'echo ', 'base64 ', 'cat ', 'stat -c%s ', 'cp ', 'mkdir -p ')):
            allowed_paths = [
                '/tmp/web_input.txt',
                '/tmp/web_encrypted.enc', 
                '/tmp/web_decrypted.txt',
                '/tmp/rnid_web/',
                '/tmp/rnid_web_signed/',
                DOWNLOADS_DIR,
                CACHE_DIR
            ]
            
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
        else:
            return jsonify({'success': False, 'error': 'Comando non permesso'})
        
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr,
            'return_code': result.returncode
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/list', methods=['GET'])
def list_identities():
    # Controlla parametro force
    force_refresh = request.args.get('force', 'false').lower() == 'true'
    
    # Se non forza refresh, prova a usare cache
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
                            result = subprocess.run(
                                ['rnid', '-i', item_path, '--print-identity'],
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
                            
                            # Limita a 10 aspect per performance
                            aspects_to_check = RNS_ASPECTS[:5]
                            for aspect in aspects_to_check:
                                try:
                                    hash_result = subprocess.run(
                                        ['rnid', '-i', item_path, '-H', aspect],
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
    
    # Salva in cache
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
    """Pulisce cache identità"""
    identity_cache.clear('all_identities')
    return jsonify({
        'success': True,
        'message': 'Cache identità pulita'
    })

@app.route('/api/cache/identities/status')
def cache_identities_status():
    """Stato cache identità"""
    stats = identity_cache.get_stats()
    return jsonify({
        'success': True,
        'cache': stats
    })

@app.route('/api/cache/identities/refresh', methods=['POST'])
def cache_identities_refresh():
    """Forza refresh cache"""
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
        
        suggested_name = request.form.get('suggested_name', '').strip()
        if not suggested_name:
            suggested_name = file.filename.split('.')[0] if '.' in file.filename else file.filename
        
        dest_path = os.path.join(RNS_MANAGER_STORAGE, suggested_name)
        file.save(dest_path)
        
        file_size = os.path.getsize(dest_path)
        if file_size != 64:
            os.remove(dest_path)
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        result = subprocess.run(
            ['rnid', '-i', dest_path, '--print-identity'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
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
        data = request.json
        identity_data = data.get('data', '').strip()
        format_type = data.get('format', 'hex')
        suggested_name = data.get('suggested_name', '').strip()
        
        if not identity_data:
            return jsonify({'success': False, 'error': 'No identity data provided'})
        
        if not suggested_name:
            suggested_name = f"imported_{int(time.time())}"
        
        dest_path = os.path.join(RNS_MANAGER_STORAGE, suggested_name)
        
        cmd_parts = ['rnid']
        if format_type == 'base32':
            cmd_parts.append('-B')
        elif format_type == 'base64':
            cmd_parts.append('-b')
        
        cmd_parts.extend(['-m', identity_data, '-P', '--print-identity', '--export', '-w', dest_path])
        
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({'success': False, 'error': f'Import failed: {result.stderr[:200]}'})
        
        if not os.path.exists(dest_path):
            return jsonify({'success': False, 'error': f'File non creato in {dest_path}'})
        
        file_size = os.path.getsize(dest_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        verify_result = subprocess.run(
            ['rnid', '-i', dest_path, '--print-identity'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if verify_result.returncode != 0:
            os.remove(dest_path)
            return jsonify({'success': False, 'error': 'Il file salvato non è un\'identità valida'})
        
        rns_hash = None
        for line in verify_result.stdout.split('\n'):
            if 'Loaded Identity <' in line:
                start = line.find('<') + 1
                end = line.find('>', start)
                if start > 0 and end > start:
                    rns_hash = line[start:end]
                    break
        
        return jsonify({
            'success': True,
            'message': f'Identità importata come {suggested_name}',
            'name': suggested_name,
            'path': dest_path,
            'info': result.stdout,
            'size': file_size,
            'format': format_type,
            'rns_hash': rns_hash
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Errore: {str(e)}'})

@app.route('/api/identities/export', methods=['POST'])
def export_identity():
    try:
        data = request.json
        identity_path = data.get('path', '')
        format_type = data.get('format', 'hex')
        
        if not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'File identità non trovato'})
        
        file_size = os.path.getsize(identity_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        cmd = ['rnid']
        if format_type == 'base64':
            cmd.append('-b')
        elif format_type == 'base32':
            cmd.append('-B')
        
        cmd.extend(['-i', identity_path, '--export'])
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({'success': False, 'error': f'Errore: {result.stderr[:100]}'})
        
        exported_data = result.stdout.strip()
        lines = exported_data.split('\n')
        clean_data = ""
        
        for line in lines:
            if "Exported Identity : " in line:
                clean_line = line.split("Exported Identity : ")[-1].strip()
                clean_line = re.sub(r'\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]', '', clean_line).strip()
                clean_data = clean_line
                break
        
        if not clean_data and lines:
            clean_data = lines[-1].strip()
            clean_data = re.sub(r'\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]', '', clean_data).strip()
        
        return jsonify({
            'success': True,
            'data': clean_data,
            'format': format_type,
            'path': identity_path,
            'length': len(clean_data)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/generate', methods=['POST'])
def generate_identity():
    try:
        data = request.json
        name = data.get('name', 'identity').strip()
        
        if '.' in name:
            name = name.split('.')[0]
        
        dest_path = os.path.join(RNS_MANAGER_STORAGE, name)
        
        result = subprocess.run(
            ['rnid', '-g', dest_path, '--print-identity', '-P'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({'success': False, 'error': 'Errore nella generazione'})
        
        file_size = os.path.getsize(dest_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File generato di {file_size} bytes (dovrebbe essere 64)'})
        
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
        data = request.json
        identity_path = data.get('path', '')
        aspect = data.get('aspect', '')
        
        if not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'File identità non trovato'})
        
        file_size = os.path.getsize(identity_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        result = subprocess.run(
            ['rnid', '-i', identity_path, '--print-identity', '-P'],
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
        
        if aspect and aspect in RNS_ASPECTS:
            hash_result = subprocess.run(
                ['rnid', '-i', identity_path, '-H', aspect],
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

@app.route('/api/upload/temp', methods=['POST'])
def upload_temp_file():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'})
        
        temp_dir = os.path.join(tempfile.gettempdir(), 'rnid_web')
        os.makedirs(temp_dir, exist_ok=True)
        
        unique_id = str(uuid.uuid4())[:8]
        original_name = file.filename
        safe_name = ''.join(c for c in original_name if c.isalnum() or c in '._- ')
        
        temp_filename = f"{unique_id}_{safe_name}"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        file.save(temp_path)
        
        return jsonify({
            'success': True,
            'temp_path': temp_path,
            'original_name': original_name,
            'output_dir': DOWNLOADS_DIR,
            'message': 'File salvato temporaneamente'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cleanup/temp', methods=['POST'])
def cleanup_temp_file():
    try:
        data = request.json
        temp_path = data.get('temp_path', '')
        
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
            return jsonify({'success': True, 'message': 'File temporaneo rimosso'})
        
        return jsonify({'success': False, 'error': 'File non trovato'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ============================================
# === COMANDI RNS (rnstatus, rnpath, rnprobe) ===
# ============================================

@app.route('/api/rns/status')
def rns_status():
    try:
        result = subprocess.run(
            ['rnstatus'],
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
        
        print(f"[DEBUG] rnpath richiesto per destinazione: '{destination}'")
        
        if destination and destination.strip():
            # rnpath aspetta l'hash come argomento
            cmd = ['rnpath', destination.strip()]
            print(f"[DEBUG] Esecuzione comando: {' '.join(cmd)}")
        else:
            # Se nessuna destinazione, mostra tutte le route
            cmd = ['rnpath']
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
        
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr,
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
        data = request.json
        destination = data.get('destination', '')
        aspect = data.get('aspect', 'rnstransport.probe')
        
        if not destination:
            return jsonify({'success': False, 'error': 'Nessuna destinazione specificata'})
        
        # Comando corretto: rnprobe <aspect> <destination>
        cmd = ['rnprobe', aspect, destination]
        
        print(f"[DEBUG] Esecuzione probe: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        print(f"[DEBUG] Probe returncode: {result.returncode}")
        print(f"[DEBUG] Probe stdout: {result.stdout[:200]}")
        
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr,
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
        data = request.json
        identity_path = data.get('identity_path', '')
        aspect = data.get('aspect', 'rnstransport.probe')
        
        if not identity_path or not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'Identità non trovata'})
        
        if aspect not in RNS_ASPECTS:
            return jsonify({'success': False, 'error': f'Aspect non valido: {aspect}'})
        
        # Prima ottieni l'hash dell'aspect
        hash_result = subprocess.run(
            ['rnid', '-i', identity_path, '-H', aspect],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if hash_result.returncode != 0:
            return jsonify({'success': False, 'error': 'Impossibile calcolare hash aspect'})
        
        # Estrai l'hash
        dest_hash = None
        for line in hash_result.stdout.split('\n'):
            if 'Destination hash:' in line:
                dest_hash = line.split('Destination hash:')[-1].strip()
                break
        
        if not dest_hash:
            return jsonify({'success': False, 'error': 'Hash non trovato'})
        
        # Esegui probe
        probe_result = subprocess.run(
            ['rnprobe', dest_hash],
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
        data = request.json
        identity_hash = data.get('destination', '')  # Qui arriva l'identity hash
        
        if not identity_hash:
            return jsonify({'success': False, 'error': 'Nessuna identità specificata'})
        
        # Comando rnpath con flag -p usando l'identity hash
        cmd = ['rnpath', '-p', identity_hash]
        
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
        hash_value = request.args.get('hash', '')
        if not hash_value:
            return jsonify({'success': False, 'error': 'Nessun hash specificato'})
        
        # Cerca nelle directory storage
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
                    
                    if os.path.isfile(item_path) and os.path.getsize(item_path) == 64:
                        # Verifica se questa identità corrisponde all'hash
                        result = subprocess.run(
                            ['rnid', '-i', item_path, '--print-identity'],
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
                                        if hash_value in identity_hash:
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
# === AVVIO SERVER ===
# ============================================

if __name__ == '__main__':
    print("=" * 60)
    print("RNID Web Interface + RNS Aspect Monitor")
    print("=" * 60)
    print("\n📁 Directory configurate:")
    for key, path in USER_DIRECTORIES.items():
        expanded = os.path.expanduser(path)
        print(f"  {key}: {expanded}")
    
    print(f"\n📁 Downloads: {DOWNLOADS_DIR}")
    print(f"📁 Cache: {CACHE_DIR}")
    print("\n🌐 Accesso:")
    print(f"  http://localhost:5000/ - Identity Manager")
    print(f"  http://localhost:5000/monitor - Aspect Monitor")
    print("\n🚀 Avvio server...\n")
    
    try:
        app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False, threaded=True)
    except KeyboardInterrupt:
        print("\n🛑 Arresto server...")
        # Ferma cache
        announce_cache.stop()
        if monitor_process and monitor_process.is_alive():
            monitor_process.terminate()
            monitor_process.join(timeout=5)
        try:
            os.unlink(SOCKET_PATH)
        except:
            pass
        print("✅ Server fermato")