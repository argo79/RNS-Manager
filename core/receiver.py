#!/usr/bin/env python3
# core/receiver.py - Modulo per la ricezione e gestione dei messaggi LXMF in arrivo

import os
import time
import RNS
import LXMF
import sqlite3
import threading
from .base import (FIELD_TELEMETRY, FIELD_TELEMETRY_STREAM, FIELD_ICON_APPEARANCE,
                   FIELD_FILE_ATTACHMENTS, FIELD_IMAGE, FIELD_AUDIO, FIELD_COMMANDS,
                   Commands, guess_image_format)
import telemeter
import audio_codec

class Receiver:
    def __init__(self, storagepath, db_lock, peers_db, telemetry_dir, attachments_dir, raw_dir, config, sender=None):
        self.storagepath = storagepath
        self.db_lock = db_lock
        self.peers_db = peers_db
        self.telemetry_dir = telemetry_dir
        self.attachments_dir = attachments_dir
        self.raw_dir = raw_dir
        self.config = config
        self.sender = sender  # opzionale, per rispondere a comandi
        self.message_callback = None

    def set_message_callback(self, callback):
        """Imposta la funzione da chiamare quando arriva un messaggio."""
        self.message_callback = callback

    def _save_attachment(self, msg_hash, typ, data, filename_hint=None):
        """Salva un allegato (immagine, audio, file) su disco."""
        if not data or len(data) == 0:
            return None
        try:
            if typ == 'image':
                # Rileva formato dai magic number
                ext = '.img'
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
        """Salva un oggetto telemetria in formato JSON e binario."""
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

    def _update_peer_from_message(self, source_hash, message):
        """Aggiorna le informazioni del peer nel database in base al messaggio ricevuto."""
        with self.db_lock:
            conn = sqlite3.connect(self.peers_db, timeout=10)
            c = conn.cursor()
            now = time.time()
            hops = RNS.Transport.hops_to(bytes.fromhex(source_hash)) if source_hash else None
            rssi = getattr(message, 'rssi', None)
            snr = getattr(message, 'snr', None)
            q = getattr(message, 'q', None)

            # Cerca se esiste già una destinazione per questo source_hash
            c.execute("SELECT identity_hash FROM destinations WHERE destination_hash = ?", (source_hash,))
            row = c.fetchone()
            identity_hash = row[0] if row else None

            # Se non abbiamo identity_hash, prova a veder se l'identità è nota a Reticulum
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

    def on_message(self, lxmf_message):
        """Callback principale per i messaggi in arrivo."""
        attachments = []
        telemetry_obj = None
        appearance = None

        if lxmf_message.fields:
            # --- IMMAGINE ---
            if FIELD_IMAGE in lxmf_message.fields:
                img_data = lxmf_message.fields[FIELD_IMAGE]
                if isinstance(img_data, (list, tuple)) and len(img_data) == 2:
                    image_type = img_data[0]
                    image_bytes = img_data[1]
                    if isinstance(image_bytes, bytes):
                        path = self._save_attachment(lxmf_message.hash.hex(), "image", image_bytes, filename_hint=image_type)
                        if path:
                            attachments.append(("image", path))

            # --- AUDIO ---
            if FIELD_AUDIO in lxmf_message.fields:
                audio_data = lxmf_message.fields[FIELD_AUDIO]
                if isinstance(audio_data, (list, tuple)) and len(audio_data) == 2:
                    audio_mode = audio_data[0]
                    audio_bytes = audio_data[1]
                    if isinstance(audio_bytes, bytes):
                        hint = (audio_mode, 'raw')
                        raw_path = self._save_attachment(lxmf_message.hash.hex(), "audio", audio_bytes, filename_hint=hint)
                        if raw_path:
                            attachments.append(("audio_raw", raw_path))

                        # Decodifica Codec2 se disponibile
                        if 1 <= audio_mode <= 9 and audio_codec.CODEC2_AVAILABLE:
                            try:
                                pcm_data = audio_codec.decode_codec2(audio_bytes, audio_mode)
                                if pcm_data:
                                    wav_path = os.path.join(self.attachments_dir, f"{lxmf_message.hash.hex()}_audio_c2_{audio_mode}.wav")
                                    if audio_codec.samples_to_wav(pcm_data, wav_path):
                                        attachments.append(("audio_wav", wav_path))
                                    ogg_path = os.path.join(self.attachments_dir, f"{lxmf_message.hash.hex()}_audio_c2_{audio_mode}.ogg")
                                    if audio_codec.samples_to_ogg(pcm_data, ogg_path):
                                        attachments.append(("audio_ogg", ogg_path))
                            except Exception as e:
                                RNS.log(f"Errore decodifica Codec2: {e}", RNS.LOG_WARNING)

                        # Opus
                        elif audio_mode >= 16:
                            new_path = raw_path.replace('.aud', '.opus')
                            try:
                                os.rename(raw_path, new_path)
                                attachments.remove(("audio_raw", raw_path))
                                attachments.append(("audio", new_path))
                            except Exception as e:
                                RNS.log(f"Errore rinomina Opus: {e}", RNS.LOG_WARNING)

            # --- FILE ATTACHMENTS ---
            if FIELD_FILE_ATTACHMENTS in lxmf_message.fields:
                file_list = lxmf_message.fields[FIELD_FILE_ATTACHMENTS]
                if isinstance(file_list, list):
                    for item in file_list:
                        if isinstance(item, (list, tuple)) and len(item) == 2:
                            file_name = item[0]
                            file_bytes = item[1]
                            if isinstance(file_bytes, bytes):
                                path = self._save_attachment(lxmf_message.hash.hex(), f"file_{file_name}", file_bytes, filename_hint=file_name)
                                if path:
                                    attachments.append(("file", path))

            # --- TELEMETRIA SINGOLA ---
            if FIELD_TELEMETRY in lxmf_message.fields:
                try:
                    telemetry_obj = telemeter.Telemeter.from_packed(lxmf_message.fields[FIELD_TELEMETRY])
                    self._save_telemetry(lxmf_message.source_hash.hex(), telemetry_obj)
                except Exception as e:
                    RNS.log(f"Errore parsing telemetria: {e}", RNS.LOG_ERROR)

            # --- STREAM DI TELEMETRIA ---
            if FIELD_TELEMETRY_STREAM in lxmf_message.fields:
                try:
                    stream = []
                    for packed in lxmf_message.fields[FIELD_TELEMETRY_STREAM]:
                        t = telemeter.Telemeter.from_packed(packed)
                        if t:
                            stream.append(t)
                    telemetry_obj = stream
                except Exception as e:
                    RNS.log(f"Errore parsing stream telemetria: {e}", RNS.LOG_ERROR)

            # --- ICONA APPEARANCE ---
            if FIELD_ICON_APPEARANCE in lxmf_message.fields:
                appearance = lxmf_message.fields[FIELD_ICON_APPEARANCE]

            # --- COMANDI ---
            if FIELD_COMMANDS in lxmf_message.fields and self.sender:
                self._handle_commands(lxmf_message, lxmf_message.fields[FIELD_COMMANDS])

        # --- RAW ---
        packed_size = len(lxmf_message.packed) if lxmf_message.packed else 0
        raw_hex = lxmf_message.packed.hex() if lxmf_message.packed else None
        raw_ascii = ''.join(chr(b) if 32 <= b < 127 else '.' for b in lxmf_message.packed) if lxmf_message.packed else None
        if lxmf_message.packed:
            self._save_raw(lxmf_message.hash.hex(), lxmf_message.packed)

        # Costruisci dizionario del messaggio
        msg_dict = {
            'from': lxmf_message.source_hash.hex(),
            'to': lxmf_message.destination_hash.hex(),
            'content': lxmf_message.content.decode('utf-8') if lxmf_message.content else '',
            'title': lxmf_message.title.decode('utf-8') if lxmf_message.title else '',
            'time': lxmf_message.timestamp,
            'hash': lxmf_message.hash.hex(),
            'size': len(lxmf_message.content) if lxmf_message.content else 0,
            'packed_size': packed_size,
            'method': ('propagated' if lxmf_message.method == 0x03 else
                      ('opportunistic' if lxmf_message.method == 0x01 else 'direct')),
            'signature_valid': lxmf_message.signature_validated,
            'attachments': attachments,
            'telemetry': telemetry_obj,
            'appearance': appearance,
            'rssi': getattr(lxmf_message, 'rssi', None),
            'snr': getattr(lxmf_message, 'snr', None),
            'q': getattr(lxmf_message, 'q', None),
            'hops': RNS.Transport.hops_to(lxmf_message.source_hash) if lxmf_message.source_hash else None,
            'raw_hex': raw_hex,
            'raw_ascii': raw_ascii,
            'fields': lxmf_message.fields,
        }

        # Aggiorna informazioni del peer
        if lxmf_message.source_hash:
            self._update_peer_from_message(lxmf_message.source_hash.hex(), lxmf_message)

        # Chiama il callback esterno
        if self.message_callback:
            self.message_callback(msg_dict)

    def _handle_commands(self, message, commands):
        """Gestisce i comandi ricevuti e risponde automaticamente."""
        source = message.source_hash.hex()
        for cmd_dict in commands:
            if not isinstance(cmd_dict, dict):
                continue
            for cmd_type, cmd_data in cmd_dict.items():
                if cmd_type == Commands.PING:
                    self.sender.send(source, "Ping reply", callbacks=None)
                elif cmd_type == Commands.ECHO:
                    if isinstance(cmd_data, bytes):
                        text = cmd_data.decode('utf-8', errors='replace')
                    else:
                        text = str(cmd_data)
                    self.sender.send(source, f"Echo reply: {text}", callbacks=None)
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
                    self.sender.send(source, "\n".join(report), callbacks=None)
                elif cmd_type == Commands.TELEMETRY_REQUEST:
                    # La richiesta di telemetria è già gestita altrove? Forse no.
                    # Qui potremmo rispondere con i nostri dati telemetrici.
                    # Per semplicità, ignoriamo o rimandiamo a sender.request_telemetry?
                    # In un'implementazione completa, bisognerebbe rispondere.
                    pass