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
from datetime import datetime, timedelta

# Costanti
SOCKET_PATH = "/tmp/rns_monitor.sock"

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
# === CACHE PERSISTENTE ===
# ============================================
class PersistentAnnounceCache:
    """Cache persistente su disco per annunci RNS - ORA CON DATI RADIO"""
    
    def __init__(self, cache_dir, cache_file='announce_cache.json', save_interval=60, max_age_days=7, max_size=10000):
        self.cache_file = os.path.join(cache_dir, cache_file)
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
                self.stats['total_saved'] = len(self.cache)
                print(f"[Cache] Caricati {len(self.cache)} annunci storici (rimossi {len(data) - len(self.cache)} vecchi)")
            else:
                print(f"[Cache] Nessun file cache esistente, partenza vuota")
        except Exception as e:
            print(f"[Cache] Errore caricamento: {e}")
            self.cache = []
    
    def add_announce(self, announce):
        """Aggiunge annuncio alla cache - ORA CON DATI RADIO COMPLETI"""
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
            
            # Aggiorna statistiche
            self.stats['total_saved'] = len(self.cache)
    
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
            
            # Scrivi su file temporaneo poi rinomina
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
    
    def clear(self):
        """Pulisce tutta la cache"""
        with self.lock:
            self.cache = []
            self.stats['total_saved'] = 0
            self.save()
        print(f"[Cache] Cache pulita")
    
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
                self.stats['total_saved'] = len(self.cache)
                self.save()
                print(f"[Cache] Rimossi {removed} annunci vecchi")
            return removed
    
    def stop(self):
        """Ferma thread e salva"""
        self.running = False
        if self.save_thread.is_alive():
            self.save_thread.join(timeout=5)
        self.save()
        print(f"[Cache] Cache fermata")

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
    
    # Setup socket
    try:
        os.unlink(socket_path)
    except OSError:
        pass
    
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
    """Gestore per Flask con contatori centralizzati - ORA CON DATI RADIO"""
    
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
        self.announce_history = []  # Memoria recente (max_history)
        self.history_lock = threading.Lock()
        self.announce_queue = queue.Queue(maxsize=1000)
        
        # Cache persistente
        self.announce_cache = PersistentAnnounceCache(cache_dir) if cache_dir else None
    
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
                                
                                # Aggiungi alla history recente
                                self.announce_history.insert(0, announce)
                                if len(self.announce_history) > self.max_history:
                                    self.announce_history.pop()
                                
                                # Aggiungi alla cache persistente (con lo stesso ID)
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
        """Restituisce statistiche unificate - ORA CON DATI RADIO"""
        with self.history_lock:
            unique_sources = len({a.get('identity_hash', '') for a in self.announce_history if a.get('identity_hash')}) if self.announce_history else 0
            
            cache_stats = self.announce_cache.get_stats() if self.announce_cache else {}
            
            # Calcola statistiche radio
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
                'cache_size': len(self.announce_cache.cache) if self.announce_cache else 0,
                'monitor_alive': self.monitor_process.is_alive() if self.monitor_process else False,
                'unique_sources': unique_sources,
                'radio_stats': radio_stats,
                'cache': cache_stats
            }
    
    def get_history(self, aspect_filter='all', limit=100, offset=0, search='', sort='time_desc', source='memory'):
        """Recupera storico annunci - ORA CON DATI RADIO"""
        if source == 'cache' and self.announce_cache:
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
                           search in a.get('data', '').lower() or
                           search in a.get('ip', '').lower() or
                           search in a.get('interface', '').lower())
                return True
            
            filtered = self.announce_cache.get_all(filter_func)
            total = len(filtered)
            
            # Ordina
            sort_functions = {
                'time_desc': (lambda x: x.get('timestamp', 0), True),
                'time_asc': (lambda x: x.get('timestamp', 0), False),
                'hops_desc': (lambda x: int(x.get('hops', 0)) if str(x.get('hops', '0')).isdigit() else 0, True),
                'hops_asc': (lambda x: int(x.get('hops', 0)) if str(x.get('hops', '0')).isdigit() else 999, False),
                'rssi_desc': (lambda x: x.get('rssi', -999) or -999, True),
                'rssi_asc': (lambda x: x.get('rssi', 999) or 999, False),
            }
            key_func, reverse = sort_functions.get(sort, (lambda x: x.get('timestamp', 0), True))
            filtered.sort(key=key_func, reverse=reverse)
            
            paginated = filtered[offset:offset + limit]
            
        else:
            # Usa memoria recente
            with self.history_lock:
                filtered = self.announce_history.copy()
            
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
                           search in a.get('data', '').lower() or
                           search in a.get('ip', '').lower() or
                           search in a.get('interface', '').lower()]
            
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
            }
            
            key_func, reverse = sort_functions.get(sort, (lambda x: x.get('timestamp', 0), True))
            filtered.sort(key=key_func, reverse=reverse)
            
            total = len(filtered)
            paginated = filtered[offset:offset + limit]
        
        return {
            'announces': paginated,
            'total': total,
            'source': source
        }
    
    def get_peer_details(self, dest_hash):
        """Ottieni dettagli completi di un peer specifico"""
        with self.history_lock:
            # Cerca nella history
            for a in self.announce_history:
                if a.get('dest_hash') == dest_hash:
                    return a
            
            # Cerca nella cache
            if self.announce_cache:
                filtered = self.announce_cache.get_all(lambda x: x.get('dest_hash') == dest_hash)
                if filtered:
                    return filtered[0]
        
        return None
    
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
        
        # Pulisci cache
        if self.announce_cache:
            self.announce_cache.clear()
            
            # Elimina file cache
            try:
                if os.path.exists(self.announce_cache.cache_file):
                    os.remove(self.announce_cache.cache_file)
                    print(f"[MonitorManager] File cache eliminato: {self.announce_cache.cache_file}")
            except Exception as e:
                print(f"[MonitorManager] Errore eliminazione file: {e}")
            
            # Crea file cache vuoto
            try:
                with open(self.announce_cache.cache_file, 'w') as f:
                    json.dump([], f)
                print(f"[MonitorManager] Creato nuovo file cache vuoto")
            except Exception as e:
                print(f"[MonitorManager] Errore creazione file: {e}")
        
        return True
    
    def stop(self):
        """Ferma tutti i processi e thread"""
        self.running = False
        
        # Ferma cache
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

# ============================================
# === BLUEPRINT PER FLASK ===
# ============================================

# ============================================
# === BLUEPRINT PER FLASK (CORRETTO) ===
# ============================================

# ============================================
# === BLUEPRINT PER FLASK (CORRETTO) ===
# ============================================

def create_monitor_blueprint(monitor_manager):
    """Crea un blueprint Flask con le route del monitor - ORA CON DATI RADIO"""
    from flask import Blueprint, request, jsonify, Response, stream_with_context, render_template, make_response
    import uuid
    import json
    import time
    
    # Blueprint con prefisso /api/monitor
    monitor_bp = Blueprint('monitor', __name__, url_prefix='/api/monitor')
    
    # Pagina principale - CORRETTA
    @monitor_bp.route('')
    def monitor_page():
        response = make_response(render_template('monitor.html'))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    
    # API stats
    @monitor_bp.route('/stats')
    def stats():
        return jsonify({
            'success': True,
            **monitor_manager.get_stats()
        })
    
    # API history
    @monitor_bp.route('/history')
    def history():
        aspect = request.args.get('aspect', 'all')
        limit = int(request.args.get('limit', 100))
        offset = int(request.args.get('offset', 0))
        search = request.args.get('search', '').lower()
        sort = request.args.get('sort', 'time_desc')
        source = request.args.get('source', 'memory')
        
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
    
    # API peer details
    @monitor_bp.route('/peer/<dest_hash>')
    def peer_details(dest_hash):
        peer = monitor_manager.get_peer_details(dest_hash)
        if peer:
            return jsonify({'success': True, 'peer': peer})
        return jsonify({'success': False, 'error': 'Peer non trovato'}), 404
    
    # API stream
    @monitor_bp.route('/stream')
    def stream():
        def generate():
            client_id = str(uuid.uuid4())[:8]
            print(f"[SSE] Client {client_id} connesso")
            
            last_keepalive = time.time()
            
            with monitor_manager.history_lock:
                last_id = monitor_manager.announce_counter
                recent = list(reversed(monitor_manager.announce_history[-10:])) if monitor_manager.announce_history else []
                for ann in recent:
                    yield f"data: {json.dumps(ann)}\n\n"
            
            while True:
                try:
                    announce = monitor_manager.announce_queue.get(timeout=15)
                    if announce['id'] > last_id:
                        last_id = announce['id']
                        yield f"data: {json.dumps(announce)}\n\n"
                        print(f"[SSE] Inviato #{announce['id']} a {client_id}")
                except queue.Empty:
                    if time.time() - last_keepalive > 15:
                        yield ":\n\n"
                        last_keepalive = time.time()
                    continue
                except GeneratorExit:
                    print(f"[SSE] Client {client_id} disconnesso")
                    break
                except Exception as e:
                    print(f"[SSE] Errore: {e}")
                    time.sleep(1)
        
        response = Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={
                'Cache-Control': 'no-cache, no-transform',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive',
                'Content-Type': 'text/event-stream; charset=utf-8',
                'Access-Control-Allow-Origin': '*'
            }
        )
        return response
    
    # API clear
    @monitor_bp.route('/clear', methods=['POST'])
    def clear():
        monitor_manager.clear_all()
        return jsonify({'success': True, 'message': 'TUTTO PULITO!'})
    
    # API reset-all
    @monitor_bp.route('/reset-all', methods=['POST'])
    def reset_all():
        """Reset totale di tutti i dati (cache, history, contatori)"""
        try:
            print("="*60)
            print("🔄 RESET TOTALE RICHIESTO")
            print("="*60)
            
            monitor_manager.clear_all()
            
            if monitor_manager.announce_cache:
                monitor_manager.announce_cache.clear()
                try:
                    if os.path.exists(monitor_manager.announce_cache.cache_file):
                        os.remove(monitor_manager.announce_cache.cache_file)
                        print(f"[Reset] File cache eliminato: {monitor_manager.announce_cache.cache_file}")
                except Exception as e:
                    print(f"[Reset] Errore eliminazione file: {e}")
                
                try:
                    with open(monitor_manager.announce_cache.cache_file, 'w') as f:
                        json.dump([], f)
                    print(f"[Reset] Creato nuovo file cache vuoto")
                except Exception as e:
                    print(f"[Reset] Errore creazione file: {e}")
            
            stats = monitor_manager.get_stats()
            print(f"[Reset] Completato. Stats: {stats}")
            
            return jsonify({
                'success': True,
                'message': 'Reset totale completato',
                'stats': stats
            })
        except Exception as e:
            print(f"[Reset] ERRORE: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    # API cache clear
    @monitor_bp.route('/cache/clear', methods=['POST'])
    def cache_clear():
        if monitor_manager.announce_cache:
            monitor_manager.announce_cache.clear()
            return jsonify({'success': True, 'message': 'Cache persistente pulita'})
        return jsonify({'success': False, 'error': 'Cache non disponibile'})
    
    # API cache cleanup
    @monitor_bp.route('/cache/cleanup', methods=['POST'])
    def cache_cleanup():
        if monitor_manager.announce_cache:
            removed = monitor_manager.announce_cache.cleanup_old()
            return jsonify({
                'success': True, 
                'removed': removed,
                'message': f'Rimossi {removed} annunci vecchi'
            })
        return jsonify({'success': False, 'error': 'Cache non disponibile'})
    
    # API cache save
    @monitor_bp.route('/cache/save', methods=['POST'])
    def cache_save():
        if monitor_manager.announce_cache:
            monitor_manager.announce_cache.save()
            return jsonify({'success': True, 'message': 'Cache salvata'})
        return jsonify({'success': False, 'error': 'Cache non disponibile'})
    
    # API cache stats
    @monitor_bp.route('/cache/stats')
    def cache_stats():
        if monitor_manager.announce_cache:
            stats = monitor_manager.announce_cache.get_stats()
            return jsonify({
                'success': True,
                'stats': stats
            })
        return jsonify({'success': False, 'error': 'Cache non disponibile'})
    
    # API aspects
    @monitor_bp.route('/aspects')
    def aspects():
        return jsonify({
            'success': True,
            'aspects': monitor_manager.aspects
        })
    
    # ============================================
    # === ENDPOINT PER LXMF-CHAT (UNICO) ===
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
    
    # ============================================
    # === RITORNA IL BLUEPRINT ===
    # ============================================
    
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