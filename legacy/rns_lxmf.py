#!/usr/bin/env python3
# rns_lxmf.py - Messenger completo per Reticulum/LXMF con telemetria, allegati, comandi e annunci

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
from RNS.vendor import umsgpack

import telemeter
import audio_codec

# Costanti LXMF
FIELD_TELEMETRY        = 0x02
FIELD_TELEMETRY_STREAM = 0x03
FIELD_ICON_APPEARANCE  = 0x04
FIELD_FILE_ATTACHMENTS = 0x05
FIELD_IMAGE            = 0x06
FIELD_AUDIO            = 0x07
FIELD_COMMANDS         = 0x09

class Commands:
    TELEMETRY_REQUEST = 0x01
    PING              = 0x02
    ECHO              = 0x03
    SIGNAL_REPORT     = 0x04

CODEC2_AVAILABLE = audio_codec.detect_codec2()

def guess_image_format(data):
    """Determina il formato di un'immagine dai primi byte."""
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

class Messenger:
    def __init__(self, storagepath="~/.rns_manager/storage", configpath="~/.rns_manager/config.json"):
        self.storagepath = os.path.expanduser(storagepath)
        self.configpath = os.path.expanduser(configpath)
        self.telemetry_dir = os.path.join(self.storagepath, "telemetry")
        self.attachments_dir = os.path.join(self.storagepath, "attachments")
        self.conversations_dir = os.path.join(self.storagepath, "conversations")
        self.raw_dir = os.path.join(self.storagepath, "raw")
        self.peers_db = os.path.join(self.storagepath, "peers.db")
        self.announces_cache_db = os.path.expanduser("~/.rns_manager/Cache/announces.db")
        os.makedirs(self.storagepath, exist_ok=True)
        os.makedirs(self.telemetry_dir, exist_ok=True)
        os.makedirs(self.attachments_dir, exist_ok=True)
        os.makedirs(self.conversations_dir, exist_ok=True)
        os.makedirs(self.raw_dir, exist_ok=True)

        # Lock per accesso concorrente al database
        self.db_lock = threading.Lock()

        self.config = self._load_config()
        self.rns = RNS.Reticulum()
        self.identity = self._choose_identity()
        self.display_name = self._load_display_name()

        # Directory per lo storage interno di LXMF: deve essere la base (~/.rns_manager)
        self.lxmf_storage = os.path.dirname(self.storagepath)  # ~/.rns_manager
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

        self.router.register_delivery_callback(self._on_message)

        self.propagation_node = None
        self._set_propagation_node()

        # Inizializza database peer
        self._init_peers_db()

        # Registra handler per annunci in arrivo
        self.announce_handler = AnnounceHandler(self)
        RNS.Transport.register_announce_handler(self.announce_handler)

        # Dizionario per memorizzare i raw dei messaggi ricevuti (in memoria, solo per la sessione)
        self.received_raw = {}

    def _init_peers_db(self):
        """Inizializza il database dei peer con le nuove tabelle, migrando se necessario."""
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()

            # Verifica se esiste la vecchia tabella peers
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='peers'")
            old_table_exists = c.fetchone() is not None

            # Crea le nuove tabelle se non esistono
            c.execute('''
                CREATE TABLE IF NOT EXISTS identities (
                    identity_hash TEXT PRIMARY KEY,
                    display_name TEXT,
                    first_seen REAL,
                    last_seen REAL,
                    notes TEXT,
                    groups TEXT  -- 🔴 JSON array per gruppi multipli
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
                    appearance TEXT,  -- 🔴 NUOVO: JSON con icona, fg, bg
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
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_identity ON destinations(identity_hash)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_dest_aspect ON destinations(aspect)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_sent_status ON sent_messages(status)')

            # 🔴 AGGIUNGI COLONNA groups se non esiste (per vecchi database)
            try:
                c.execute("ALTER TABLE identities ADD COLUMN groups TEXT")
                print("✅ Aggiunta colonna groups")
            except:
                pass  # La colonna esiste già

            # 🔴 AGGIUNGI COLONNA appearance se non esiste (per vecchi database)
            try:
                c.execute("ALTER TABLE destinations ADD COLUMN appearance TEXT")
                print("✅ Aggiunta colonna appearance")
            except:
                pass  # La colonna esiste già

            # Se esisteva la vecchia tabella e le nuove sono vuote, migra i dati
            if old_table_exists:
                c.execute("SELECT COUNT(*) FROM identities")
                id_count = c.fetchone()[0]
                c.execute("SELECT COUNT(*) FROM destinations")
                dest_count = c.fetchone()[0]
                if id_count == 0 and dest_count == 0:
                    print("🔄 Migrazione dati dalla vecchia tabella peers...")
                    c.execute("SELECT dest_hash, display_name, first_seen, last_seen, hops, rssi, snr, q, identity_hash, app_data FROM peers")
                    rows = c.fetchall()
                    for row in rows:
                        dest_hash, display_name, first_seen, last_seen, hops, rssi, snr, q, identity_hash, app_data = row
                        if identity_hash:
                            # 🔴 GROUPS DI DEFAULT VUOTO (JSON array vuoto)
                            default_groups = json.dumps([])
                            c.execute('''
                                INSERT OR IGNORE INTO identities (identity_hash, display_name, first_seen, last_seen, groups)
                                VALUES (?, ?, ?, ?, ?)
                            ''', (identity_hash, display_name, first_seen, last_seen, default_groups))
                            aspect = "lxmf.delivery"
                            c.execute('''
                                INSERT OR REPLACE INTO destinations (destination_hash, identity_hash, aspect, last_seen, hops, rssi, snr, q, app_data)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (dest_hash, identity_hash, aspect, last_seen, hops, rssi, snr, q, app_data))
                    print(f"✅ Migrazione completata: {len(rows)} record processati.")
            conn.commit()
            conn.close()

    def _load_config(self):
        default = {
            "propagation_node": None,
            "delivery_mode": "direct",
            "max_retries": 3,
            "preferred_audio": "c2_2400",
            "stamp_cost": 16,
            "propagation_stamp_cost": 16,
            "accept_invalid_stamps": False,
            "max_stamp_retries": 3,
            "auto_send_voice": False,
            "try_propagation_on_fail": True
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
            print("📄 Creato config di default")
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
            print("\n🔑 Identità trovate:")
            for i, id_file in enumerate(identities):
                try:
                    with open(os.path.join(self.storagepath, id_file), 'rb') as f:
                        data = f.read(32)
                        if len(data) == 32:
                            hash_preview = data.hex()[:16]
                            print(f"{i+1}. {hash_preview}... ({id_file})")
                        else:
                            print(f"{i+1}. {id_file}")
                except:
                    print(f"{i+1}. {id_file}")
            choice = input("\nScegli identità (numero) o 'n' per nuova: ").strip()
            if choice.lower() != 'n':
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(identities):
                        path = os.path.join(self.storagepath, identities[idx])
                        return RNS.Identity.from_file(path)
                except Exception as e:
                    print(f"Errore: {e}")
        print("\n✨ Creazione nuova identità...")
        identity = RNS.Identity()
        path = os.path.join(self.storagepath, identity.hash.hex())
        identity.to_file(path)
        print(f"✅ Nuova identità: {identity.hash.hex()}")
        return identity

    def _load_display_name(self):
        name_file = os.path.join(self.storagepath, f"{self.identity.hash.hex()}.name")
        if os.path.exists(name_file):
            with open(name_file, 'r') as f:
                return f.read().strip()
        else:
            name = input("Inserisci il tuo nome (display name): ").strip()
            if not name:
                name = "Anonymous"
            with open(name_file, 'w') as f:
                f.write(name)
            return name

    def _save_attachment(self, msg_hash, typ, data, filename_hint=None):
        if not data or len(data) == 0:
            return None
        try:
            if typ == 'image':
                # Rileva formato dai magic number
                ext = '.img'  # default
                if len(data) >= 12:
                    if data.startswith(b'\xff\xd8'):
                        ext = '.jpg'
                    elif data.startswith(b'\x89PNG\r\n\x1a\n'):
                        ext = '.png'
                    elif data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
                        ext = '.gif'
                    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
                        ext = '.webp'
                    elif data.startswith(b'BM'):
                        ext = '.bmp'
                # Se non riconosciuto, usa l'hint numerico (se fornito)
                if ext == '.img' and isinstance(filename_hint, int):
                    ext_map = {1: '.jpg', 2: '.png', 3: '.gif', 0: '.img'}
                    ext = ext_map.get(filename_hint, '.img')
                filename = f"{msg_hash}_image{ext}"
            elif typ == 'audio':
                if isinstance(filename_hint, tuple) and len(filename_hint) == 2:
                    mode, codec = filename_hint
                    if codec == 'opus':
                        ext = '.opus'
                    elif codec == 'c2':
                        ext = f'.c2_{mode}'
                    else:
                        ext = '.aud'
                else:
                    ext = '.aud'
                filename = f"{msg_hash}_audio{ext}"
            elif typ.startswith('file_'):
                safe_name = "".join(c for c in filename_hint if c.isalnum() or c in '._-')[:50]
                ext = os.path.splitext(filename_hint)[1] or '.bin'
                filename = f"{msg_hash}_{safe_name}{ext}"
            else:
                ext = '.bin'
                filename = f"{msg_hash}_{typ}{ext}"
            
            filepath = os.path.join(self.attachments_dir, filename)
            with open(filepath, 'wb') as f:
                f.write(data)
            return filepath
        except Exception as e:
            RNS.log(f"Errore salvataggio allegato: {e}", RNS.LOG_ERROR)
            return None

    def _save_telemetry(self, source_hash, telemetry_obj):
        try:
            ts = int(time.time())
            json_filename = f"{source_hash}_{ts}.json"
            json_path = os.path.join(self.telemetry_dir, json_filename)
            data = telemetry_obj.read_all()
            with open(json_path, 'w') as f:
                json.dump(data, f, indent=2)

            bin_filename = f"{source_hash}_{ts}.bin"
            bin_path = os.path.join(self.telemetry_dir, bin_filename)
            packed = telemetry_obj.packed()
            if packed and len(packed) > 0:
                with open(bin_path, 'wb') as f:
                    f.write(packed)
            return json_path
        except Exception as e:
            RNS.log(f"Errore salvataggio telemetria: {e}", RNS.LOG_ERROR)
            return None

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
        """Aggiorna le informazioni del peer in base al messaggio ricevuto."""
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            now = time.time()
            hops = RNS.Transport.hops_to(bytes.fromhex(source_hash)) if source_hash else None
            rssi = getattr(message, 'rssi', None)
            snr = getattr(message, 'snr', None)
            q = getattr(message, 'q', None)

            # Cerchiamo se esiste già una destinazione per questo source_hash
            c.execute("SELECT identity_hash FROM destinations WHERE destination_hash = ?", (source_hash,))
            row = c.fetchone()
            identity_hash = row[0] if row else None

            # Se non abbiamo identity_hash, potremmo provare a vedere se l'identità è nota a Reticulum
            if not identity_hash:
                ident = RNS.Identity.recall(bytes.fromhex(source_hash))
                if ident:
                    identity_hash = ident.hash.hex()

            # Aggiorna la destinazione
            c.execute('''
                INSERT INTO destinations (destination_hash, last_seen, hops, rssi, snr, q)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(destination_hash) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    hops = excluded.hops,
                    rssi = excluded.rssi,
                    snr = excluded.snr,
                    q = excluded.q
            ''', (source_hash, now, hops, rssi, snr, q))

            # Se abbiamo identity_hash, aggiorna anche l'identità
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
        """Salva il pacchetto raw in formato binario e hex nella directory raw."""
        if not packed:
            return
        try:
            # Salva binario
            raw_path = os.path.join(self.raw_dir, f"{msg_hash}.raw")
            with open(raw_path, 'wb') as f:
                f.write(packed)
            # Salva hex
            hex_path = os.path.join(self.raw_dir, f"{msg_hash}.hex")
            with open(hex_path, 'w') as f:
                f.write(packed.hex())
        except Exception as e:
            RNS.log(f"Errore salvataggio raw per {msg_hash}: {e}", RNS.LOG_ERROR)


    def _message_to_dict(self, lxmf_message):
        """Converte un oggetto LXMF.LXMessage in un dizionario con tutti i campi utili."""
        # Gestione fields: trasformiamo i campi speciali in rappresentazioni più leggibili
        fields_dict = {}
        if lxmf_message.fields:
            for ftype, fvalue in lxmf_message.fields.items():
                if ftype == FIELD_IMAGE and isinstance(fvalue, (list, tuple)) and len(fvalue) == 2:
                    fields_dict['image'] = {
                        'type': fvalue[0],
                        'bytes': fvalue[1]  # Puoi anche usare base64 se serve per serializzazione JSON
                    }
                elif ftype == FIELD_AUDIO and isinstance(fvalue, (list, tuple)) and len(fvalue) == 2:
                    fields_dict['audio'] = {
                        'mode': fvalue[0],
                        'bytes': fvalue[1]
                    }
                elif ftype == FIELD_FILE_ATTACHMENTS and isinstance(fvalue, list):
                    attachments = []
                    for item in fvalue:
                        if isinstance(item, (list, tuple)) and len(item) == 2:
                            attachments.append({
                                'name': item[0],
                                'bytes': item[1]
                            })
                    fields_dict['file_attachments'] = attachments
                elif ftype == FIELD_ICON_APPEARANCE and isinstance(fvalue, (list, tuple)) and len(fvalue) == 3:
                    fields_dict['appearance'] = {
                        'icon': fvalue[0],
                        'foreground': fvalue[1].hex() if isinstance(fvalue[1], bytes) else fvalue[1],
                        'background': fvalue[2].hex() if isinstance(fvalue[2], bytes) else fvalue[2]
                    }
                elif ftype == FIELD_COMMANDS and isinstance(fvalue, list):
                    fields_dict['commands'] = fvalue  # Mantieni come lista di dict
                elif ftype == FIELD_TELEMETRY and isinstance(fvalue, bytes):
                    # Potremmo provare a decodificare la telemetria, ma per ora lasciamo bytes
                    fields_dict['telemetry'] = fvalue.hex()
                elif ftype == FIELD_TELEMETRY_STREAM and isinstance(fvalue, list):
                    stream = []
                    for packed in fvalue:
                        if isinstance(packed, bytes):
                            stream.append(packed.hex())
                    fields_dict['telemetry_stream'] = stream
                else:
                    # Altri campi: li includiamo con il loro valore originale
                    # Potremmo anche provare a convertirli in stringhe se sono bytes
                    if isinstance(fvalue, bytes):
                        fields_dict[ftype] = fvalue.hex()
                    else:
                        fields_dict[ftype] = fvalue

        # Ottieni metriche radio (se non presenti nel messaggio, prova a recuperarle da Reticulum)
        rssi = getattr(lxmf_message, 'rssi', None)
        snr = getattr(lxmf_message, 'snr', None)
        q = getattr(lxmf_message, 'q', None)
        if rssi is None and hasattr(self.rns, 'get_packet_rssi'):
            rssi = self.rns.get_packet_rssi(lxmf_message.hash)
        if snr is None and hasattr(self.rns, 'get_packet_snr'):
            snr = self.rns.get_packet_snr(lxmf_message.hash)
        if q is None and hasattr(self.rns, 'get_packet_q'):
            q = self.rns.get_packet_q(lxmf_message.hash)

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

    def get_peer_groups(self, identity_hash):
        """Restituisce la lista dei gruppi di un peer."""
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            c.execute("SELECT groups FROM identities WHERE identity_hash = ?", (identity_hash,))
            row = c.fetchone()
            conn.close()
            if row and row[0]:
                return json.loads(row[0])
            return []

    def set_peer_groups(self, identity_hash, groups):
        """Imposta i gruppi di un peer (lista di stringhe)."""
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            groups_json = json.dumps(groups)
            c.execute('''
                UPDATE identities SET groups = ? WHERE identity_hash = ?
            ''', (groups_json, identity_hash))
            conn.commit()
            conn.close()

    def add_peer_group(self, identity_hash, group):
        """Aggiunge un gruppo a un peer."""
        groups = self.get_peer_groups(identity_hash)
        if group not in groups:
            groups.append(group)
            self.set_peer_groups(identity_hash, groups)

    def remove_peer_group(self, identity_hash, group):
        """Rimuove un gruppo da un peer."""
        groups = self.get_peer_groups(identity_hash)
        if group in groups:
            groups.remove(group)
            self.set_peer_groups(identity_hash, groups)

    def _save_peer_appearance(self, peer_hash, appearance):
        """Salva l'appearance di un peer nel database."""
        try:
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                
                # Crea un JSON con l'appearance
                appearance_json = json.dumps({
                    'icon': appearance[0],
                    'fg': appearance[1].hex() if isinstance(appearance[1], bytes) else appearance[1],
                    'bg': appearance[2].hex() if isinstance(appearance[2], bytes) else appearance[2]
                })
                
                # Aggiorna la tabella destinations
                c.execute('''
                    UPDATE destinations 
                    SET appearance = ? 
                    WHERE destination_hash = ?
                ''', (appearance_json, peer_hash))
                
                # Se non esiste, la creiamo (anche se probabilmente esiste già dall'annuncio)
                if c.rowcount == 0:
                    # Prova a inserire
                    c.execute('''
                        INSERT INTO destinations (destination_hash, appearance, last_seen)
                        VALUES (?, ?, ?)
                    ''', (peer_hash, appearance_json, time.time()))
                
                conn.commit()
                conn.close()
                print(f"💾 Appearance salvata per {peer_hash[:8]}: {appearance[0]}")
        except Exception as e:
            print(f"❌ Errore salvataggio appearance: {e}")

    def _on_message(self, message):
        attachments = []
        telemetry_obj = None
        appearance = None

        if message.fields:

            if FIELD_ICON_APPEARANCE in message.fields:
                appearance = message.fields[FIELD_ICON_APPEARANCE]
                # 🔴 SALVA L'APPEARANCE NEL DATABASE!
                self._save_peer_appearance(message.source_hash.hex(), appearance)

            if FIELD_IMAGE in message.fields:
                img_data = message.fields[FIELD_IMAGE]
                if isinstance(img_data, (list, tuple)) and len(img_data) == 2:
                    image_type = img_data[0]
                    image_bytes = img_data[1]
                    if isinstance(image_bytes, bytes):
                        path = self._save_attachment(message.hash.hex(), "image", image_bytes, filename_hint=image_type)
                        if path:
                            attachments.append(("image", path))

            if FIELD_AUDIO in message.fields:
                audio_data = message.fields[FIELD_AUDIO]
                if isinstance(audio_data, (list, tuple)) and len(audio_data) == 2:
                    audio_mode = audio_data[0]
                    audio_bytes = audio_data[1]
                    if isinstance(audio_bytes, bytes):
                        hint = (audio_mode, 'raw')
                        raw_path = self._save_attachment(message.hash.hex(), "audio", audio_bytes, filename_hint=hint)
                        if raw_path:
                            attachments.append(("audio_raw", raw_path))
                        
                        if 1 <= audio_mode <= 9 and CODEC2_AVAILABLE:
                            try:
                                pcm_data = audio_codec.decode_codec2(audio_bytes, audio_mode)
                                if pcm_data:
                                    wav_path = os.path.join(self.attachments_dir, f"{message.hash.hex()}_audio_c2_{audio_mode}.wav")
                                    if audio_codec.samples_to_wav(pcm_data, wav_path):
                                        attachments.append(("audio_wav", wav_path))
                                    ogg_path = os.path.join(self.attachments_dir, f"{message.hash.hex()}_audio_c2_{audio_mode}.ogg")
                                    if audio_codec.samples_to_ogg(pcm_data, ogg_path):
                                        attachments.append(("audio_ogg", ogg_path))
                            except Exception as e:
                                RNS.log(f"Errore decodifica Codec2: {e}", RNS.LOG_WARNING)
                        
                        elif audio_mode >= 16:
                            new_path = raw_path.replace('.aud', '.opus')
                            try:
                                os.rename(raw_path, new_path)
                                attachments.remove(("audio_raw", raw_path))
                                attachments.append(("audio", new_path))
                            except Exception as e:
                                RNS.log(f"Errore rinomina Opus: {e}", RNS.LOG_WARNING)

            if FIELD_FILE_ATTACHMENTS in message.fields:
                file_list = message.fields[FIELD_FILE_ATTACHMENTS]
                if isinstance(file_list, list):
                    for item in file_list:
                        if isinstance(item, (list, tuple)) and len(item) == 2:
                            file_name = item[0]
                            file_bytes = item[1]
                            if isinstance(file_bytes, bytes):
                                path = self._save_attachment(message.hash.hex(), f"file_{file_name}", file_bytes, filename_hint=file_name)
                                if path:
                                    attachments.append(("file", path))

            if FIELD_TELEMETRY in message.fields:
                try:
                    telemetry_obj = telemeter.Telemeter.from_packed(message.fields[FIELD_TELEMETRY])
                    self._save_telemetry(message.source_hash.hex(), telemetry_obj)
                except Exception as e:
                    RNS.log(f"Errore parsing telemetria: {e}", RNS.LOG_ERROR)

            if FIELD_TELEMETRY_STREAM in message.fields:
                try:
                    stream = []
                    for packed in message.fields[FIELD_TELEMETRY_STREAM]:
                        t = telemeter.Telemeter.from_packed(packed)
                        if t:
                            stream.append(t)
                    telemetry_obj = stream
                except Exception as e:
                    RNS.log(f"Errore parsing stream telemetria: {e}", RNS.LOG_ERROR)

            if FIELD_ICON_APPEARANCE in message.fields:
                appearance = message.fields[FIELD_ICON_APPEARANCE]

            if FIELD_COMMANDS in message.fields:
                self._handle_commands(message, message.fields[FIELD_COMMANDS])

        # Estrai raw se disponibile e salva
        raw_hex = None
        raw_ascii = None
        packed_size = 0
        if message.packed:
            packed_size = len(message.packed)
            raw_hex = message.packed.hex()
            raw_ascii = ''.join(chr(b) if 32 <= b < 127 else '.' for b in message.packed)
            # Salva su disco
            self._save_raw(message.hash.hex(), message.packed)

        msg_dict = {
            'from': message.source_hash.hex(),
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

        # Salva raw in memoria per accesso rapido (opzionale)
        if raw_hex:
            self.received_raw[msg_dict['hash']] = {'hex': raw_hex, 'ascii': raw_ascii}

        if self.message_callback:
            self.message_callback(msg_dict)

        msg_dict = self._message_to_dict(message)
        # Aggiungi eventuali campi extra specifici della ricezione (es. telemetry object, attachments paths)
        if message.source_hash:
            msg_dict['hops'] = RNS.Transport.hops_to(message.source_hash)
        # telemetry_obj e attachments sono già gestiti separatamente? Potremmo integrarli nel dizionario
        # Ad esempio, aggiungere 'telemetry_obj' e 'attachments_paths' se necessario.

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

    def send(self, dest_hash, content, title="", callbacks=None,
             image_path=None, image_type=None,
             audio_path=None, audio_mode=None,
             file_attachments=None,
             telemetry=None):
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

            fields = {}
            # Nella funzione send(), subito dopo aver letto l'immagine (righe ~780-790)
            if image_path:
                with open(image_path, 'rb') as f:
                    img_bytes = f.read()
                
                print(f"🔴 DEBUG INVIO 1: image_path={image_path}")
                print(f"🔴 DEBUG INVIO 2: img_bytes size={len(img_bytes)}")
                
                ext = os.path.splitext(image_path)[1].lower()
                if ext in ['.jpg', '.jpeg']:
                    image_type = 'jpg'
                elif ext == '.png':
                    image_type = 'png'
                elif ext == '.gif':
                    image_type = 'gif'
                elif ext == '.webp':
                    image_type = 'webp'
                elif ext == '.bmp':
                    image_type = 'bmp'
                else:
                    image_type = 'img'
                
                print(f"🔴 DEBUG INVIO 3: image_type={image_type}")
                
                fields[FIELD_IMAGE] = [image_type, img_bytes]
                
                print(f"🔴 DEBUG INVIO 4: fields[FIELD_IMAGE] type={type(fields[FIELD_IMAGE])}")
                print(f"🔴 DEBUG INVIO 5: fields[FIELD_IMAGE][0]={fields[FIELD_IMAGE][0]} type={type(fields[FIELD_IMAGE][0])}")
                print(f"🔴 DEBUG INVIO 6: fields[FIELD_IMAGE][1] size={len(fields[FIELD_IMAGE][1])}")

            if audio_path:
                with open(audio_path, 'rb') as f:
                    audio_bytes = f.read()
                if audio_mode is None:
                    audio_mode = 0x08
                fields[FIELD_AUDIO] = [audio_mode, audio_bytes]

            if file_attachments:
                attachments_list = []
                for fname, fpath in file_attachments:
                    with open(fpath, 'rb') as f:
                        file_bytes = f.read()
                    attachments_list.append([fname, file_bytes])
                fields[FIELD_FILE_ATTACHMENTS] = attachments_list

            if telemetry:
                fields[FIELD_TELEMETRY] = telemetry.packed()

            desired_method = 0x01
            method_name = 'opportunistic'

            msg = LXMF.LXMessage(
                dest,
                source_dest,
                content,
                title,
                fields=fields if fields else None,
                desired_method=desired_method
            )

            msg.stamp_cost = self.config.get('stamp_cost')
            if self.config.get('try_propagation_on_fail') and self.propagation_node:
                msg.try_propagation_on_fail = True

            msg.pack()
            msg_hash = msg.hash.hex()

            # Salva raw su disco
            self._save_raw(msg_hash, msg.packed)

            # Salva messaggio inviato nel database
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO sent_messages (hash, destination_hash, content, title, sent_at, status, method)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (msg_hash, dest_hash, content, title, time.time(), 'sending', method_name))
                conn.commit()
                conn.close()

            if callbacks:
                if 'delivery' in callbacks:
                    self.delivery_callbacks[msg_hash] = callbacks['delivery']
                if 'failed' in callbacks:
                    self.failed_callbacks[msg_hash] = callbacks['failed']
                if 'progress' in callbacks:
                    self.progress_callbacks[msg_hash] = callbacks['progress']

            msg.register_delivery_callback(self._delivery_callback)
            msg.register_failed_callback(self._failed_callback)

            self.router.handle_outbound(msg)

            return {
                'success': True,
                'hash': msg_hash,
                'method': method_name,
                'fallback_enabled': hasattr(msg, 'try_propagation_on_fail') and msg.try_propagation_on_fail,
                'status': 'sending'
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

            # Salva raw su disco
            self._save_raw(msg_hash, msg.packed)

            # Salva comando inviato nel database
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO sent_messages (hash, destination_hash, content, sent_at, status, method)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (msg_hash, dest_hash, "[Command]", time.time(), 'sending', method_name))
                conn.commit()
                conn.close()

            if callbacks:
                if 'delivery' in callbacks:
                    self.delivery_callbacks[msg_hash] = callbacks['delivery']
                if 'failed' in callbacks:
                    self.failed_callbacks[msg_hash] = callbacks['failed']
                if 'progress' in callbacks:
                    self.progress_callbacks[msg_hash] = callbacks['progress']

            msg.register_delivery_callback(self._delivery_callback)
            msg.register_failed_callback(self._failed_callback)

            self.router.handle_outbound(msg)

            return {
                'success': True,
                'hash': msg_hash,
                'method': method_name,
                'status': 'sending'
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

            # Salva raw su disco
            self._save_raw(msg_hash, msg.packed)

            # Salva richiesta nel database
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO sent_messages (hash, destination_hash, content, sent_at, status, method)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (msg_hash, dest_hash, "[Telemetry Request]", time.time(), 'sending', method_name))
                conn.commit()
                conn.close()

            if callbacks:
                if 'delivery' in callbacks:
                    self.delivery_callbacks[msg_hash] = callbacks['delivery']
                if 'failed' in callbacks:
                    self.failed_callbacks[msg_hash] = callbacks['failed']
                if 'progress' in callbacks:
                    self.progress_callbacks[msg_hash] = callbacks['progress']

            msg.register_delivery_callback(self._delivery_callback)
            msg.register_failed_callback(self._failed_callback)

            self.router.handle_outbound(msg)

            return {
                'success': True,
                'hash': msg_hash,
                'method': method_name,
                'status': 'sending'
            }

        except Exception as e:
            traceback.print_exc()
            return {'success': False, 'error': str(e)}

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

    def get_peer_name(self, dest_hash):
        """Restituisce il nome visualizzato di un peer dato un hash di destinazione."""
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
        """Restituisce una lista di tutte le identità (con nome e identity_hash) ordinate alfabeticamente."""
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            c.execute("SELECT identity_hash, display_name, group_name FROM identities")
            rows = c.fetchall()
            conn.close()

        # Ordina in Python ignorando caratteri speciali iniziali
        def sort_key(row):
            name = row[1] or ""
            clean = re.sub(r'^[^a-zA-Z0-9]+', '', name)
            return clean.lower()

        rows.sort(key=sort_key)
        return [{'hash': r[0], 'name': r[1], 'group': r[2]} for r in rows]

    def add_peer_manual(self, identity_hash, name, group=None):
        """Aggiunge un'identità manualmente."""
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
        """Importa gli ultimi annunci lxmf.delivery dal database announces.db."""
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

                    # Estrai display_name da app_data
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

    def _progress_callback(self, message):
        if message.hash:
            msg_hash = message.hash.hex()
            if msg_hash in self.progress_callbacks:
                self.progress_callbacks[msg_hash]({
                    'hash': msg_hash,
                    'progress': message.progress,
                    'status': 'sending'
                })

# Classe per gestire gli annunci in arrivo
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

# -------------------------------------------------------------------
# CLI di esempio con menu interattivo
# -------------------------------------------------------------------
if __name__ == "__main__":
    m = Messenger()

    print(f"\n🆔 La tua identità: {m.get_hash()}")
    print(f"📛 Nome visualizzato: {m.display_name}")

    def on_message(msg):
        sender = m.get_peer_name(msg['from'])
        print(f"\n📨 Da: {sender}")
        print(f"   🆔 Hash mittente: {msg['from']}")
        print(f"   🆔 Hash messaggio: {msg['hash']}")
        if msg['title']:
            print(f"   📌 {msg['title']}")
        # Mostra solo i primi 100 caratteri del contenuto
        content_preview = msg['content'][:100] + ('...' if len(msg['content']) > 100 else '')
        print(f"   💬 {content_preview}")
        if msg.get('attachments'):
            print(f"   📎 Allegati: {len(msg['attachments'])}")
        if msg.get('appearance'):
            print(f"   🎨 Appearance: {msg['appearance']}")
        if msg.get('telemetry'):
            print(f"   📊 Telemetria presente")
        # Info tecniche
        tech = []
        if msg.get('rssi') is not None:
            tech.append(f"RSSI: {msg['rssi']} dBm")
        if msg.get('snr') is not None:
            tech.append(f"SNR: {msg['snr']} dB")
        if msg.get('q') is not None:
            tech.append(f"Q: {msg['q']}%")
        if msg.get('hops') is not None:
            tech.append(f"Hops: {msg['hops']}")
        if tech:
            print(f"   📡 {' | '.join(tech)}")
        # Mostra la dimensione del pacchetto
        if msg.get('packed_size'):
            print(f"   📦 Dimensione pacchetto: {msg['packed_size']} byte")
        # Mostra i fields in modo chiaro
        if msg.get('fields'):
            print("   📦 Campi del messaggio:")
            field_names = {
                0x02: "📊 Telemetria",
                0x03: "📊 Stream Telemetria",
                0x04: "🎨 Icona/Appearance",
                0x05: "📎 Allegati",
                0x06: "🖼️ Immagine",
                0x07: "🎵 Audio",
                0x09: "⚙️ Comandi",
            }
            for ftype, fvalue in msg['fields'].items():
                fname = field_names.get(ftype, f"🔹 0x{ftype:02x}")
                if ftype == FIELD_IMAGE and isinstance(fvalue, (list, tuple)) and len(fvalue) == 2:
                    img_bytes = fvalue[1]
                    fmt = guess_image_format(img_bytes)
                    preview = f"lista di 2 elementi, formato {fmt}, {len(img_bytes)} byte"
                elif isinstance(fvalue, bytes):
                    preview = f"bytes ({len(fvalue)} byte)"
                elif isinstance(fvalue, list):
                    preview = f"lista di {len(fvalue)} elementi"
                elif isinstance(fvalue, dict):
                    preview = f"dizionario di {len(fvalue)} elementi"
                else:
                    preview = str(fvalue)
                print(f"      {fname}: {preview}")
        # Dettaglio allegati salvati
        if msg.get('attachments'):
            for att in msg['attachments']:
                if len(att) == 2:
                    att_type, att_path = att
                    if os.path.exists(att_path):
                        size = os.path.getsize(att_path)
                        if att_type == 'image':
                            # Leggi i primi byte per determinare il formato
                            with open(att_path, 'rb') as f:
                                header = f.read(12)
                            fmt = guess_image_format(header)
                            print(f"      - Immagine {fmt}: {os.path.basename(att_path)} ({size} byte)")
                        else:
                            print(f"      - {att_type}: {os.path.basename(att_path)} ({size} byte)")
                    else:
                        print(f"      - {att_type}: file non trovato")
        # Indica se il raw è disponibile
        if msg.get('raw_hex'):
            print(f"   💾 Raw salvato su disco (usa il comando 'raw <hash>' per vederlo)")

    m.message_callback = on_message
    m.announce()
    print("\n👂 In ascolto... (Ctrl+C per uscire)\n")

    while True:
        print("\n📋 MENU")
        print("1. Invia messaggio")
        print("2. Aggiungi identità (con hash identità)")
        print("3. Lista identità (da database)")
        print("4. Sincronizza messaggi")
        print("5. Stato messaggi inviati")
        print("6. Richiedi telemetria")
        print("7. Invia PING")
        print("8. Invia ECHO")
        print("9. Invia SIGNAL REPORT")
        print("10. Invia annuncio")
        print("11. Importa peer da cache")
        print("12. Mostra raw messaggio (da disco)")
        print("13. Esci")

        choice = input("👉 ").strip()

        if choice == '1':
            dest = input("Hash destinatario (lxmf.delivery): ").strip()
            name = m.get_peer_name(dest)
            if name != dest[:8]:
                print(f"📇 Destinatario: {name}")
            else:
                print("⚠️  Destinatario non in rubrica, continuo comunque.")

            content = input("Messaggio: ").strip()
            title = input("Titolo (opzionale): ").strip()

            def on_delivery(info):
                print(f"\n✅ Messaggio {info['hash'][:8]} consegnato!")
            def on_failed(info):
                print(f"\n❌ Messaggio {info['hash'][:8]} fallito dopo {info['attempts']} tentativi")
            def on_progress(info):
                print(f"\r📤 Progresso: {info['progress']*100:.1f}%", end='')
            callbacks = {'delivery': on_delivery, 'failed': on_failed, 'progress': on_progress}

            result = m.send(dest, content, title, callbacks=callbacks)
            if result['success']:
                print(f"\n📤 Invio avviato: {result['hash'][:8]}...")
                print(f"   Metodo: {result['method']}")
                print(f"   Fallback: {'✅' if result.get('fallback_enabled') else '❌'}")
            else:
                print(f"❌ Errore: {result['error']}")

        elif choice == '2':
            identity = input("Identity hash (32 byte hex): ").strip()
            name = input("Nome: ").strip()
            group = input("Gruppo (opzionale): ").strip()
            if not group:
                group = None
            m.add_peer_manual(identity, name, group)

        elif choice == '3':
            peers = m.list_peers()
            if peers:
                for p in peers:
                    group_str = f" [{p['group']}]" if p['group'] else ""
                    print(f"👤 {p['name']}{group_str}: {p['hash']}")
            else:
                print("📭 Nessuna identità")

        elif choice == '4':
            result = m.sync_messages()
            if result['success']:
                print(f"✅ Sincronizzato, {result['new']} nuovi messaggi")
            else:
                print(f"❌ Errore: {result.get('error')}")

        elif choice == '5':
            with m.db_lock:
                conn = sqlite3.connect(m.peers_db, timeout=10)
                c = conn.cursor()
                c.execute("SELECT hash, destination_hash, content, status, sent_at FROM sent_messages ORDER BY sent_at DESC LIMIT 10")
                rows = c.fetchall()
                conn.close()
            if rows:
                for row in rows:
                    hash_short = row[0][:8]
                    dest = row[1][:8]
                    content = (row[2][:30] + "...") if len(row[2]) > 30 else row[2]
                    status_icon = {
                        'sending': '⏳',
                        'delivered': '✅',
                        'failed': '❌',
                        'cancelled': '🚫'
                    }.get(row[3], '❓')
                    print(f"{status_icon} {hash_short} -> {dest}: {content} ({row[3]})")
            else:
                print("📭 Nessun messaggio inviato")

        elif choice == '6':
            dest = input("Hash destinatario per richiesta telemetria: ").strip()
            def on_req_delivery(info):
                print(f"\n✅ Richiesta telemetria consegnata a {info['hash'][:8]}!")
            def on_req_failed(info):
                print(f"\n❌ Richiesta telemetria fallita dopo {info['attempts']} tentativi")
            callbacks = {'delivery': on_req_delivery, 'failed': on_req_failed}
            result = m.request_telemetry(dest, callbacks=callbacks)
            if result['success']:
                print(f"\n📤 Richiesta telemetria inviata: {result['hash'][:8]}...")
            else:
                print(f"❌ Errore: {result['error']}")

        elif choice == '7':
            dest = input("Hash destinatario per PING: ").strip()
            result = m.send_command(dest, Commands.PING)
            if result['success']:
                print(f"📤 PING inviato: {result['hash'][:8]}")
            else:
                print(f"❌ Errore: {result['error']}")

        elif choice == '8':
            dest = input("Hash destinatario per ECHO: ").strip()
            text = input("Testo da ripetere: ").strip()
            result = m.send_command(dest, Commands.ECHO, text.encode('utf-8'))
            if result['success']:
                print(f"📤 ECHO inviato: {result['hash'][:8]}")
            else:
                print(f"❌ Errore: {result['error']}")

        elif choice == '9':
            dest = input("Hash destinatario per SIGNAL REPORT: ").strip()
            result = m.send_command(dest, Commands.SIGNAL_REPORT)
            if result['success']:
                print(f"📤 SIGNAL REPORT inviato: {result['hash'][:8]}")
            else:
                print(f"❌ Errore: {result['error']}")

        elif choice == '10':
            m.announce()

        elif choice == '11':
            count = m.import_peers_from_cache()
            print(f"✅ Importati {count} peer.")

        elif choice == '12':
            h = input("Inserisci hash del messaggio: ").strip()
            # Cerca prima in memoria, poi su disco
            if h in m.received_raw:
                print(f"\n📦 Raw hex ({len(m.received_raw[h]['hex'])//2} bytes):")
                print(m.received_raw[h]['hex'])
                print(f"\n📦 Raw ascii:")
                print(m.received_raw[h]['ascii'])
            else:
                # Prova a leggere dal disco
                hex_path = os.path.join(m.raw_dir, f"{h}.hex")
                raw_path = os.path.join(m.raw_dir, f"{h}.raw")
                if os.path.exists(hex_path):
                    with open(hex_path, 'r') as f:
                        hex_data = f.read().strip()
                    print(f"\n📦 Raw hex ({len(hex_data)//2} bytes) (da disco):")
                    print(hex_data)
                    # Genera ascii dal raw se disponibile
                    if os.path.exists(raw_path):
                        with open(raw_path, 'rb') as f:
                            raw = f.read()
                        ascii_data = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw)
                        print(f"\n📦 Raw ascii (da disco):")
                        print(ascii_data)
                    else:
                        print("⚠️  File .raw non trovato, impossibile mostrare ascii.")
                elif os.path.exists(raw_path):
                    # Solo raw, generiamo hex e ascii
                    with open(raw_path, 'rb') as f:
                        raw = f.read()
                    hex_data = raw.hex()
                    print(f"\n📦 Raw hex ({len(raw)} bytes) (generato da .raw):")
                    print(hex_data)
                    ascii_data = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw)
                    print(f"\n📦 Raw ascii:")
                    print(ascii_data)
                else:
                    print("❌ Messaggio non trovato né in memoria né su disco.")

        elif choice == '13':
            print("Chiusura...")
            break