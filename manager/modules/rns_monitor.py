#!/usr/bin/env python3
"""
Modulo per il monitoraggio degli annunci RNS
Versione con contatori centralizzati e DATI RADIO COMPLETI
"""

import os
import copy
import json
import codecs
import time
import threading
import queue
import socket
import multiprocessing
import sqlite3
import sys
from datetime import datetime, timedelta

try:
    from version import APP_VERSION
except ImportError:
    APP_VERSION = "1.2.1"

STATS_CACHE_TTL = float(os.environ.get('RNS_STATS_CACHE_TTL', '30'))

if sys.platform != 'win32':
    try:
        multiprocessing.set_start_method('fork', force=True)
    except (RuntimeError, ValueError):
        pass
# ======================================

# ============================================
# === DETECTION SISTEMA OPERATIVO ===
# ============================================
IS_WINDOWS = sys.platform == 'win32'
print(f"[DEBUG] Sistema operativo: {'Windows' if IS_WINDOWS else 'Linux/Unix'}")

# ============================================
# === SOCKET PATH ===
# ============================================
if IS_WINDOWS:
    # Su Windows usa TCP su localhost (porta 5011)
    SOCKET_HOST = '127.0.0.1'
    SOCKET_PORT = 5011
    SOCKET_PATH = None  # Non usato su Windows
    print(f"[DEBUG] Socket TCP: {SOCKET_HOST}:{SOCKET_PORT}")
else:
    # Su Linux/Unix usa socket UNIX
    SOCKET_PATH = os.path.expanduser("~/.rns_manager/rns_monitor.sock")
    SOCKET_HOST = None
    SOCKET_PORT = None
    print(f"[DEBUG] Socket UNIX: {SOCKET_PATH}")

# Aspect uguali all'originale
RNS_ASPECTS = [
    "lxmf.delivery","nomadnetwork.node","lxst.telephony","lxst.room","call.audio","retibbs.bbs","rrc.hub","lxmf.propagation",
    "rnstransport.probe","rnstransport.info.blackhole","rnsh","rncp","rncp.receive","rnsh.listen","rnsh.default",
    "rns_unit_tests.link.establish","atak.cot","haven.status","demo.msg",
    "rnstransport.discovery.interface","reticulum_console.echo",
    "rnstransport.tunnel.synthesize","rnx.execute","map-data-v1",
    "rnstransport.path.request","nostr.relay","microrn.mgmt",
    "rnstransport.remote.management",    
    "rnstransport.network.instance",
    "rnstransport.network","rns.sofia","aurora.echo","aurora.test","aurora.resorce","aurora.files",
    "styrene.delivery","r3akt.emergency",
    "rns.rec.gateway","rns.rec.wallet",
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
    "qortal-hub-v3.presence.v3",
    "qortal-hub-v3.community-stun.v1","qortal-hub-v3.community-masque-relay.v1","qortal-hub-v3.qapp-backend.v1",
]

# ============================================
# === SQLITE CACHE ===
# ============================================
class SQLiteAnnounceCache:
    """Cache persistente su SQLite per annunci RNS - VELOCE E SCALABILE"""
    
    def __init__(self, cache_dir, max_age_days=7, max_size=100000):
        self.db_path = os.path.join(cache_dir, 'announces.db')
        self.max_age_days = max_age_days
        self.max_size = max_size
        self._insert_since_check = 0
        self._write_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._cleanup_running = False
        self._init_db()
        
        # Thread per cleanup periodico
        self.running = True
        self.cleanup_thread = threading.Thread(target=self._auto_cleanup, daemon=True)
        self.cleanup_thread.start()
        
        print(f"📦 SQLite Cache: {self.db_path}")
    
    def _connect(self):
        """Connessione SQLite con WAL/busy timeout per uso concorrente."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA busy_timeout=10000')
        except sqlite3.Error:
            pass
        return conn

    def _init_db(self):
        """Inizializza database SQLite"""
        conn = self._connect()
        try:
            conn.execute('PRAGMA journal_mode=WAL')
        except sqlite3.Error:
            pass
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
        c.execute('CREATE INDEX IF NOT EXISTS idx_identity_time ON announces(identity_hash, timestamp DESC)')
        
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
            conn = self._connect()
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
            # Cleanup periodico: evita un COUNT(*) per ogni singolo annuncio.
            with self._write_lock:
                self._insert_since_check += 1
                due = self._insert_since_check >= 250
                if due:
                    self._insert_since_check = 0
            if due and self._should_cleanup():
                self._schedule_cleanup()
            
            return True
            
        except Exception as e:
            print(f"❌ Errore inserimento SQLite: {e}")
            return False
    
    def get_announces(self, aspect=None, dest_hash=None, identity_hash=None,
                      min_rssi=None, since=None, search=None, limit=None, offset=0, sort='time_desc'):
        """Recupera annunci con filtri avanzati"""
        
        print(f"🔍 SQLiteAnnounceCache.get_announces()")
        print(f"   aspect: {aspect}")
        print(f"   limit: {limit}")
        print(f"   offset: {offset}")
        
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        # Costruisci query
        query = "SELECT * FROM announces WHERE 1=1"
        params = []
        
        if aspect == 'unknown':
            query += " AND (aspect IS NULL OR aspect = '' OR aspect = 'unknown')"
            print("   filtro aspect: unknown")
        elif aspect == 'known':
            query += " AND aspect IS NOT NULL AND aspect != '' AND aspect != 'unknown' AND aspect != 'identity_hash'"
            print("   filtro aspect: known")
        elif aspect:
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

        if search:
            like = f'%{search}%'
            query += (" AND (dest_hash LIKE ? OR identity_hash LIKE ? OR data LIKE ?"
                      " OR interface LIKE ? OR ip LIKE ? OR aspect LIKE ?)")
            params.extend([like, like, like, like, like, like])
            print(f"   filtro search: {search}")
        
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
            'aspect_desc': ('aspect', 'DESC'),
            'aspect_asc': ('aspect', 'ASC'),
            'identity_desc': ('identity_hash', 'DESC'),
            'identity_asc': ('identity_hash', 'ASC'),
            'dest_desc': ('dest_hash', 'DESC'),
            'dest_asc': ('dest_hash', 'ASC'),
            'iface_desc': ('interface', 'DESC'),
            'iface_asc': ('interface', 'ASC'),
            'data_desc': ('data', 'DESC'),
            'data_asc': ('data', 'ASC'),
            'q_desc': ('q', 'DESC'),
            'q_asc': ('q', 'ASC'),
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
                        min_rssi=None, since=None, search=None):
        """Conta annunci con filtri"""
        conn = self._connect()
        c = conn.cursor()
        
        query = "SELECT COUNT(*) FROM announces WHERE 1=1"
        params = []
        
        if aspect == 'unknown':
            query += " AND (aspect IS NULL OR aspect = '' OR aspect = 'unknown')"
        elif aspect == 'known':
            query += " AND aspect IS NOT NULL AND aspect != '' AND aspect != 'unknown' AND aspect != 'identity_hash'"
        elif aspect:
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

        if search:
            like = f'%{search}%'
            query += (" AND (dest_hash LIKE ? OR identity_hash LIKE ? OR data LIKE ?"
                      " OR interface LIKE ? OR ip LIKE ? OR aspect LIKE ?)")
            params.extend([like, like, like, like, like, like])
        
        c.execute(query, params)
        count = c.fetchone()[0]
        conn.close()
        
        return count

    def get_max_announce_id(self):
        """Restituisce l'ID massimo presente in cache (0 se vuota).

        Serve a mantenere il contatore degli annunci monotono anche dopo
        un riavvio del server: il frontend usa l'ID per capire quali
        annunci sono nuovi.
        """
        try:
            conn = self._connect()
            c = conn.cursor()
            c.execute("SELECT COALESCE(MAX(announce_id), 0) FROM announces")
            row = c.fetchone()
            conn.close()
            return int(row[0]) if row and row[0] is not None else 0
        except Exception as e:
            print(f"[SQLiteCache] Impossibile leggere MAX(announce_id): {e}")
            return 0

    
    def get_stats(self):
        """Statistiche database (usate anche dal frontend in modalità cache)."""
        conn = self._connect()
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM announces")
        total = c.fetchone()[0]

        c.execute('''
            SELECT aspect, COUNT(*) as count
            FROM announces
            GROUP BY aspect
            ORDER BY count DESC
        ''')
        aspect_rows = c.fetchall()
        aspects = [{'aspect': row[0], 'count': row[1]} for row in aspect_rows[:10]]
        aspect_counts = {(row[0] or 'unknown'): row[1] for row in aspect_rows}

        c.execute('''
            SELECT
                AVG(rssi), MAX(rssi), MIN(rssi),
                AVG(snr), MAX(snr), MIN(snr),
                AVG(q)
            FROM announces
        ''')
        radio = c.fetchone()

        c.execute("SELECT MIN(timestamp), MAX(timestamp) FROM announces")
        time_range = c.fetchone()
        c.execute("SELECT COUNT(DISTINCT identity_hash) FROM announces WHERE identity_hash IS NOT NULL AND identity_hash != ''")
        unique_identities = c.fetchone()[0] or 0

        c.execute('''
            SELECT a.identity_hash, COUNT(*) as count,
                   MIN(a.timestamp) as first_seen, MAX(a.timestamp) as last_seen,
                   (SELECT x.data FROM announces x
                    WHERE x.identity_hash = a.identity_hash
                    ORDER BY x.timestamp DESC LIMIT 1) as last_data,
                   (SELECT COUNT(DISTINCT x.aspect) FROM announces x
                    WHERE x.identity_hash = a.identity_hash) as aspect_count
            FROM announces a
            WHERE a.identity_hash IS NOT NULL AND a.identity_hash != ''
            GROUP BY a.identity_hash
            ORDER BY count DESC
            LIMIT 500
        ''')
        identities = [{
            'identity_hash': row[0],
            'count': row[1],
            'first_seen': row[2],
            'last_seen': row[3],
            'last_data': row[4],
            'aspect_count': row[5],
        } for row in c.fetchall()]

        conn.close()

        return {
            'total_announces': total,
            'aspects': aspects,
            'aspect_counts': aspect_counts,
            'identities': identities,
            'unique_identities': unique_identities,
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
        """Statistiche per un peer (calcolate dai dati correnti)."""
        conn = self._connect()
        c = conn.cursor()
        c.execute('''
            SELECT COUNT(*), MIN(timestamp), MAX(timestamp),
                   AVG(CASE WHEN hops GLOB '[0-9]*' THEN CAST(hops AS INTEGER) END),
                   AVG(rssi), AVG(snr), AVG(q)
            FROM announces WHERE dest_hash = ?
        ''', (dest_hash,))
        row = c.fetchone()
        c.execute('''
            SELECT aspect, interface FROM announces
            WHERE dest_hash = ? ORDER BY timestamp DESC LIMIT 1
        ''', (dest_hash,))
        last = c.fetchone()
        conn.close()
        if not row or not row[0]:
            return None
        return {
            'dest_hash': dest_hash,
            'first_seen': row[1],
            'last_seen': row[2],
            'announce_count': row[0],
            'avg_hops': row[3],
            'avg_rssi': row[4],
            'avg_snr': row[5],
            'avg_q': row[6],
            'last_aspect': last[0] if last else None,
            'last_interface': last[1] if last else None,
        }

    def _should_cleanup(self):
        """Verifica se è ora di fare cleanup"""
        # Chiamato ogni 250 inserimenti: esegue il COUNT solo quando serve.
        conn = self._connect()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM announces")
        count = c.fetchone()[0]
        conn.close()
        
        return count > self.max_size

    def _schedule_cleanup(self):
        """Avvia il cleanup in background evitando esecuzioni concorrenti."""
        with self._cleanup_lock:
            if self._cleanup_running:
                return
            self._cleanup_running = True

        def _run():
            try:
                self._cleanup_old()
            finally:
                with self._cleanup_lock:
                    self._cleanup_running = False

        threading.Thread(target=_run, daemon=True).start()

    def _cleanup_old(self):
        """Rimuove annunci vecchi"""
        try:
            conn = self._connect()
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
        
        conn = self._connect()
        c = conn.cursor()
        c.execute("DELETE FROM announces WHERE timestamp < ?", (cutoff,))
        removed = c.rowcount
        conn.commit()
        conn.close()
        
        print(f"🧹 SQLite: rimossi {removed} annunci più vecchi di {days} giorni")
        return removed
    
    def vacuum(self):
        """Ottimizza database"""
        conn = self._connect()
        conn.execute("VACUUM")
        conn.close()
        print("🧹 SQLite: VACUUM completato")
    
    def _auto_cleanup(self):
        """Thread di cleanup automatico"""
        while self.running:
            time.sleep(3600)  # Ogni ora
            if not self.running:
                break
            self._schedule_cleanup()
    
    def clear(self):
        """Pulisce tutto il database"""
        conn = self._connect()
        c = conn.cursor()
        c.execute("DELETE FROM announces")
        c.execute("DELETE FROM announce_stats")
        try:
            c.execute("DELETE FROM sqlite_sequence WHERE name = 'announces'")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        with self._write_lock:
            self._insert_since_check = 0
        conn.close()
        print("🧹 SQLite: database pulito")
    
    def stop(self):
        """Ferma thread"""
        self.running = False

# ============================================
# === PROCESSO MONITOR ===
# ============================================
def run_rns_monitor(socket_path, aspects, host=None, port=None):
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
        if not IS_WINDOWS and socket_path and os.path.exists(socket_path):
            os.unlink(socket_path)
    except OSError:
        pass

    # === CREAZIONE SOCKET (UNIX su Linux, TCP su Windows) ===
    if IS_WINDOWS:
        # Windows: TCP socket
        socket_dir = os.path.dirname(socket_path) if socket_path else None
        if socket_dir and not os.path.exists(socket_dir):
            os.makedirs(socket_dir, exist_ok=True)
        
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(1)
        print(f"[MONITOR] In attesa su TCP {host}:{port}...")
    else:
        # Linux/Unix: UNIX socket
        socket_dir = os.path.dirname(socket_path)
        if socket_dir and not os.path.exists(socket_dir):
            os.makedirs(socket_dir, exist_ok=True)

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        server.listen(1)
        os.chmod(socket_path, 0o600)
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
    
    def __init__(self, socket_path, aspects, cache_dir, host=None, port=None):
        self.socket_path = socket_path
        self.aspects = aspects
        self.host = host
        self.port = port
        self.is_windows = IS_WINDOWS
        
        # Processi e thread
        self.monitor_process = None
        self.listener_thread = None
        self.running = False
        
        # Cache SQLite
        self.announce_cache = SQLiteAnnounceCache(cache_dir) if cache_dir else None

        # DATI CENTRALIZZATI - unico contatore per TUTTO
        # Riprende l'ID massimo dalla cache: così gli ID restano monotoni
        # anche dopo un riavvio (il frontend usa l'ID per il gate SSE).
        self.announce_counter = 0
        if self.announce_cache:
            try:
                self.announce_counter = self.announce_cache.get_max_announce_id()
                print(f"[MonitorManager] Contatore ripreso da ID {self.announce_counter}")
            except Exception as e:
                print(f"[MonitorManager] Impossibile riprendere il contatore: {e}")

        self.announce_history = []  # Memoria recente (ultimi 100)
        self.history_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._stats_cache = None
        self._stats_ts = 0

        # Sottoscrittori SSE: una coda per client, broadcast a tutti
        self.subscribers = set()
        self.subscribers_lock = threading.Lock()

        
        if self.is_windows:
            print(f"[MonitorManager] Inizializzato con SQLite: {cache_dir} (Windows TCP)")
        else:
            print(f"[MonitorManager] Inizializzato con SQLite: {cache_dir}")
    
    def start_monitor_process(self):
        """Avvia il processo monitor separato"""
        if not self.is_windows:
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
        
        self.monitor_process = multiprocessing.Process(
            target=run_rns_monitor,
            args=(self.socket_path, self.aspects, self.host, self.port),
            # Non daemon: RNS avvia processi figli per generare gli stamp
            # dei path request ("daemonic processes are not allowed to
            # have children"). stop() termina comunque il processo.
            daemon=False
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
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        
        print("[MonitorManager] Connessione al monitor...")
        
        while self.running:
            try:
                if sock is None:
                    if self.is_windows:
                        # Windows: TCP
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(5)
                        sock.connect((self.host, self.port))
                        sock.settimeout(None)
                        print(f"[MonitorManager] ✅ Connesso al monitor TCP {self.host}:{self.port}")
                    else:
                        # Linux/Unix: UNIX socket
                        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        sock.settimeout(5)
                        sock.connect(self.socket_path)
                        sock.settimeout(None)
                        print("[MonitorManager] ✅ Connesso al monitor socket")
                
                chunk = sock.recv(16384)
                data = decoder.decode(chunk) if chunk else ''
                if not data:
                    sock.close()
                    sock = None
                    decoder = codecs.getincrementaldecoder('utf-8')('replace')
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
                            
                            # Broadcast a tutti i client SSE connessi
                            self._broadcast(announce)

                            
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

    def _broadcast(self, announce):
        """Invia un annuncio a tutti i client SSE connessi.

        Ogni client ha la propria coda: in questo modo più schede aperte
        ricevono tutte lo stesso annuncio (la vecchia coda singola
        distribuiva gli eventi a un solo consumatore).
        """
        with self.subscribers_lock:
            subscribers = list(self.subscribers)

        for client_queue in subscribers:
            try:
                client_queue.put_nowait(announce)
            except queue.Full:
                # Client lento: scarta l'evento per non bloccare il monitor
                pass

    def get_stats(self):
        """Statistiche unificate, con cache TTL per non martellare SQLite."""
        now = time.time()
        with self._stats_lock:
            if self._stats_cache is not None and (now - self._stats_ts) < STATS_CACHE_TTL:
                return copy.deepcopy(self._stats_cache)

        with self.history_lock:
            history = list(self.announce_history)
            counter = self.announce_counter

        unique_sources = len({a.get('identity_hash', '') for a in history if a.get('identity_hash')})
        radio_stats = {}
        if history:
            rssi_values = [a.get('rssi') for a in history if a.get('rssi') is not None]
            snr_values = [a.get('snr') for a in history if a.get('snr') is not None]
            q_values = [a.get('q') for a in history if a.get('q') is not None]
            radio_stats = {
                'avg_rssi': sum(rssi_values) / len(rssi_values) if rssi_values else None,
                'avg_snr': sum(snr_values) / len(snr_values) if snr_values else None,
                'avg_q': sum(q_values) / len(q_values) if q_values else None,
                'min_rssi': min(rssi_values) if rssi_values else None,
                'max_rssi': max(rssi_values) if rssi_values else None,
            }

        sqlite_stats = self.announce_cache.get_stats() if self.announce_cache else {}
        result = {
            'total_announces': counter,
            'history_size': len(history),
            'sqlite_total': sqlite_stats.get('total_announces', 0),
            'monitor_alive': self.monitor_process.is_alive() if self.monitor_process else False,
            'unique_sources': unique_sources,
            'radio_stats': radio_stats,
            'sqlite': sqlite_stats,
        }
        with self._stats_lock:
            self._stats_cache = copy.deepcopy(result)
            self._stats_ts = now
        return result

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
        
        aspect = None if aspect_filter == 'all' else aspect_filter
        
        print(f"📌 Parametri convertiti: aspect={aspect}, search={search}")
        
        # Ottieni da SQLite
        announces = self.announce_cache.get_announces(
            aspect=aspect,
            search=search if search else None,
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
            search=search if search else None
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
        
        with self._stats_lock:
            self._stats_cache = None
            self._stats_ts = 0

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
        
        # Rimuovi socket (solo su Linux)
        if not self.is_windows:
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
        response = make_response(render_template('monitor.html', version=APP_VERSION))
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
        if aspect not in ('all', 'unknown', 'known') and aspect not in RNS_ASPECTS:
            aspect = 'all'
        try:
            limit = int(request.args.get('limit', 100))
        except (TypeError, ValueError):
            limit = 100
        try:
            offset = int(request.args.get('offset', 0))
        except (TypeError, ValueError):
            offset = 0
        limit = max(0, min(limit, 100000))
        offset = max(0, min(offset, 10**9))
        search = (request.args.get('search', '') or '')[:128].lower()
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
            client_queue = queue.Queue(maxsize=200)

            # Registra questo client: riceverà il broadcast degli annunci
            with monitor_manager.subscribers_lock:
                monitor_manager.subscribers.add(client_queue)

            print(f"[Stream:{client_id}] Nuovo client connesso")

            try:
                # Invia header SSE
                yield "retry: 1000\n\n"

                # Invia storico recente
                with monitor_manager.history_lock:
                    recent = list(reversed(monitor_manager.announce_history[:20]))

                for announce in recent:
                    if announce.get('id', 0) > last_id:
                        last_id = announce.get('id', 0)
                        yield f"data: {json.dumps(announce)}\n\n"

                # Streaming in tempo reale (coda dedicata a questo client)
                while True:
                    try:
                        announce = client_queue.get(timeout=30)
                        if announce.get('id', 0) > last_id:
                            last_id = announce.get('id', 0)
                            yield f"data: {json.dumps(announce)}\n\n"
                    except queue.Empty:
                        yield ": heartbeat\n\n"
            except GeneratorExit:
                print(f"[Stream:{client_id}] Client disconnesso")
            finally:
                with monitor_manager.subscribers_lock:
                    monitor_manager.subscribers.discard(client_queue)

        
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
                # Non eliminare il file: lo stesso oggetto SQLiteAnnounceCache
                # resta in uso dal listener/cleanup thread. Cancellare il file
                # renderebbe la cache inutilizzabile fino al riavvio.
                monitor_manager.announce_cache.clear()
            
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
    # === 🔥 NUOVO ENDPOINT PER SALVATAGGIO TEMPO REALE ===
    # ============================================
    @monitor_bp.route('/announce', methods=['POST'])
    def save_announce():
        """Salva un annuncio in tempo reale nel database SQLite"""
        try:
            announce = request.get_json(silent=True)
            if not isinstance(announce, dict) or not announce:
                return jsonify({'success': False, 'error': 'Dati mancanti'}), 400
            
            print(f"\n📡 NUOVO ANNUNCIO IN TEMPO REALE - ID: {announce.get('id')}")
            print(f"   Aspect: {announce.get('aspect')}")
            print(f"   Dest: {announce.get('dest_hash', '')[:16]}...")
            
            # Salva nel database SQLite
            if monitor_manager.announce_cache:
                monitor_manager.announce_cache.add_announce(announce)
                print(f"   ✅ Salvato in SQLite")
            else:
                print(f"   ⚠️ SQLite non disponibile")
            
            return jsonify({'success': True})
            
        except Exception as e:
            print(f"❌ Errore salvataggio annuncio: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500


    # ============================================
    # === ROUTE PER CHIUSURA SICURA ===
    # ============================================



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
        dest = (request.args.get('dest') or '')[:128] or None
        identity = (request.args.get('identity') or '')[:128] or None
        min_rssi = request.args.get('min_rssi', type=float)
        since = request.args.get('since', type=float)
        try:
            limit = int(request.args.get('limit', 100))
        except (TypeError, ValueError):
            limit = 100
        try:
            offset = int(request.args.get('offset', 0))
        except (TypeError, ValueError):
            offset = 0
        limit = max(1, min(limit, 100000))
        offset = max(0, min(offset, 10**9))
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
        
        body = request.get_json(silent=True) or {}
        try:
            days = int(body.get('days', 30))
        except (TypeError, ValueError):
            days = 30
        days = max(1, min(days, 3650))
        
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
    
    return monitor_bp
