#!/usr/bin/env python3
"""
Modulo per il monitoraggio degli annunci RNS
Versione con contatori centralizzati e DATI RADIO COMPLETI
"""

import os
import json
import time
import threading
import queue
import socket
import multiprocessing
import sqlite3
from datetime import datetime, timedelta

# Costanti
SOCKET_PATH = os.path.expanduser("~/.rns_manager/rns_monitor.sock")

# Aspect uguali all'originale
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
# === SQLITE CACHE ===
# ============================================
class SQLiteAnnounceCache:
    """Cache persistente su SQLite per annunci RNS - VELOCE E SCALABILE"""
    
    def __init__(self, cache_dir, max_age_days=7, max_size=10000):
        self.db_path = os.path.join(cache_dir, 'announces.db')
        self.max_age_days = max_age_days
        self.max_size = max_size
        self._init_db()
        
        # Thread per cleanup periodico
        self.running = True
        self.cleanup_thread = threading.Thread(target=self._auto_cleanup, daemon=True)
        self.cleanup_thread.start()
        
        print(f"📦 SQLite Cache: {self.db_path}")
    
    def _init_db(self):
        """Inizializza database SQLite"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Tabella principale annunci
        c.execute('''
            CREATE TABLE IF NOT EXISTS announces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                announce_id INTEGER,                    -- ID originale del contatore
                timestamp REAL NOT NULL,
                dest_hash TEXT NOT NULL,
                dest_short TEXT,
                packet_hash TEXT,
                identity_hash TEXT,
                aspect TEXT,
                hops TEXT,
                interface TEXT,
                via TEXT,
                ip TEXT,
                port INTEGER,
                data TEXT,
                data_length INTEGER,
                has_identity BOOLEAN,
                
                -- 🔥 DATI RADIO (NUOVI)
                rssi REAL,
                snr REAL,
                q REAL,
                
                -- JSON completo (per debug)
                full_data TEXT,
                
                -- Indici
                UNIQUE(timestamp, dest_hash, packet_hash) ON CONFLICT REPLACE
            )
        ''')
        
        # Indici per query veloci
        c.execute('CREATE INDEX IF NOT EXISTS idx_aspect ON announces(aspect)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_dest ON announces(dest_hash)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_identity ON announces(identity_hash)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_time ON announces(timestamp)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_rssi ON announces(rssi)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_snr ON announces(snr)')
        
        # Tabella per statistiche aggregate
        c.execute('''
            CREATE TABLE IF NOT EXISTS announce_stats (
                dest_hash TEXT PRIMARY KEY,
                first_seen REAL,
                last_seen REAL,
                announce_count INTEGER,
                avg_hops REAL,
                avg_rssi REAL,
                avg_snr REAL,
                avg_q REAL,
                last_aspect TEXT,
                last_interface TEXT
            )
        ''')
        
        conn.commit()
        conn.close()
        print(f"✅ Database annunci SQLite inizializzato")
    
    def add_announce(self, announce):
        """Aggiunge annuncio al database SQLite"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # Estrai dati
            timestamp = announce.get('timestamp', time.time())
            dest_hash = announce.get('dest_hash', '')
            packet_hash = announce.get('packet_hash', '')
            
            # 🔥 DATI RADIO
            rssi = announce.get('rssi')
            snr = announce.get('snr')
            q = announce.get('q')
            
            # JSON completo
            full_data = json.dumps(announce)
            
            # Inserisci annuncio
            c.execute('''
                INSERT OR REPLACE INTO announces 
                (announce_id, timestamp, dest_hash, dest_short, packet_hash, 
                 identity_hash, aspect, hops, interface, via, ip, port, 
                 data, data_length, has_identity, rssi, snr, q, full_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                announce.get('id'),
                timestamp,
                dest_hash,
                announce.get('dest_short'),
                packet_hash,
                announce.get('identity_hash'),
                announce.get('aspect'),
                announce.get('hops'),
                announce.get('interface'),
                announce.get('via'),
                announce.get('ip'),
                announce.get('port'),
                announce.get('data'),
                announce.get('data_length'),
                1 if announce.get('has_identity') else 0,
                rssi, snr, q,
                full_data
            ))
            
            # Aggiorna statistiche aggregate
            c.execute('''
                INSERT INTO announce_stats 
                (dest_hash, first_seen, last_seen, announce_count, 
                 avg_hops, avg_rssi, avg_snr, avg_q, last_aspect, last_interface)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dest_hash) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    announce_count = announce_count + 1,
                    avg_hops = (avg_hops * announce_count + excluded.avg_hops) / (announce_count + 1),
                    avg_rssi = (avg_rssi * announce_count + excluded.avg_rssi) / (announce_count + 1),
                    avg_snr = (avg_snr * announce_count + excluded.avg_snr) / (announce_count + 1),
                    avg_q = (avg_q * announce_count + excluded.avg_q) / (announce_count + 1),
                    last_aspect = excluded.last_aspect,
                    last_interface = excluded.last_interface
            ''', (
                dest_hash,
                timestamp, timestamp,
                announce.get('hops'),
                rssi, snr, q,
                announce.get('aspect'),
                announce.get('interface')
            ))
            
            conn.commit()
            conn.close()
            
            # Cleanup se necessario (in background)
            if self._should_cleanup():
                threading.Thread(target=self._cleanup_old, daemon=True).start()
            
            return True
            
        except Exception as e:
            print(f"❌ Errore inserimento SQLite: {e}")
            return False
    
    def get_announces(self, aspect=None, dest_hash=None, identity_hash=None,
                      min_rssi=None, since=None, limit=None, offset=0, sort='time_desc'):
        """Recupera annunci con filtri avanzati"""
        
        print(f"🔍 SQLiteAnnounceCache.get_announces()")
        print(f"   aspect: {aspect}")
        print(f"   limit: {limit}")
        print(f"   offset: {offset}")
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Costruisci query
        query = "SELECT * FROM announces WHERE 1=1"
        params = []
        
        if aspect:
            query += " AND aspect = ?"
            params.append(aspect)
            print(f"   filtro aspect: {aspect}")
        
        if dest_hash:
            query += " AND dest_hash LIKE ?"
            params.append(f'%{dest_hash}%')
            print(f"   filtro dest: {dest_hash}")
        
        if identity_hash:
            query += " AND identity_hash LIKE ?"
            params.append(f'%{identity_hash}%')
            print(f"   filtro identity: {identity_hash}")
        
        if min_rssi is not None:
            query += " AND rssi >= ?"
            params.append(min_rssi)
        
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        
        # Ordinamento
        sort_map = {
            'time_desc': ('timestamp', 'DESC'),
            'time_asc': ('timestamp', 'ASC'),
            'rssi_desc': ('rssi', 'DESC'),
            'rssi_asc': ('rssi', 'ASC'),
            'snr_desc': ('snr', 'DESC'),
            'snr_asc': ('snr', 'ASC'),
            'hops_desc': ('CAST(hops AS INTEGER)', 'DESC'),
            'hops_asc': ('CAST(hops AS INTEGER)', 'ASC'),
        }
        sort_col, sort_dir = sort_map.get(sort, ('timestamp', 'DESC'))
        query += f" ORDER BY {sort_col} {sort_dir}"
        
        if limit and limit > 0:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            print(f"   LIMIT {limit}, OFFSET {offset}")
        elif offset > 0:
            query += " OFFSET ?"
            params.append(offset)
            print(f"   OFFSET {offset} (no limit)")
        else:
            print(f"   NESSUN LIMITE (tutti gli annunci)")
        
        print(f"   Query: {query}")
        print(f"   Params: {params}")
        
        c.execute(query, params)
        rows = c.fetchall()
        conn.close()
        
        # Converti in dizionari
        results = [dict(row) for row in rows]
        
        print(f"   Risultati: {len(results)} annunci")
        
        return results
    
    def count_announces(self, aspect=None, dest_hash=None, identity_hash=None,
                        min_rssi=None, since=None):
        """Conta annunci con filtri"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        query = "SELECT COUNT(*) FROM announces WHERE 1=1"
        params = []
        
        if aspect:
            query += " AND aspect = ?"
            params.append(aspect)
        
        if dest_hash:
            query += " AND dest_hash LIKE ?"
            params.append(f'%{dest_hash}%')
        
        if identity_hash:
            query += " AND identity_hash LIKE ?"
            params.append(f'%{identity_hash}%')
        
        if min_rssi is not None:
            query += " AND rssi >= ?"
            params.append(min_rssi)
        
        if since:
            query += " AND timestamp >= ?"
            params.append(since)
        
        c.execute(query, params)
        count = c.fetchone()[0]
        conn.close()
        
        return count
    
    def get_stats(self):
        """Statistiche database"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Conteggio totale
        c.execute("SELECT COUNT(*) FROM announces")
        total = c.fetchone()[0]
        
        # Statistiche per aspect
        c.execute('''
            SELECT aspect, COUNT(*) as count 
            FROM announces 
            GROUP BY aspect 
            ORDER BY count DESC 
            LIMIT 10
        ''')
        aspects = [{'aspect': row[0], 'count': row[1]} for row in c.fetchall()]
        
        # Statistiche radio
        c.execute('''
            SELECT 
                AVG(rssi) as avg_rssi,
                MAX(rssi) as max_rssi,
                MIN(rssi) as min_rssi,
                AVG(snr) as avg_snr,
                MAX(snr) as max_snr,
                MIN(snr) as min_snr,
                AVG(q) as avg_q
            FROM announces 
            WHERE rssi IS NOT NULL
        ''')
        radio = c.fetchone()
        
        # Primo e ultimo timestamp
        c.execute("SELECT MIN(timestamp), MAX(timestamp) FROM announces")
        time_range = c.fetchone()
        
        conn.close()
        
        return {
            'total_announces': total,
            'aspects': aspects,
            'radio': {
                'avg_rssi': radio[0],
                'max_rssi': radio[1],
                'min_rssi': radio[2],
                'avg_snr': radio[3],
                'max_snr': radio[4],
                'min_snr': radio[5],
                'avg_q': radio[6]
            },
            'time_range': {
                'first': time_range[0],
                'last': time_range[1],
                'span_days': (time.time() - time_range[0]) / 86400 if time_range[0] else 0
            }
        }
    
    def get_peer_stats(self, dest_hash):
        """Statistiche per un peer specifico"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            SELECT * FROM announce_stats WHERE dest_hash = ?
        ''', (dest_hash,))
        
        row = c.fetchone()
        conn.close()
        
        if row:
            return {
                'dest_hash': row[0],
                'first_seen': row[1],
                'last_seen': row[2],
                'announce_count': row[3],
                'avg_hops': row[4],
                'avg_rssi': row[5],
                'avg_snr': row[6],
                'avg_q': row[7],
                'last_aspect': row[8],
                'last_interface': row[9]
            }
        return None
    
    def _should_cleanup(self):
        """Verifica se è ora di fare cleanup"""
        # Cleanup ogni 1000 inserimenti (semplice)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM announces")
        count = c.fetchone()[0]
        conn.close()
        
        return count > self.max_size
    
    def _cleanup_old(self):
        """Rimuove annunci vecchi"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # Rimuovi oltre max_size
            c.execute('''
                DELETE FROM announces 
                WHERE id NOT IN (
                    SELECT id FROM announces 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                )
            ''', (self.max_size,))
            
            # Rimuovi oltre max_age
            cutoff = time.time() - (self.max_age_days * 86400)
            c.execute("DELETE FROM announces WHERE timestamp < ?", (cutoff,))
            
            deleted = c.rowcount
            conn.commit()
            conn.close()
            
            if deleted:
                print(f"🧹 SQLite: rimossi {deleted} annunci vecchi")
                
        except Exception as e:
            print(f"❌ Errore cleanup SQLite: {e}")
    
    def cleanup_old(self, days=30):
        """Rimuovi annunci più vecchi di N giorni (metodo pubblico)"""
        cutoff = time.time() - (days * 86400)
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM announces WHERE timestamp < ?", (cutoff,))
        removed = c.rowcount
        conn.commit()
        conn.close()
        
        print(f"🧹 SQLite: rimossi {removed} annunci più vecchi di {days} giorni")
        return removed
    
    def vacuum(self):
        """Ottimizza database"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("VACUUM")
        conn.close()
        print("🧹 SQLite: VACUUM completato")
    
    def _auto_cleanup(self):
        """Thread di cleanup automatico"""
        while self.running:
            time.sleep(3600)  # Ogni ora
            self._cleanup_old()
    
    def clear(self):
        """Pulisce tutto il database"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM announces")
        c.execute("DELETE FROM announce_stats")
        conn.commit()
        conn.close()
        print("🧹 SQLite: database pulito")
    
    def stop(self):
        """Ferma thread"""
        self.running = False

# ============================================
# === PROCESSO MONITOR ===
# ============================================
def run_rns_monitor(socket_path, aspects):
    """Processo separato con il monitor RNS - ORA CON DATI RADIO COMPLETI"""
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
            self.cache = {}
            self.seen_packets = set()
            self.socket = sock
            self.ASPECTS = aspects
        
        def send_announce(self, data):
            try:
                message = json.dumps(data) + "\n"
                self.socket.send(message.encode('utf-8'))
            except:
                pass
        
        def get_packet_metadata(self, packet_hash):
            """Ottiene metadati dal pacchetto incluso RSSI/SNR/Q"""
            try:
                reticulum = RNS.Reticulum.get_instance()
                rssi = reticulum.get_packet_rssi(packet_hash)
                snr = reticulum.get_packet_snr(packet_hash)
                q = reticulum.get_packet_q(packet_hash)
                return rssi, snr, q
            except:
                return None, None, None
        
        def received_announce(self, destination_hash, announced_identity, app_data, announce_packet_hash):
            try:
                self.count += 1
                ts = datetime.now().strftime("%H:%M:%S")
                
                dest_hex = destination_hash.hex()
                packet_hex = announce_packet_hash.hex()[:32]
                
                # OTTIENI DATI RADIO DAL PACCHETTO
                rssi, snr, q = self.get_packet_metadata(announce_packet_hash)
                
                app_text = ""
                if app_data:
                    try:
                        text = app_data.decode('utf-8', errors='ignore').strip()
                        if text:
                            app_text = ' '.join(text.split())[:100]
                    except:
                        app_text = f"[{len(app_data)}b]"
                
                aspect = "unknown"
                identity_hash = ""
                
                if announced_identity:
                    identity_hash = announced_identity.hash.hex()
                    aspect = self._calculate_aspect_rnid(announced_identity, dest_hex)
                
                hops = "?"
                interface = "?"
                via = None
                ip = None
                port = None
                
                # OTTIENI INFORMAZIONI DI ROUTING E INTERFACCIA
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
                            
                            # TENTA DI OTTENERE IP E PORTA PER INTERFACCE TCP/UDP
                            if hasattr(entry[5], 'target_host'):
                                ip = entry[5].target_host
                                port = entry[5].target_port
                            
                            # OTTIENI VIA (next hop)
                            if len(entry) > 1 and entry[1]:
                                via = entry[1].hex()[:16] + "..."
                
                print(f"[{ts}] #{self.count:04d} id: {identity_hash[:32] if identity_hash else '?'*32} dest: {dest_hex[:32]}... hops: {hops} aspect: {aspect} iface: {interface} data: '{app_text[:50]}' RSSI:{rssi} SNR:{snr} Q:{q}")
                
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
                    'via': via,
                    'ip': ip,
                    'port': port,
                    'data': app_text,
                    'data_length': len(app_data) if app_data else 0,
                    'has_identity': announced_identity is not None,
                    'rssi': rssi,
                    'snr': snr,
                    'q': q
                }
                
                self.send_announce(announce_data)
                
            except Exception as e:
                print(f"[MONITOR] Errore: {e}")
        
        def _calculate_aspect_rnid(self, identity, target_dest_hex):
            identity_hash = identity.hash.hex()
            
            if identity_hash in self.cache:
                if target_dest_hex in self.cache[identity_hash]:
                    return self.cache[identity_hash][target_dest_hex]
            
            if identity_hash not in self.cache:
                self.cache[identity_hash] = {}
            
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
                    self.cache[identity_hash][calculated_hex] = aspect
                    
                    if calculated_hex == target_dest_hex:
                        return aspect
                        
                except Exception:
                    continue
            
            if len(target_dest_hex) == 32:
                if identity_hash.startswith(target_dest_hex[:len(identity_hash)]):
                    return "identity_hash"
            
            return "unknown"
    
    try:
        os.unlink(socket_path)
    except OSError:
        pass

    socket_dir = os.path.dirname(socket_path)
    if socket_dir and not os.path.exists(socket_dir):
        os.makedirs(socket_dir, exist_ok=True)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)

    server.listen(1)
    os.chmod(socket_path, 0o666)
    
    print(f"[MONITOR] In attesa di connessione Flask su {socket_path}...")
    
    try:
        client_socket, _ = server.accept()
        print("[MONITOR] ✅ Connesso a Flask")
        
        print("[MONITOR] Avvio Reticulum...")
        
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

# ============================================
# === MANAGER PER FLASK ===
# ============================================
class RNSMonitorManager:
    """Gestore per Flask con contatori centralizzati - ORA CON SQLITE"""
    
    def __init__(self, socket_path, aspects, cache_dir, max_history=1000):
        self.socket_path = socket_path
        self.aspects = aspects
        self.max_history = max_history
        
        # Processi e thread
        self.monitor_process = None
        self.listener_thread = None
        self.running = False
        
        # DATI CENTRALIZZATI - unico contatore per TUTTO
        self.announce_counter = 0  # Contatore unico globale
        self.announce_history = []  # Memoria recente (ridotta a 100)
        self.history_lock = threading.Lock()
        self.announce_queue = queue.Queue(maxsize=1000)
        
        # Cache SQLite
        self.announce_cache = SQLiteAnnounceCache(cache_dir) if cache_dir else None
        
        print(f"[MonitorManager] Inizializzato con SQLite: {cache_dir}")
    
    def start_monitor_process(self):
        """Avvia il processo monitor separato"""
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass
        
        self.monitor_process = multiprocessing.Process(
            target=run_rns_monitor,
            args=(self.socket_path, self.aspects),
            daemon=True
        )
        self.monitor_process.start()
        print(f"[MonitorManager] Monitor avviato (PID: {self.monitor_process.pid})")
        time.sleep(2)
        return True
    
    def start_listener(self):
        """Avvia il thread listener per il socket"""
        self.running = True
        self.listener_thread = threading.Thread(target=self._socket_listener, daemon=True)
        self.listener_thread.start()
        time.sleep(1)
        print("[MonitorManager] Listener avviato")
    
    def _socket_listener(self):
        """Thread che ascolta gli annunci dal socket"""
        sock = None
        buffer = ""
        
        print("[MonitorManager] Connessione al monitor...")
        
        while self.running:
            try:
                if sock is None:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(5)
                    sock.connect(self.socket_path)
                    sock.settimeout(None)
                    print("[MonitorManager] ✅ Connesso al monitor socket")
                
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
                            
                            with self.history_lock:
                                # INCREMENTA IL CONTATORE UNICO
                                self.announce_counter += 1
                                announce['id'] = self.announce_counter
                                
                                # Aggiungi alla history recente (solo ultimi 100)
                                self.announce_history.insert(0, announce)
                                if len(self.announce_history) > 100:  # Ridotto a 100
                                    self.announce_history.pop()
                                
                                # 🔥 Aggiungi alla cache SQLite
                                if self.announce_cache:
                                    self.announce_cache.add_announce(announce)
                            
                            # Metti in coda per lo streaming
                            try:
                                self.announce_queue.put_nowait(announce)
                            except queue.Full:
                                pass
                            
                            rssi = announce.get('rssi')
                            snr = announce.get('snr')
                            q = announce.get('q')
                            radio_info = f"RSSI:{rssi} SNR:{snr} Q:{q}" if any([rssi, snr, q]) else "no radio data"
                            
                            print(f"[MonitorManager] ✅ Annuncio #{self.announce_counter} - Aspect: {announce.get('aspect', 'unknown')} - {radio_info}")
                            
                        except json.JSONDecodeError:
                            continue
                            
            except (ConnectionRefusedError, FileNotFoundError):
                if sock:
                    sock.close()
                    sock = None
                time.sleep(2)
            except Exception as e:
                print(f"[MonitorManager] Errore socket: {e}")
                if sock:
                    sock.close()
                    sock = None
                time.sleep(2)
    
    def get_stats(self):
        """Restituisce statistiche unificate - ORA CON DATI SQLITE"""
        with self.history_lock:
            unique_sources = len({a.get('identity_hash', '') for a in self.announce_history if a.get('identity_hash')}) if self.announce_history else 0
            
            # 🔥 Statistiche da SQLite
            sqlite_stats = self.announce_cache.get_stats() if self.announce_cache else {}
            
            # Calcola statistiche radio dalla history recente
            radio_stats = {}
            if self.announce_history:
                rssi_values = [a.get('rssi') for a in self.announce_history if a.get('rssi') is not None]
                snr_values = [a.get('snr') for a in self.announce_history if a.get('snr') is not None]
                q_values = [a.get('q') for a in self.announce_history if a.get('q') is not None]
                
                radio_stats = {
                    'avg_rssi': sum(rssi_values)/len(rssi_values) if rssi_values else None,
                    'avg_snr': sum(snr_values)/len(snr_values) if snr_values else None,
                    'avg_q': sum(q_values)/len(q_values) if q_values else None,
                    'min_rssi': min(rssi_values) if rssi_values else None,
                    'max_rssi': max(rssi_values) if rssi_values else None,
                }
            
            return {
                'total_announces': self.announce_counter,
                'history_size': len(self.announce_history),
                'sqlite_total': sqlite_stats.get('total_announces', 0),
                'monitor_alive': self.monitor_process.is_alive() if self.monitor_process else False,
                'unique_sources': unique_sources,
                'radio_stats': radio_stats,
                'sqlite': sqlite_stats  # Statistiche complete da SQLite
            }
    
    def get_history(self, aspect_filter='all', limit=100, offset=0, search='', sort='time_desc', source='memory'):
        """
        Recupera storico annunci - ORA CON SQLITE
        """
        print(f"\n🔴🔴🔴 get_history CHIAMATO con source={source} 🔴🔴🔴")
        
        # 🟢 FIX: Se source è 'cache' e abbiamo la cache, usa SQLITE!
        if source == 'cache' and self.announce_cache:
            print("   ✅ usando SQLITE (_get_history_from_sqlite)")
            return self._get_history_from_sqlite(aspect_filter, limit, offset, search, sort)
        
        # Altrimenti usa memoria
        print("   ⚠️ usando MEMORIA (_get_history_from_memory)")
        return self._get_history_from_memory(aspect_filter, limit, offset, search, sort)
    
    def _get_history_from_memory(self, aspect_filter, limit, offset, search, sort):
        """Recupera storico dalla memoria recente (history)"""
        with self.history_lock:
            filtered = self.announce_history.copy()
        
        # Filtra per aspect
        if aspect_filter != 'all':
            if aspect_filter == 'unknown':
                filtered = [a for a in filtered if a.get('aspect') in ['unknown', None, '']]
            elif aspect_filter == 'known':
                filtered = [a for a in filtered if a.get('aspect') not in ['unknown', None, ''] and a.get('aspect') != 'identity_hash']
            else:
                filtered = [a for a in filtered if a.get('aspect') == aspect_filter]
        
        # Filtra per search
        if search:
            search_lower = search.lower()
            filtered = [a for a in filtered if 
                       search_lower in a.get('dest_hash', '').lower() or
                       search_lower in a.get('identity_hash', '').lower() or
                       search_lower in a.get('aspect', '').lower() or
                       search_lower in a.get('data', '').lower() or
                       search_lower in a.get('ip', '').lower() or
                       search_lower in a.get('interface', '').lower()]
        
        # Ordina
        sort_functions = {
            'time_asc': (lambda x: x.get('timestamp', 0), False),
            'time_desc': (lambda x: x.get('timestamp', 0), True),
            'hops_asc': (lambda x: int(x.get('hops', 0)) if str(x.get('hops', '0')).isdigit() else 999, False),
            'hops_desc': (lambda x: int(x.get('hops', 0)) if str(x.get('hops', '0')).isdigit() else 0, True),
            'aspect_asc': (lambda x: x.get('aspect', ''), False),
            'aspect_desc': (lambda x: x.get('aspect', ''), True),
            'identity_asc': (lambda x: x.get('identity_hash', ''), False),
            'identity_desc': (lambda x: x.get('identity_hash', ''), True),
            'rssi_desc': (lambda x: x.get('rssi', -999) or -999, True),
            'rssi_asc': (lambda x: x.get('rssi', 999) or 999, False),
            'snr_desc': (lambda x: x.get('snr', -999) or -999, True),
            'snr_asc': (lambda x: x.get('snr', 999) or 999, False),
        }
        
        key_func, reverse = sort_functions.get(sort, (lambda x: x.get('timestamp', 0), True))
        filtered.sort(key=key_func, reverse=reverse)
        
        total = len(filtered)
        paginated = filtered[offset:offset + limit]
        
        return {
            'announces': paginated,
            'total': total,
            'source': 'memory',
            'offset': offset,
            'limit': limit
        }
    
    def _get_history_from_sqlite(self, aspect_filter, limit, offset, search, sort):
        """Recupera storico da SQLite con filtri avanzati"""
        
        print("\n" + "="*60)
        print(f"🔴🔴🔴 _get_history_from_sqlite CHIAMATA!")
        print(f"📊 aspect_filter: {aspect_filter}")
        print(f"📊 limit: {limit}")
        print(f"📊 offset: {offset}")
        print(f"📊 search: {search}")
        print(f"📊 sort: {sort}")
        print("="*60 + "\n")
        
        # Converti aspect_filter
        aspect = None
        if aspect_filter not in ['all', 'unknown', 'known']:
            aspect = aspect_filter
        
        # Per search, cerchiamo in dest_hash e identity_hash
        dest_filter = search if search else None
        identity_filter = search if search else None
        
        print(f"📌 Parametri convertiti: aspect={aspect}, dest={dest_filter}, identity={identity_filter}")
        
        # Ottieni da SQLite
        announces = self.announce_cache.get_announces(
            aspect=aspect,
            dest_hash=dest_filter,
            identity_hash=identity_filter,
            limit=limit if limit and limit > 0 else None,
            offset=offset,
            sort=sort
        )
        
        print(f"✅ Trovati {len(announces)} annunci in SQLite")
        if len(announces) > 0:
            print(f"📝 Primo annuncio ID: {announces[0].get('id')}")
            print(f"📝 Primo annuncio Aspect: {announces[0].get('aspect')}")
        else:
            print("❌ NESSUN annuncio trovato in SQLite!")
        
        # Conteggio totale
        total = self.announce_cache.count_announces(
            aspect=aspect,
            dest_hash=dest_filter,
            identity_hash=identity_filter
        )
        
        print(f"📊 Totale conteggio: {total}")
        print("="*60 + "\n")
        
        return {
            'announces': announces,
            'total': total,
            'source': 'sqlite',
            'offset': offset,
            'limit': limit
        }
    
    def get_peer_details(self, dest_hash):
        """Ottieni dettagli completi di un peer specifico"""
        # Prima cerca nella history recente
        with self.history_lock:
            for a in self.announce_history:
                if a.get('dest_hash') == dest_hash:
                    return a
        
        # 🔥 Poi cerca in SQLite
        if self.announce_cache:
            results = self.announce_cache.get_announces(
                dest_hash=dest_hash,
                limit=1
            )
            if results:
                return results[0]
        
        return None
    
    def get_peer_statistics(self, dest_hash):
        """Ottieni statistiche aggregate per un peer"""
        if self.announce_cache:
            return self.announce_cache.get_peer_stats(dest_hash)
        return None
    
    def search_advanced(self, **kwargs):
        """Ricerca avanzata in SQLite"""
        if self.announce_cache:
            return self.announce_cache.get_announces(**kwargs)
        return []
    
    def clear_all(self):
        """Reset totale di tutti i dati"""
        with self.history_lock:
            self.announce_history = []
            self.announce_counter = 0
            
            # Svuota la coda
            while not self.announce_queue.empty():
                try:
                    self.announce_queue.get_nowait()
                except:
                    break
        
        # 🔥 Pulisci SQLite
        if self.announce_cache:
            self.announce_cache.clear()
            print("[MonitorManager] SQLite cache pulita")
        
        return True
    
    def stop(self):
        """Ferma tutti i processi e thread"""
        self.running = False
        
        # Ferma SQLite cache
        if self.announce_cache:
            self.announce_cache.stop()
        
        # Ferma processo monitor
        if self.monitor_process and self.monitor_process.is_alive():
            self.monitor_process.terminate()
            self.monitor_process.join(timeout=5)
        
        # Rimuovi socket
        try:
            os.unlink(self.socket_path)
        except:
            pass
        
        print("[MonitorManager] Monitor fermato")


def create_monitor_blueprint(monitor_manager):
    """Crea un blueprint Flask con le route del monitor - ORA CON SQLITE"""
    from flask import Blueprint, request, jsonify, Response, stream_with_context, render_template, make_response
    import uuid
    import json
    import time
    
    # Blueprint con prefisso /api/monitor
    monitor_bp = Blueprint('monitor', __name__, url_prefix='/api/monitor')
    
    # === ENDPOINT ESISTENTI ===
    
    @monitor_bp.route('')
    def monitor_page():
        response = make_response(render_template('monitor.html'))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    
    @monitor_bp.route('/stats')
    def stats():
        return jsonify({
            'success': True,
            **monitor_manager.get_stats()
        })
    
    @monitor_bp.route('/history')
    def history():
        aspect = request.args.get('aspect', 'all')
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        search = request.args.get('search', '').lower()
        sort = request.args.get('sort', 'time_desc')
        source = request.args.get('source', 'memory')  # 'memory' o 'sqlite'
        
        result = monitor_manager.get_history(
            aspect_filter=aspect,
            limit=limit,
            offset=offset,
            search=search,
            sort=sort,
            source=source
        )
        
        return jsonify({
            'success': True,
            **result
        })
    
    @monitor_bp.route('/peer/<dest_hash>')
    def peer_details(dest_hash):
        peer = monitor_manager.get_peer_details(dest_hash)
        if peer:
            return jsonify({'success': True, 'peer': peer})
        return jsonify({'success': False, 'error': 'Peer non trovato'}), 404
    
    @monitor_bp.route('/stream')
    def stream():
        def generate():
            client_id = str(uuid.uuid4())[:8]
            last_id = 0
            
            print(f"[Stream:{client_id}] Nuovo client connesso")
            
            # Invia header SSE
            yield "retry: 1000\n\n"
            
            # Invia storico recente
            with monitor_manager.history_lock:
                recent = list(reversed(monitor_manager.announce_history[-20:]))
            
            for announce in recent:
                if announce.get('id', 0) > last_id:
                    last_id = announce.get('id', 0)
                    yield f"data: {json.dumps(announce)}\n\n"
            
            # Streaming in tempo reale
            while True:
                try:
                    announce = monitor_manager.announce_queue.get(timeout=30)
                    if announce.get('id', 0) > last_id:
                        last_id = announce.get('id', 0)
                        yield f"data: {json.dumps(announce)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
                except GeneratorExit:
                    print(f"[Stream:{client_id}] Client disconnesso")
                    break
        
        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no'
            }
        )
    
    @monitor_bp.route('/clear', methods=['POST'])
    def clear():
        monitor_manager.clear_all()
        return jsonify({'success': True, 'message': 'TUTTO PULITO!'})
    
    @monitor_bp.route('/reset-all', methods=['POST'])
    def reset_all():
        """Reset totale di storico e cache"""
        try:
            print("\n" + "="*60)
            print("🔄 RESET TOTALE RICHIESTO")
            print("="*60)
            
            monitor_manager.clear_all()
            
            if monitor_manager.announce_cache:
                monitor_manager.announce_cache.clear()
                
                # Elimina file cache
                cache_file = monitor_manager.announce_cache.db_path
                if os.path.exists(cache_file):
                    os.remove(cache_file)
                    print(f"[✓] File cache eliminato: {cache_file}")
            
            print("="*60)
            print("✅ RESET TOTALE COMPLETATO")
            print("="*60 + "\n")
            
            return jsonify({
                'success': True,
                'message': 'Reset totale completato',
                'announces': 0,
                'identities': 0
            })
            
        except Exception as e:
            print(f"[!] Errore durante reset: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            })
    
    # ============================================
    # === 🔥 NUOVI ENDPOINT PER SQLITE ===
    # ============================================
    
    @monitor_bp.route('/sqlite/stats')
    def sqlite_stats():
        """Statistiche dettagliate del database SQLite"""
        if not monitor_manager.announce_cache:
            return jsonify({'success': False, 'error': 'SQLite non disponibile'}), 404
        
        stats = monitor_manager.announce_cache.get_stats()
        return jsonify({
            'success': True,
            'stats': stats
        })
    
    @monitor_bp.route('/sqlite/peer/<dest_hash>/stats')
    def sqlite_peer_stats(dest_hash):
        """Statistiche aggregate per un peer specifico"""
        if not monitor_manager.announce_cache:
            return jsonify({'success': False, 'error': 'SQLite non disponibile'}), 404
        
        stats = monitor_manager.announce_cache.get_peer_stats(dest_hash)
        if stats:
            return jsonify({
                'success': True,
                'stats': stats
            })
        
        return jsonify({'success': False, 'error': 'Peer non trovato'}), 404
    
    @monitor_bp.route('/sqlite/search')
    def sqlite_search():
        """Ricerca avanzata in SQLite con tutti i filtri"""
        if not monitor_manager.announce_cache:
            return jsonify({'success': False, 'error': 'SQLite non disponibile'}), 404
        
        # Parametri di ricerca
        aspect = request.args.get('aspect')
        dest = request.args.get('dest')
        identity = request.args.get('identity')
        min_rssi = request.args.get('min_rssi', type=float)
        since = request.args.get('since', type=float)
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        sort = request.args.get('sort', 'time_desc')
        
        # Costruisci filtri
        results = monitor_manager.announce_cache.get_announces(
            aspect=aspect if aspect and aspect != 'all' else None,
            dest_hash=dest,
            identity_hash=identity,
            min_rssi=min_rssi,
            since=since,
            limit=limit,
            offset=offset,
            sort=sort
        )
        
        return jsonify({
            'success': True,
            'results': results,
            'count': len(results),
            'filters': {
                'aspect': aspect,
                'dest': dest,
                'identity': identity,
                'min_rssi': min_rssi,
                'since': since,
                'limit': limit,
                'offset': offset,
                'sort': sort
            }
        })
    
    @monitor_bp.route('/sqlite/cleanup', methods=['POST'])
    def sqlite_cleanup():
        """Forza cleanup manuale del database"""
        if not monitor_manager.announce_cache:
            return jsonify({'success': False, 'error': 'SQLite non disponibile'}), 404
        
        days = int(request.json.get('days', 30)) if request.json else 30
        
        removed = monitor_manager.announce_cache.cleanup_old(days)
        
        return jsonify({
            'success': True,
            'removed': removed,
            'message': f'Rimossi {removed} annunci più vecchi di {days} giorni'
        })
    
    @monitor_bp.route('/sqlite/vacuum', methods=['POST'])
    def sqlite_vacuum():
        """Ottimizza database (VACUUM)"""
        if not monitor_manager.announce_cache:
            return jsonify({'success': False, 'error': 'SQLite non disponibile'}), 404
        
        size_before = os.path.getsize(monitor_manager.announce_cache.db_path) / (1024*1024)
        
        monitor_manager.announce_cache.vacuum()
        
        size_after = os.path.getsize(monitor_manager.announce_cache.db_path) / (1024*1024)
        
        return jsonify({
            'success': True,
            'size_before_mb': round(size_before, 2),
            'size_after_mb': round(size_after, 2),
            'reduced_mb': round(size_before - size_after, 2)
        })
    
    # ============================================
    # === ENDPOINT PER LXMF-CHAT ===
    # ============================================

    @monitor_bp.route('/launch-lxmfchat', methods=['GET'])
    def launch_lxmfchat():
        """Avvia LXMF-Chat (rns_lxmf.py) e apre il browser"""
        print("\n" + "="*60)
        print("🔴🔴🔴 LAUNCH LXMF-CHAT CHIAMATO 🔴🔴🔴")
        print("="*60)
        
        try:
            import subprocess
            import threading
            import os
            import sys
            import time
            import webbrowser
            import requests
            
            print("📦 Import completati")
            
            def run_lxmfchat():
                try:
                    print("\n🚀 THREAD AVVIATO")
                    
                    # Trova il percorso BASE (dove sta rns_manager.py)
                    # Risali di un livello da modules/ alla root del progetto
                    current_dir = os.path.dirname(os.path.abspath(__file__))  # .../modules/
                    base_dir = os.path.dirname(current_dir)  # .../rnsManagerEvo/
                    
                    # Percorso ASSOLUTO a rns_lxmf.py (nella root)
                    script_path = os.path.join(base_dir, 'rns_lxmf.py')
                    
                    print(f"📁 Base dir: {base_dir}")
                    print(f"📁 Script: {script_path}")
                    print(f"📁 Esiste: {os.path.exists(script_path)}")
                    
                    if not os.path.exists(script_path):
                        print(f"❌ Script non trovato!")
                        return
                    
                    python_exe = sys.executable
                    print(f"🐍 Python: {python_exe}")
                    
                    # AVVIA IL PROCESSO NELLA DIRECTORY BASE (dove stanno i templates/)
                    print(f"🚀 Avvio processo in {base_dir}...")
                    process = subprocess.Popen(
                        [python_exe, script_path],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                        text=True,
                        bufsize=1,
                        cwd=base_dir  # ⚠️ LAVORA NELLA ROOT!
                    )
                    
                    print(f"✅ Processo avviato PID: {process.pid}")
                    
                    # Trova la porta dall'output
                    print("\n🔍 Cerco porta nell'output...")
                    port = None
                    start_time = time.time()
                    
                    while time.time() - start_time < 15:
                        line = process.stdout.readline()
                        if not line:
                            time.sleep(0.1)
                            continue
                        
                        line = line.strip()
                        print(f"STDOUT: {line}")
                        
                        if "http://localhost:" in line:
                            import re
                            match = re.search(r'http://localhost:(\d+)/chat', line)
                            if match:
                                port = int(match.group(1))
                                print(f"\n✅ Trovata porta {port}!")
                                break
                    
                    if not port:
                        print("\n❌ Porta non trovata nell'output")
                        stderr = process.stderr.read()
                        if stderr:
                            print(f"\n📤 STDERR:\n{stderr}")
                        return
                    
                    # Aspetta che il server sia pronto
                    print(f"\n⏳ Attendo che il server su porta {port} sia pronto...")
                    start_time = time.time()
                    
                    while time.time() - start_time < 30:
                        try:
                            r = requests.get(f'http://localhost:{port}/chat', timeout=1)
                            if r.status_code == 200:
                                elapsed = time.time() - start_time
                                print(f"\n✅ Server pronto dopo {elapsed:.1f} secondi!")
                                break
                        except:
                            print(f"   ⏳ Attesa... ({int(time.time()-start_time)}s)")
                            time.sleep(1)
                    else:
                        print(f"\n❌ Server non risponde dopo 30 secondi")
                        return
                    
                    # Apri browser
                    url = f'http://localhost:{port}/chat'
                    print(f"\n🌐 Apro browser: {url}")
                    webbrowser.open(url)
                    print(f"✅ Browser aperto")
                    
                except Exception as e:
                    print(f"\n💥 ECCEZIONE NEL THREAD: {e}")
                    import traceback
                    traceback.print_exc()
            
            print("🎯 Avvio thread...")
            thread = threading.Thread(target=run_lxmfchat, daemon=True)
            thread.start()
            print("✅ Thread avviato")
            
            return jsonify({'success': True, 'message': 'Avvio LXMF-Chat...'})
        
        except Exception as e:
            print(f"\n💥 ECCEZIONE ENDPOINT: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500
    
    return monitor_bp


# ============================================
# === FUNZIONE PER INIZIALIZZARE IL MONITOR ===
# ============================================
def init_monitor(app, cache_dir):
    """Inizializza il monitor e restituisce il manager"""
    
    # Crea manager
    manager = RNSMonitorManager(
        socket_path=SOCKET_PATH,
        aspects=RNS_ASPECTS,
        cache_dir=cache_dir
    )
    
    # Avvia processi
    manager.start_monitor_process()
    manager.start_listener()
    
    return manager