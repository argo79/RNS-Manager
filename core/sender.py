#!/usr/bin/env python3
# core/sender.py - Modulo per l'invio di messaggi LXMF

import os
import time
import RNS
import LXMF
import traceback
import threading
import sqlite3
from .base import FIELD_IMAGE, FIELD_AUDIO, FIELD_FILE_ATTACHMENTS, FIELD_TELEMETRY, FIELD_COMMANDS, Commands

class Sender:
    def __init__(self, router, identity, local_dest, config, propagation_node, db_lock, peers_db, raw_dir):
        self.router = router
        self.identity = identity
        self.local_dest = local_dest
        self.config = config
        self.propagation_node = propagation_node
        self.db_lock = db_lock
        self.peers_db = peers_db
        self.raw_dir = raw_dir

        # Callback registry
        self.delivery_callbacks = {}
        self.failed_callbacks = {}
        self.progress_callbacks = {}

        # Monitoraggio progresso upload
        self._monitor_threads = {}   # msg_hash -> thread
        self._monitor_stop = {}       # msg_hash -> flag

    def _save_raw(self, msg_hash, packed):
        """Salva il pacchetto raw in formato binario e hex."""
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

    def _monitor_upload_progress(self, msg_hash, lxmf_message, total_size):
        """Monitora il progresso di un upload e stampa aggiornamenti."""
        start_time = time.time()
        last_progress = 0.0
        while not self._monitor_stop.get(msg_hash, False):
            progress = lxmf_message.progress
            if progress is None:
                progress = 0.0
            if progress > last_progress or progress >= 1.0:
                elapsed = time.time() - start_time
                bytes_done = int(progress * total_size)
                speed_bps = (bytes_done * 8) / elapsed if elapsed > 0 else 0
                # Formatta velocità
                if speed_bps >= 1000:
                    speed_str = f"{speed_bps/1000:.1f} kbit/s"
                else:
                    speed_str = f"{speed_bps:.0f} bit/s"
                print(f"\r📤 Progresso: {progress*100:.1f}%  {bytes_done}/{total_size} byte  {speed_str}    ", end='')
                last_progress = progress
                if progress >= 1.0:
                    print()  # nuova riga a fine
                    break
            time.sleep(0.5)
        # Pulizia
        self._monitor_threads.pop(msg_hash, None)
        self._monitor_stop.pop(msg_hash, None)

    def _delivery_callback(self, lxmf_message):
        """Chiamato quando un messaggio viene consegnato."""
        msg_hash = lxmf_message.hash.hex()
        # Ferma monitoraggio se attivo
        if msg_hash in self._monitor_threads:
            self._monitor_stop[msg_hash] = True
        # Aggiorna database
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            c.execute('''
                UPDATE sent_messages SET status = ?, delivered_at = ? WHERE hash = ?
            ''', ('delivered', time.time(), msg_hash))
            conn.commit()
            conn.close()
        RNS.log(f"✅ Consegna riuscita per {msg_hash}", RNS.LOG_INFO)
        if msg_hash in self.delivery_callbacks:
            self.delivery_callbacks[msg_hash]({
                'hash': msg_hash,
                'status': 'delivered',
                'time': time.time()
            })
            del self.delivery_callbacks[msg_hash]

    def _failed_callback(self, lxmf_message):
        """Chiamato quando un messaggio fallisce."""
        msg_hash = lxmf_message.hash.hex()
        # Ferma monitoraggio se attivo
        if msg_hash in self._monitor_threads:
            self._monitor_stop[msg_hash] = True

        # Se è previsto il fallback a propagazione
        if hasattr(lxmf_message, 'try_propagation_on_fail') and lxmf_message.try_propagation_on_fail:
            lxmf_message.desired_method = 0x03  # PROPAGATED
            lxmf_message.try_propagation_on_fail = False
            lxmf_message.delivery_attempts = 0
            lxmf_message.packed = None
            lxmf_message.state = LXMF.LXMessage.GENERATING
            self.router.handle_outbound(lxmf_message)
            return

        # Aggiorna database
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            c.execute('''
                UPDATE sent_messages SET status = ?, failed_at = ?, attempts = ? WHERE hash = ?
            ''', ('failed', time.time(), lxmf_message.delivery_attempts, msg_hash))
            conn.commit()
            conn.close()
        RNS.log(f"❌ Invio fallito per {msg_hash}", RNS.LOG_INFO)
        if msg_hash in self.failed_callbacks:
            self.failed_callbacks[msg_hash]({
                'hash': msg_hash,
                'status': 'failed',
                'time': time.time(),
                'attempts': lxmf_message.delivery_attempts
            })
            del self.failed_callbacks[msg_hash]

    def send(self, dest_hash, content, title="", callbacks=None,
             image_path=None, image_type=None,
             audio_path=None, audio_mode=None,
             file_attachments=None,
             telemetry=None):
        """
        Invia un messaggio LXMF.
        - dest_hash: hash della destinazione (lxmf.delivery)
        - content: testo del messaggio
        - title: titolo opzionale
        - callbacks: dict con chiavi 'delivery', 'failed', 'progress' (funzioni)
        - image_path: percorso immagine
        - image_type: tipo immagine (1=JPEG, 2=PNG, 3=GIF, 0=altro)
        - audio_path: percorso audio
        - audio_mode: modalità audio (es. 0x08 per Codec2 2400)
        - file_attachments: lista di tuple (nome_file, percorso_file)
        - telemetry: istanza di Telemeter
        """
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

            fields = {}
            if image_path:
                with open(image_path, 'rb') as f:
                    img_bytes = f.read()
                if image_type is None:
                    ext = os.path.splitext(image_path)[1].lower()
                    image_type = 1 if ext in ('.jpg','.jpeg') else 2 if ext == '.png' else 3 if ext == '.gif' else 0
                fields[FIELD_IMAGE] = [image_type, img_bytes]

            if audio_path:
                with open(audio_path, 'rb') as f:
                    audio_bytes = f.read()
                if audio_mode is None:
                    audio_mode = 0x08  # Codec2 2400 default
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

            # Metodo opportunistico di default
            desired_method = 0x01
            method_name = 'opportunistic'

            msg = LXMF.LXMessage(
                dest,
                self.local_dest,
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

            # Salva nel database dei messaggi inviati
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO sent_messages (hash, destination_hash, content, title, sent_at, status, method)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (msg_hash, dest_hash, content, title, time.time(), 'sending', method_name))
                conn.commit()
                conn.close()

            # Avvia monitoraggio se il messaggio è una risorsa (grande)
            if msg.representation == LXMF.LXMessage.RESOURCE:
                if msg_hash not in self._monitor_threads:
                    self._monitor_stop[msg_hash] = False
                    t = threading.Thread(target=self._monitor_upload_progress,
                                         args=(msg_hash, msg, msg.packed_size))
                    t.daemon = True
                    t.start()
                    self._monitor_threads[msg_hash] = t

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
        """
        Invia un comando LXMF (PING, ECHO, SIGNAL_REPORT).
        """
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

            fields = {FIELD_COMMANDS: [{command_type: command_data}]}
            desired_method = 0x01
            method_name = 'opportunistic'

            msg = LXMF.LXMessage(
                dest,
                self.local_dest,
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
            self._save_raw(msg_hash, msg.packed)

            if callbacks:
                if 'delivery' in callbacks:
                    self.delivery_callbacks[msg_hash] = callbacks['delivery']
                if 'failed' in callbacks:
                    self.failed_callbacks[msg_hash] = callbacks['failed']
                if 'progress' in callbacks:
                    self.progress_callbacks[msg_hash] = callbacks['progress']

            msg.register_delivery_callback(self._delivery_callback)
            msg.register_failed_callback(self._failed_callback)

            # Salva nel database
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO sent_messages (hash, destination_hash, content, sent_at, status, method)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (msg_hash, dest_hash, "[Command]", time.time(), 'sending', method_name))
                conn.commit()
                conn.close()

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
        """
        Invia una richiesta di telemetria.
        """
        try:
            dest_hash_bytes = bytes.fromhex(dest_hash)
            dest_identity = RNS.Identity.recall(dest_hash_bytes)
            if not dest_identity:
                RNS.Transport.request_path(dest_hash_bytes)
                return {'success': False, 'error': 'Destinatario sconosciuto, richiesto path'}

            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )

            if timebase is None:
                timebase = 0  # richiedi tutta la telemetria (o ultima, ma serve metodo per ottenerla)

            commands = [{Commands.TELEMETRY_REQUEST: [timebase, is_collector_request]}]
            fields = {FIELD_COMMANDS: commands}
            desired_method = 0x01
            method_name = 'opportunistic'

            msg = LXMF.LXMessage(
                dest,
                self.local_dest,
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
            self._save_raw(msg_hash, msg.packed)

            if callbacks:
                if 'delivery' in callbacks:
                    self.delivery_callbacks[msg_hash] = callbacks['delivery']
                if 'failed' in callbacks:
                    self.failed_callbacks[msg_hash] = callbacks['failed']
                if 'progress' in callbacks:
                    self.progress_callbacks[msg_hash] = callbacks['progress']

            msg.register_delivery_callback(self._delivery_callback)
            msg.register_failed_callback(self._failed_callback)

            # Salva nel database
            with self.db_lock:
                conn = sqlite3.connect(self.peers_db, timeout=10)
                c = conn.cursor()
                c.execute('''
                    INSERT INTO sent_messages (hash, destination_hash, content, sent_at, status, method)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (msg_hash, dest_hash, "[Telemetry Request]", time.time(), 'sending', method_name))
                conn.commit()
                conn.close()

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