#!/usr/bin/env python3
"""
LXMF Message Sender
Gestisce l'invio di messaggi di testo e file tramite LXMF
Supporta Codec2, propagation node e multiple modalità di consegna
"""

import os
import time
import threading
import mimetypes
import msgpack
import RNS
import LXMF
import numpy as np
import subprocess
import tempfile
import wave
import json
import re

# Import per Codec2
try:
    from modules.audioproc import decode_codec2, samples_to_wav, encode_codec2
    CODEC2_AVAILABLE = True
    print("✅ Codec2 support available in sender")
except ImportError as e:
    CODEC2_AVAILABLE = False
    print(f"⚠️ Codec2 not available in sender: {e}")

# Configurazione propagation
CONFIG_FILE = os.path.expanduser("~/.rns_manager/config.json")

DEFAULT_CONFIG = {
    "propagation_node": None,
    "delivery_mode": "direct",
    "max_retries": 3,
    "preferred_audio": "opus",
    "save_sent": True,
    "auto_retry": True,
    "stamp_cost": None,
    "propagation_stamp_cost": 16,
    "accept_invalid_stamps": False,
    "max_stamp_retries": 3,
    "auto_send_voice": False,
    "try_propagation_on_fail": True
}

def load_config():
    """Carica configurazione propagation"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                for key, value in DEFAULT_CONFIG.items():
                    if key not in config:
                        config[key] = value
                return config
        except:
            return DEFAULT_CONFIG.copy()
    else:
        return DEFAULT_CONFIG.copy()

def save_config(config):
    """Salva configurazione propagation"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)


class LXMFMessageSender:
    """
    Classe dedicata all'invio di messaggi tramite LXMF
    Gestisce sia messaggi di testo che file (immagini, audio, generici)
    Supporta Codec2, propagation node e modalità di consegna multiple
    """
    
    def __init__(self, lxmf_client, messages_lock, messages_buffer, conversations_dir, downloads_dir):
        self.client = lxmf_client
        self.messages_lock = messages_lock
        self.messages_buffer = messages_buffer
        self.conversations_dir = conversations_dir
        self.downloads_dir = downloads_dir
        self.config = load_config()
        
        self._processed_messages = set()
        self._processed_lock = threading.Lock()
        self._trusted_destinations = set()
        
        # Inizializza propagation node se configurato
        if self.config.get("propagation_node"):
            threading.Thread(target=self._set_propagation_node_internal, 
                            args=(self.config["propagation_node"],), 
                            daemon=True).start()
    
    # ========== METODI PER TRUSTED DESTINATIONS ==========
    
    def _is_trusted(self, dest_hash):
        """Verifica se una destinazione è trusted"""
        clean = self._clean_hash(dest_hash)
        return clean in self._trusted_destinations
    
    def add_trusted_destination(self, dest_hash):
        """Aggiunge una destinazione trusted"""
        clean = self._clean_hash(dest_hash)
        self._trusted_destinations.add(clean)
    
    def remove_trusted_destination(self, dest_hash):
        """Rimuove una destinazione trusted"""
        clean = self._clean_hash(dest_hash)
        if clean in self._trusted_destinations:
            self._trusted_destinations.remove(clean)
    
    # ========== METODI PER CONFIGURAZIONE PROPAGATION ==========
    
    def set_propagation_node(self, node_hash):
        """Imposta il propagation node nel router del client"""
        try:
            result = self._set_propagation_node_internal(node_hash)
            if result:
                self.config["propagation_node"] = node_hash
                save_config(self.config)
            return result
        except Exception as e:
            print(f"❌ Errore impostazione propagation node: {e}")
            return False
    
    def _set_propagation_node_internal(self, node_hash):
        """Imposta propagation node internamente"""
        if node_hash:
            node_bytes = bytes.fromhex(self._clean_hash(node_hash))
            self.client.router.set_outbound_propagation_node(node_bytes)
            print(f"✅ Propagation node impostato: {node_hash[:16]}")
            return True
        else:
            self.client.router.set_outbound_propagation_node(None)
            print("ℹ️ Propagation node rimosso")
            return True

    def set_stamp_cost(self, cost):
        """Imposta lo stamp cost per i messaggi in arrivo"""
        try:
            if cost is None or cost == '':
                self.client.router.stamp_cost = 0
            else:
                self.client.router.stamp_cost = int(cost)
            return True
        except Exception as e:
            print(f"❌ Errore impostazione stamp cost: {e}")
            return False
    
    def _delivery_link_available(self, dest_hash):
        """Verifica se un link di consegna diretto è disponibile"""
        try:
            dest_bytes = bytes.fromhex(self._clean_hash(dest_hash))
            return self.client.router.delivery_link_available(dest_bytes)
        except:
            return False
    
    def configure_delivery_mode(self, mode, propagation_node=None):
        """
        Configura la modalità di consegna
        
        mode: 
            - "direct": solo diretto
            - "propagation": sempre via propagation node
            - "hybrid": diretto con fallback a propagation
            - "auto": lascia decidere a LXMF
            - "opportunistic": usa OPPORTUNISTIC se disponibile
        """
        valid_modes = ["direct", "propagation", "hybrid", "auto", "opportunistic"]
        if mode not in valid_modes:
            print(f"❌ Modalità non valida. Usa: {valid_modes}")
            return False
        
        self.config["delivery_mode"] = mode
        
        if propagation_node:
            self.config["propagation_node"] = propagation_node
            self._set_propagation_node_internal(propagation_node)
        
        save_config(self.config)
        print(f"✅ Modalità consegna impostata a: {mode}")
        return True
    
    def sync_propagation(self):
        """Avvia sincronizzazione con propagation node"""
        try:
            self.client.router.request_messages_from_propagation_node(self.client.identity)
            print("📡 Richiesta sincronizzazione propagation node inviata")
            return True
        except Exception as e:
            print(f"❌ Errore sincronizzazione: {e}")
            return False

    def get_propagation_status(self):
        """Ottieni stato sincronizzazione"""
        try:
            state_map = {
                0: "idle",
                1: "path_requested",
                2: "link_establishing",
                3: "link_established",
                4: "request_sent",
                5: "receiving",
                6: "response_received",
                7: "complete",
                8: "no_path",
                9: "link_failed",
                10: "transfer_failed",
                11: "no_identity_received",
                12: "no_access",
                13: "failed"
            }
            
            state = self.client.router.propagation_transfer_state
            progress = self.client.router.propagation_transfer_progress * 100 if hasattr(self.client.router, 'propagation_transfer_progress') else 0
            messages = self.client.router.propagation_transfer_last_result if hasattr(self.client.router, 'propagation_transfer_last_result') else 0
            
            return {
                'state': state_map.get(state, 'unknown'),
                'progress': progress,
                'messages_received': messages
            }
        except Exception as e:
            print(f"❌ Errore lettura stato propagation: {e}")
            return None
    
    def get_propagation_node(self):
        """Restituisce il propagation node configurato"""
        return self.config.get("propagation_node")
    
    # ========== METODI PRINCIPALI PER INVIO ==========
    
    def send_text(self, dest_hash, content):
        """
        Invia messaggio di testo con modalità configurata
        """
        return self._send_with_config(dest_hash, content, is_file=False)
    
    def send_file(self, dest_hash, file_path, description="", audio_codec="opus", audio_mode=None):
        """
        Invia file come allegato con modalità configurata
        """
        return self._send_with_config(
            dest_hash, None, is_file=True, file_path=file_path,
            description=description, audio_codec=audio_codec, audio_mode=audio_mode
        )
    
    def _send_with_config(self, dest_hash, content, is_file=False, file_path=None,
                          description="", audio_codec="opus", audio_mode=None):
        """
        Invia messaggio usando la modalità configurata
        """
        if not dest_hash or len(dest_hash) < 32:
            return False, "Hash destinazione non valido", None
        
        mode = self.config.get("delivery_mode", "direct")
        
        if mode == "propagation":
            return self._send_propagated(dest_hash, content, is_file, file_path,
                                         description, audio_codec, audio_mode)
        elif mode == "hybrid":
            return self._send_hybrid(dest_hash, content, is_file, file_path,
                                     description, audio_codec, audio_mode)
        elif mode == "auto":
            return self._send_auto(dest_hash, content, is_file, file_path,
                                   description, audio_codec, audio_mode)
        elif mode == "opportunistic":
            return self._send_opportunistic(dest_hash, content, is_file, file_path,
                                           description, audio_codec, audio_mode)
        else:
            return self._send_direct(dest_hash, content, is_file, file_path,
                                     description, audio_codec, audio_mode)
    
    # ========== METODI PER LE VARIE MODALITÀ DI CONSEGNA ==========
    
    def _determine_desired_method(self, dest_hash):
        """Determina il metodo di consegna appropriato"""
        if self._delivery_link_available(dest_hash):
            return LXMF.LXMessage.DIRECT
        elif RNS.Identity.current_ratchet_id(bytes.fromhex(self._clean_hash(dest_hash))) is not None:
            return LXMF.LXMessage.OPPORTUNISTIC
        else:
            return LXMF.LXMessage.DIRECT
    
    def _get_destination_identity(self, dest_hash, timeout=10):
        """Ottiene identità della destinazione"""
        try:
            clean = self._clean_hash(dest_hash)
            if len(clean) > 32:
                clean = clean[:32]
            
            dest_bytes = bytes.fromhex(clean)
            
            if not RNS.Transport.has_path(dest_bytes):
                RNS.Transport.request_path(dest_bytes)
                for _ in range(20):
                    time.sleep(0.1)
                    if RNS.Transport.has_path(dest_bytes):
                        break
            
            return RNS.Identity.recall(dest_bytes)
        except Exception as e:
            self._log_debug('get_destination_error', {'error': str(e)})
            return None
    
    def _send_direct(self, dest_hash, content, is_file=False, file_path=None,
                     description="", audio_codec="opus", audio_mode=None):
        """Invia messaggio in modalità DIRECT (solo diretto)"""
        try:
            msg_id = self._generate_message_id()
            
            self._log_debug('send_direct', {
                'to': dest_hash[:16],
                'is_file': is_file,
                'msg_id': msg_id
            })
            
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                return False, "Destinazione sconosciuta", None
            
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
                file_size = 0
                msg_data['total_size'] = 0
            
            message.desired_method = LXMF.LXMessage.DIRECT
            message.try_propagation_on_fail = False
            
            if self.config.get("propagation_stamp_cost", 16) > 0:
                message.stamp_cost = self.config["propagation_stamp_cost"]
            
            if self._is_trusted(dest_hash):
                message.include_ticket = True
            
            with self.messages_lock:
                exists = False
                for m in self.messages_buffer:
                    if m.get('message_id') == msg_id:
                        exists = True
                        break
                
                if not exists:
                    self.messages_buffer.append(msg_data)
                    if len(self.messages_buffer) > 200:
                        self.messages_buffer[:] = self.messages_buffer[-200:]
            
            with self._processed_lock:
                self._processed_messages.add(msg_id)
            
            if is_file:
                self._register_file_callbacks(
                    message, dest_hash, file_path, file_size, msg_data, msg_id
                )
            else:
                self._register_text_callbacks(
                    message, dest_hash, content, msg_data, msg_id
                )
            
            self.client.router.handle_outbound(message)
            
            print(f"📤 Messaggio diretto inviato a {dest_hash[:16]}")
            return True, "Messaggio inviato in modalità DIRECT", msg_data
            
        except Exception as e:
            self._log_debug('send_direct_error', {'error': str(e)})
            import traceback
            traceback.print_exc()
            return False, str(e), None
    
    def _send_opportunistic(self, dest_hash, content, is_file=False, file_path=None,
                           description="", audio_codec="opus", audio_mode=None):
        """Invia messaggio in modalità OPPORTUNISTIC"""
        try:
            msg_id = self._generate_message_id()
            
            self._log_debug('send_opportunistic', {
                'to': dest_hash[:16],
                'is_file': is_file,
                'msg_id': msg_id
            })
            
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                return False, "Destinazione sconosciuta", None
            
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
                file_size = 0
            
            message.desired_method = self._determine_desired_method(dest_hash)
            
            if self.config.get("try_propagation_on_fail", True):
                message.try_propagation_on_fail = True
            
            if self.config.get("propagation_stamp_cost", 16) > 0:
                message.stamp_cost = self.config["propagation_stamp_cost"]
            
            if self._is_trusted(dest_hash):
                message.include_ticket = True
            
            prop_node = self.config.get("propagation_node")
            if prop_node:
                prop_bytes = bytes.fromhex(self._clean_hash(prop_node))
                self.client.router.set_outbound_propagation_node(prop_bytes)
                msg_data['propagation_node'] = prop_node
            
            with self.messages_lock:
                exists = False
                for m in self.messages_buffer:
                    if m.get('message_id') == msg_id:
                        exists = True
                        break
                
                if not exists:
                    self.messages_buffer.append(msg_data)
                    if len(self.messages_buffer) > 200:
                        self.messages_buffer[:] = self.messages_buffer[-200:]
            
            with self._processed_lock:
                self._processed_messages.add(msg_id)
            
            if is_file:
                self._register_file_callbacks(
                    message, dest_hash, file_path, file_size, msg_data, msg_id
                )
            else:
                self._register_text_callbacks(
                    message, dest_hash, content, msg_data, msg_id
                )
            
            self.client.router.handle_outbound(message)
            
            method_names = {
                LXMF.LXMessage.DIRECT: "DIRECT",
                LXMF.LXMessage.OPPORTUNISTIC: "OPPORTUNISTIC",
                LXMF.LXMessage.PROPAGATED: "PROPAGATED"
            }
            method_name = method_names.get(message.desired_method, "UNKNOWN")
            
            print(f"📤 Messaggio inviato in modalità {method_name} per {dest_hash[:16]}")
            return True, f"Messaggio inviato in modalità {method_name}", msg_data
            
        except Exception as e:
            self._log_debug('send_opportunistic_error', {'error': str(e)})
            import traceback
            traceback.print_exc()
            return False, str(e), None
    
    def _send_propagated(self, dest_hash, content, is_file=False, file_path=None,
                         description="", audio_codec="opus", audio_mode=None):
        """Invia messaggio in modalità PROPAGATED (sempre via propagation node)"""
        try:
            prop_node = self.config.get("propagation_node")
            if not prop_node:
                return False, "Nessun propagation node configurato", None
            
            msg_id = self._generate_message_id()
            
            self._log_debug('send_propagated', {
                'to': dest_hash[:16],
                'propagation_node': prop_node[:16],
                'is_file': is_file,
                'msg_id': msg_id
            })
            
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                dest_bytes = bytes.fromhex(self._clean_hash(dest_hash)[:32])
                RNS.Transport.request_path(dest_bytes)
                time.sleep(2)
                dest_identity = RNS.Identity.recall(dest_bytes)
                if not dest_identity:
                    return False, "Destinatario sconosciuto", None
            
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
                file_size = 0
                msg_data['total_size'] = 0
            
            prop_stamp_cost = self.config.get("propagation_stamp_cost", 16)
            if prop_stamp_cost > 0:
                message.stamp_cost = prop_stamp_cost
            
            message.desired_method = LXMF.LXMessage.PROPAGATED
            message.try_propagation_on_fail = False
            
            if self._is_trusted(dest_hash):
                message.include_ticket = True
            
            prop_bytes = bytes.fromhex(self._clean_hash(prop_node))
            self.client.router.set_outbound_propagation_node(prop_bytes)
            
            msg_data['propagation_mode'] = 'forced'
            msg_data['propagation_node'] = prop_node
            msg_data['stamp_cost'] = prop_stamp_cost
            
            with self.messages_lock:
                exists = False
                for m in self.messages_buffer:
                    if m.get('message_id') == msg_id:
                        exists = True
                        break
                
                if not exists:
                    self.messages_buffer.append(msg_data)
                    if len(self.messages_buffer) > 200:
                        self.messages_buffer[:] = self.messages_buffer[-200:]
            
            with self._processed_lock:
                self._processed_messages.add(msg_id)
            
            if is_file:
                self._register_file_callbacks(
                    message, dest_hash, file_path, file_size, msg_data, msg_id
                )
            else:
                self._register_text_callbacks(
                    message, dest_hash, content, msg_data, msg_id
                )
            
            self.client.router.handle_outbound(message)
            
            print(f"📤 Messaggio inviato via propagation node {prop_node[:16]} per {dest_hash[:16]} (stamp cost: {prop_stamp_cost})")
            return True, f"Messaggio inviato in modalità PROPAGATED via {prop_node[:16]}", msg_data
            
        except Exception as e:
            self._log_debug('send_propagated_error', {'error': str(e)})
            import traceback
            traceback.print_exc()
            return False, str(e), None
    
    def _send_auto(self, dest_hash, content, is_file=False, file_path=None,
                   description="", audio_codec="opus", audio_mode=None):
        """Invia messaggio in modalità AUTO (try_propagation_on_fail=True)"""
        try:
            msg_id = self._generate_message_id()
            
            self._log_debug('send_auto', {
                'to': dest_hash[:16],
                'is_file': is_file,
                'msg_id': msg_id
            })
            
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                dest_bytes = bytes.fromhex(self._clean_hash(dest_hash)[:32])
                RNS.Transport.request_path(dest_bytes)
                time.sleep(2)
                dest_identity = RNS.Identity.recall(dest_bytes)
                if not dest_identity:
                    return False, "Destinatario sconosciuto", None
            
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
                file_size = 0
                msg_data['total_size'] = 0
            
            message.desired_method = LXMF.LXMessage.DIRECT
            message.try_propagation_on_fail = True
            
            prop_stamp_cost = self.config.get("propagation_stamp_cost", 16)
            if prop_stamp_cost > 0:
                message.stamp_cost = prop_stamp_cost
            
            if self._is_trusted(dest_hash):
                message.include_ticket = True
            
            prop_node = self.config.get("propagation_node")
            if prop_node:
                prop_bytes = bytes.fromhex(self._clean_hash(prop_node))
                self.client.router.set_outbound_propagation_node(prop_bytes)
                msg_data['propagation_node'] = prop_node
            
            msg_data['propagation_mode'] = 'auto'
            
            with self.messages_lock:
                exists = False
                for m in self.messages_buffer:
                    if m.get('message_id') == msg_id:
                        exists = True
                        break
                
                if not exists:
                    self.messages_buffer.append(msg_data)
                    if len(self.messages_buffer) > 200:
                        self.messages_buffer[:] = self.messages_buffer[-200:]
            
            with self._processed_lock:
                self._processed_messages.add(msg_id)
            
            if is_file:
                self._register_file_callbacks(
                    message, dest_hash, file_path, file_size, msg_data, msg_id
                )
            else:
                self._register_text_callbacks(
                    message, dest_hash, content, msg_data, msg_id
                )
            
            self.client.router.handle_outbound(message)
            
            mode_str = f"con fallback a propagation node {prop_node[:16]}" if prop_node else "senza propagation node"
            print(f"📤 Messaggio inviato in modalità AUTO {mode_str} per {dest_hash[:16]}")
            return True, f"Messaggio inviato in modalità AUTO {mode_str}", msg_data
            
        except Exception as e:
            self._log_debug('send_auto_error', {'error': str(e)})
            import traceback
            traceback.print_exc()
            return False, str(e), None
    
    def _send_hybrid(self, dest_hash, content, is_file=False, file_path=None,
                     description="", audio_codec="opus", audio_mode=None):
        """Modalità ibrida: diretto con fallback a propagation dopo max_retries"""
        try:
            max_retries = self.config.get("max_retries", 3)
            prop_node = self.config.get("propagation_node")
            
            if not prop_node:
                print("⚠️ Nessun propagation node configurato per modalità hybrid, uso DIRECT")
                return self._send_direct(dest_hash, content, is_file, file_path,
                                         description, audio_codec, audio_mode)
            
            hybrid_id = f"hybrid_{int(time.time())}_{os.urandom(4).hex()}"
            msg_id = self._generate_message_id()
            
            self._log_debug('send_hybrid_start', {
                'to': dest_hash[:16],
                'is_file': is_file,
                'max_retries': max_retries,
                'hybrid_id': hybrid_id,
                'msg_id': msg_id
            })
            
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                dest_bytes = bytes.fromhex(self._clean_hash(dest_hash)[:32])
                RNS.Transport.request_path(dest_bytes)
                time.sleep(2)
                dest_identity = RNS.Identity.recall(dest_bytes)
                if not dest_identity:
                    return False, "Destinatario sconosciuto", None
            
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
                file_size = 0
            
            message.desired_method = LXMF.LXMessage.DIRECT
            message.try_propagation_on_fail = True
            
            prop_stamp_cost = self.config.get("propagation_stamp_cost", 16)
            if prop_stamp_cost > 0:
                message.stamp_cost = prop_stamp_cost
            
            if self._is_trusted(dest_hash):
                message.include_ticket = True
            
            prop_bytes = bytes.fromhex(self._clean_hash(prop_node))
            self.client.router.set_outbound_propagation_node(prop_bytes)
            
            msg_data['hybrid_id'] = hybrid_id
            msg_data['propagation_node'] = prop_node
            msg_data['original_dest'] = dest_hash
            msg_data['is_file'] = is_file
            msg_data['total_size'] = file_size
            if is_file:
                msg_data['file_path'] = file_path
                msg_data['description'] = description
                msg_data['audio_codec'] = audio_codec
                msg_data['audio_mode'] = audio_mode
            
            with self.messages_lock:
                exists = False
                for m in self.messages_buffer:
                    if m.get('message_id') == msg_id:
                        exists = True
                        break
                
                if not exists:
                    self.messages_buffer.append(msg_data)
                    if len(self.messages_buffer) > 200:
                        self.messages_buffer[:] = self.messages_buffer[-200:]
            
            with self._processed_lock:
                self._processed_messages.add(msg_id)
            
            self._register_hybrid_callbacks(
                message, dest_hash, content if not is_file else None,
                file_path if is_file else None, file_size if is_file else 0,
                msg_data, hybrid_id, max_retries, is_file
            )
            
            self.client.router.handle_outbound(message)
            
            print(f"📤 Hybrid mode avviato per {dest_hash[:16]} (max {max_retries} tentativi LXMF)")
            return True, f"Hybrid mode avviato", msg_data
            
        except Exception as e:
            self._log_debug('send_hybrid_error', {'error': str(e)})
            import traceback
            traceback.print_exc()
            return False, str(e), None

    def _register_hybrid_callbacks(self, message, dest_hash, content, file_path, file_size, msg_data, hybrid_id, max_retries, is_file):
        """Registra callbacks per modalità hybrid"""
        
        def on_delivery(msg):
            print(f"✅ Hybrid: messaggio consegnato a {dest_hash[:16]}")
            
            original_timestamp = msg_data.get('timestamp', time.time())
            
            with self.messages_lock:
                for m in self.messages_buffer:
                    if m.get('message_id') == msg_data.get('message_id'):
                        m['status'] = 'delivered'
                        m['progress'] = 100
                        break
            
            if is_file:
                self._save_file_message(message, original_timestamp, dest_hash, 
                                       msg_data.get('attachments', []),
                                       msg_data.get('file_path'), msg_data.get('content', ''), 
                                       'delivered', file_size, msg_data.get('msg_type', 'file'), 
                                       msg_data.get('message_id'))
            else:
                self._save_text_message(message, dest_hash, content, original_timestamp, 
                                       'delivered', msg_data.get('message_id'))
            
            with self._processed_lock:
                if msg_data.get('message_id') in self._processed_messages:
                    self._processed_messages.remove(msg_data.get('message_id'))
        
        def on_failed(msg):
            attempts = getattr(message, 'delivery_attempts', 0)
            print(f"⚠️ Hybrid tentativo {attempts}/{max_retries} fallito per {dest_hash[:16]}")
            
            original_timestamp = msg_data.get('timestamp', time.time())
            
            with self.messages_lock:
                for m in self.messages_buffer:
                    if m.get('message_id') == msg_data.get('message_id'):
                        m['status'] = f'failed_attempt_{attempts}'
                        m['delivery_attempts'] = attempts
                        break
            
            if is_file:
                self._save_file_message(message, original_timestamp, dest_hash,
                                       msg_data.get('attachments', []),
                                       msg_data.get('file_path'), msg_data.get('content', ''),
                                       'failed', file_size, msg_data.get('msg_type', 'file'),
                                       msg_data.get('message_id'))
            else:
                self._save_text_message(message, dest_hash, content, original_timestamp,
                                       'failed', msg_data.get('message_id'))
            
            if attempts >= max_retries:
                print(f"⚠️ Hybrid: {max_retries} tentativi falliti, LXMF passerà a propagation node")
                with self.messages_lock:
                    for m in self.messages_buffer:
                        if m.get('hybrid_id') == hybrid_id:
                            m['escalated_to_propagation'] = True
            else:
                print(f"ℹ️ LXMF riproverà automaticamente (tentativo {attempts+1}/{max_retries})")
            
            with self._processed_lock:
                if msg_data.get('message_id') in self._processed_messages:
                    self._processed_messages.remove(msg_data.get('message_id'))
        
        message.register_delivery_callback(on_delivery)
        message.register_failed_callback(on_failed)
        
        if is_file:
            self._start_progress_monitor(message, dest_hash, file_path, file_size, msg_data, msg_data.get('message_id'))
    
    # ========== METODI PER PREPARAZIONE MESSAGGI ==========
    
    def _prepare_text_message(self, dest, dest_hash, content, msg_id):
        """Prepara messaggio di testo"""
        message = LXMF.LXMessage(
            destination=dest,
            source=self.client.destination,
            content=content,
            title=""
        )
        
        msg_data = {
            'timestamp': time.time(),
            'from': self.client.get_identity_info()['delivery_hash'],
            'to': dest_hash,
            'content': content,
            'direction': 'outgoing',
            'status': 'sending',
            'progress': 0,
            'total_size': 0,
            'rssi': None,
            'snr': None,
            'q': None,
            'attachments': [],
            'msg_type': 'text',
            'lxmf_file': None,
            'message_id': msg_id,
            'transfer_start': time.time()
        }
        
        return message, msg_data
    
    def _prepare_file_message(self, dest, dest_hash, file_path, description,
                              audio_codec, audio_mode, msg_id):
        """Prepara messaggio con file"""
        file_name = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        
        with open(file_path, 'rb') as f:
            file_bytes = f.read()

        if file_name.endswith(('.c2_3', '.c2_4', '.c2_8', '.c2_9')):
            mime = 'audio/codec2'
        else:
            mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

        fields = {}
        attachments = []
        
        if mime.startswith('image/'):
            ext = file_name.split('.')[-1].lower()
            if ext in ['jpg', 'jpeg']:
                ext = 'jpg'
            fields[LXMF.FIELD_IMAGE] = [ext, file_bytes]
            icon = "🖼️"
            msg_type = "image"
            attachments = [{
                'type': 'image',
                'name': file_name,
                'size': file_size,
                'mime': mime
            }]
            
        elif mime.startswith('audio/'):
            audio_mode_map = {
                'c2_450pwb': 1, 'c2_450': 2, 'c2_700': 3,
                'c2_1200': 4, 'c2_1300': 5, 'c2_1400': 6,
                'c2_1600': 7, 'c2_2400': 8, 'c2_3200': 9,
                'opus': 16,
            }
            
            if audio_mode is not None:
                audio_mode = int(audio_mode)
            else:
                audio_mode = audio_mode_map.get(audio_codec, 16)
            
            original_bytes = file_bytes
            original_size = len(file_bytes)
            
            if audio_mode < 16 and CODEC2_AVAILABLE:
                is_opus = file_name.endswith('.opus') or 'opus' in mime
                if is_opus and file_size > 0:
                    print(f"🔄 Conversione Opus ({file_size} bytes) -> Codec2 mode {audio_mode}...")
                    c2_bytes = self._convert_opus_to_codec2(file_bytes, audio_mode)
                    if c2_bytes:
                        file_bytes = c2_bytes
                        file_size = len(c2_bytes)
                        print(f"✅ Conversione riuscita: {original_size} bytes -> {file_size} bytes")
            
            fields[LXMF.FIELD_AUDIO] = [audio_mode, file_bytes]
            
            if audio_mode >= 16:
                icon = "🎵"
                msg_type = 'audio_opus'
                ext = 'opus'
            else:
                icon = "🎤"
                msg_type = f'audio_codec2_{audio_mode}'
                ext = f'c2_{audio_mode}'
            
            attachments = [{
                'type': 'audio',
                'name': file_name,
                'size': file_size,
                'mime': mime,
                'audio_codec': audio_codec,
                'audio_mode': audio_mode
            }]
            
        else:
            fields[LXMF.FIELD_FILE_ATTACHMENTS] = [[file_name, file_bytes]]
            icon = "📎"
            msg_type = "file"
            attachments = [{
                'type': 'file',
                'name': file_name,
                'size': file_size,
                'mime': mime
            }]
        
        if description:
            fields['description'] = description
        
        content = f"{icon} {file_name} ({self._format_size(file_size)})"
        
        message = LXMF.LXMessage(
            destination=dest,
            source=self.client.destination,
            content=content,
            title="",
            fields=fields
        )
        
        local_filename = f"{msg_type}_{int(time.time())}.{ext}"
        local_filepath = os.path.join(self.downloads_dir, local_filename)
        
        with open(local_filepath, 'wb') as f:
            f.write(file_bytes)
        
        attachments[0]['saved_as'] = local_filename
        
        msg_data = {
            'timestamp': time.time(),
            'from': self.client.get_identity_info()['delivery_hash'],
            'to': dest_hash,
            'content': content,
            'direction': 'outgoing',
            'status': 'sending',
            'progress': 0,
            'speed': 0,
            'elapsed': 0,
            'total_size': file_size,
            'rssi': None,
            'snr': None,
            'q': None,
            'attachments': attachments,
            'file_path': local_filename,
            'msg_type': msg_type,
            'is_receiving': False,
            'message_id': msg_id,
            'transfer_start': time.time(),
            'bytes_sent': 0
        }
        
        return message, msg_data, file_size
    
    # ========== METODI PER CALLBACKS ==========
    
    def _register_text_callbacks(self, message, dest_hash, content, msg_data, msg_id):
        """Registra callbacks per messaggi di testo"""
        
        def on_delivery(msg):
            self._on_text_delivery(msg, message, dest_hash, content, msg_data, msg_id)
        
        def on_failed(msg):
            self._on_text_failed(msg, message, dest_hash, content, msg_data, msg_id)
        
        message.register_delivery_callback(on_delivery)
        message.register_failed_callback(on_failed)
    
    def _on_text_delivery(self, msg, message, dest_hash, content, msg_data, msg_id):
        """Callback per consegna testo riuscita"""
        print(f"✅ Messaggio consegnato a {dest_hash[:16]}")
        
        rssi = getattr(msg, 'rssi', None)
        snr = getattr(msg, 'snr', None)
        q = getattr(msg, 'q', None)
        
        original_timestamp = msg_data.get('timestamp', time.time())
        
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('message_id') == msg_id:
                    m['status'] = 'delivered'
                    m['progress'] = 100
                    m['rssi'] = rssi
                    m['snr'] = snr
                    m['q'] = q
                    if hasattr(message, 'packed') and message.packed:
                        m['total_size'] = len(message.packed)
                    break
        
        self._save_text_message(message, dest_hash, content, original_timestamp, 'delivered', msg_id, rssi, snr, q)
        
        with self._processed_lock:
            if msg_id in self._processed_messages:
                self._processed_messages.remove(msg_id)
    
    def _on_text_failed(self, msg, message, dest_hash, content, msg_data, msg_id):
        """Callback per consegna testo fallita"""
        print(f"❌ Consegna fallita per {dest_hash[:16]}")
        
        original_timestamp = msg_data.get('timestamp', time.time())
        
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('message_id') == msg_id:
                    m['status'] = 'failed'
                    break
        
        self._save_text_message(message, dest_hash, content, original_timestamp, 'failed', msg_id)
        
        with self._processed_lock:
            if msg_id in self._processed_messages:
                self._processed_messages.remove(msg_id)
    
    def _register_file_callbacks(self, message, dest_hash, file_path, file_size, msg_data, msg_id):
        """Registra callbacks per file"""
        
        def on_delivery(msg):
            self._on_file_delivery(msg, message, dest_hash, file_path, file_size, msg_data, msg_id)
        
        def on_failed(msg):
            self._on_file_failed(msg, message, dest_hash, file_path, file_size, msg_data, msg_id)
        
        message.register_delivery_callback(on_delivery)
        message.register_failed_callback(on_failed)
        
        self._start_progress_monitor(message, dest_hash, file_path, file_size, msg_data, msg_id)
    
    def _on_file_delivery(self, msg, message, dest_hash, file_path, file_size, msg_data, msg_id):
        """Callback per consegna file riuscita"""
        elapsed = time.time() - msg_data.get('transfer_start', time.time())
        avg_speed = file_size / elapsed if elapsed > 0 else 0
        
        rssi = getattr(msg, 'rssi', None)
        snr = getattr(msg, 'snr', None)
        q = getattr(msg, 'q', None)
        
        self._log_debug('file_delivered', {
            'to': dest_hash[:16],
            'file': os.path.basename(file_path),
            'size': file_size,
            'time': f"{elapsed:.1f}s",
            'speed': self._format_speed(avg_speed)
        })
        
        original_timestamp = msg_data.get('timestamp', time.time())
        
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('message_id') == msg_id:
                    m['status'] = 'delivered'
                    m['progress'] = 100
                    m['speed'] = avg_speed
                    m['elapsed'] = elapsed
                    m['rssi'] = rssi
                    m['snr'] = snr
                    m['q'] = q
                    m['bytes_sent'] = file_size
                    m['delivery_time'] = time.time()
                    break
        
        self._save_file_message(message, original_timestamp, dest_hash, msg_data.get('attachments', []),
                               msg_data.get('file_path'), msg_data.get('content', ''), 
                               'delivered', file_size, msg_data.get('msg_type', 'file'), msg_id,
                               rssi, snr, q, elapsed, avg_speed)
        
        print(f"✅ File consegnato a {dest_hash[:16]}: {os.path.basename(file_path)}")
        print(f"   ⏱️ {elapsed:.1f}s | ⚡ {self._format_speed(avg_speed)} | 📡 RSSI:{rssi} SNR:{snr}")
        
        with self._processed_lock:
            if msg_id in self._processed_messages:
                self._processed_messages.remove(msg_id)
    
    def _on_file_failed(self, msg, message, dest_hash, file_path, file_size, msg_data, msg_id):
        """Callback per consegna file fallita"""
        print(f"❌ Consegna file fallita per {dest_hash[:16]}: {os.path.basename(file_path)}")
        
        original_timestamp = msg_data.get('timestamp', time.time())
        
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('message_id') == msg_id:
                    m['status'] = 'failed'
                    break
        
        self._save_file_message(message, original_timestamp, dest_hash, msg_data.get('attachments', []),
                               msg_data.get('file_path'), msg_data.get('content', ''), 
                               'failed', file_size, msg_data.get('msg_type', 'file'), msg_id)
        
        with self._processed_lock:
            if msg_id in self._processed_messages:
                self._processed_messages.remove(msg_id)
    
    # ========== MONITORAGGIO PROGRESSO ==========
    
    def _start_progress_monitor(self, message, dest_hash, file_path, file_size, msg_data, msg_id):
        """Avvia monitoraggio progresso in thread separato"""
        
        def monitor_progress():
            last_progress = 0
            last_bytes = 0
            last_time = time.time()
            update_count = 0
            start_time = msg_data.get('transfer_start', time.time())
            
            while True:
                time.sleep(0.5)
                try:
                    if hasattr(message, 'progress'):
                        current_progress = message.progress * 100
                        current_bytes = file_size * (current_progress / 100)
                        current_time = time.time()
                        
                        elapsed_instant = current_time - last_time
                        if elapsed_instant > 0 and current_bytes > last_bytes:
                            instant_speed = (current_bytes - last_bytes) / elapsed_instant
                        else:
                            instant_speed = 0
                        
                        elapsed_total = current_time - start_time
                        avg_speed = current_bytes / elapsed_total if elapsed_total > 0 else 0
                        
                        if abs(current_progress - last_progress) > 0.5 or update_count % 10 == 0:
                            last_progress = current_progress
                            last_bytes = current_bytes
                            last_time = current_time
                            update_count += 1
                            
                            with self.messages_lock:
                                for m in self.messages_buffer:
                                    if m.get('message_id') == msg_id:
                                        m['progress'] = current_progress
                                        m['speed'] = instant_speed
                                        m['avg_speed'] = avg_speed
                                        m['elapsed'] = elapsed_total
                                        m['bytes_sent'] = current_bytes
                                        m['eta'] = (file_size - current_bytes) / instant_speed if instant_speed > 0 else 0
                                        m['last_update'] = current_time
                                        break
                            
                            if int(current_progress) % 10 == 0 and int(current_progress) > 0:
                                print(f"📊 Progresso: {current_progress:.1f}% - "
                                      f"⚡ {self._format_speed(instant_speed)} - "
                                      f"📈 Media {self._format_speed(avg_speed)}")
                    
                    if message.state in [LXMF.LXMessage.DELIVERED, LXMF.LXMessage.FAILED]:
                        break
                        
                except Exception as e:
                    print(f"Errore monitor progresso: {e}")
                    break
        
        threading.Thread(target=monitor_progress, daemon=True).start()
    
    # ========== SALVATAGGIO MESSAGGI ==========
    
    def _save_text_message(self, message, dest_hash, content, timestamp, status, msg_id, rssi=None, snr=None, q=None):
        """Salva messaggio di testo come file .lxb"""
        peer_dir = os.path.join(self.conversations_dir, dest_hash)
        os.makedirs(peer_dir, exist_ok=True)
        
        msg_id_short = msg_id[:8] if msg_id and len(msg_id) >= 8 else (msg_id or 'unknown')
        
        filename = f"{int(timestamp)}_{msg_id_short}{'_failed' if 'failed' in status else ''}.lxb"
        filepath = os.path.join(peer_dir, filename)
        
        if os.path.exists(filepath):
            filename = f"{int(timestamp)}_{msg_id_short}_{os.urandom(2).hex()}.lxb"
            filepath = os.path.join(peer_dir, filename)
        
        packed_bytes = message.packed if hasattr(message, 'packed') else b''
        
        container = {
            'lxmf_bytes': packed_bytes,
            'timestamp': timestamp,
            'content': content,
            'direction': 'outgoing',
            'status': status,
            'total_size': len(packed_bytes),
            'from_hash': self.client.get_identity_info()['delivery_hash'],
            'to_hash': dest_hash,
            'display_name': self.client.identity_name,
            'lxmf_file': filename,
            'rssi': rssi,
            'snr': snr,
            'q': q,
            'message_id': msg_id,
            'progress': 100 if 'delivered' in status else 0,
            'raw_hex': packed_bytes.hex() if packed_bytes else None,
            'raw_ascii': ''.join(chr(b) if 32 <= b < 127 else '.' for b in packed_bytes) if packed_bytes else None,
            'raw_size': len(packed_bytes) if packed_bytes else 0
        }
        
        with open(filepath, 'wb') as f:
            f.write(msgpack.packb(container))
        
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('message_id') == msg_id:
                    m['lxmf_file'] = filename
                    m['total_size'] = len(packed_bytes)
                    break
    
    def _save_file_message(self, message, timestamp, dest_hash, attachments, 
                           local_filename, content, status, file_size, msg_type, msg_id,
                           rssi=None, snr=None, q=None, elapsed=None, speed=None):
        """Salva messaggio file come .lxb"""
        peer_dir = os.path.join(self.conversations_dir, dest_hash)
        os.makedirs(peer_dir, exist_ok=True)
        
        msg_id_short = msg_id[:8] if msg_id and len(msg_id) >= 8 else (msg_id or 'unknown')
        
        filename = f"{int(timestamp)}_{msg_id_short}{'_failed' if 'failed' in status else ''}.lxb"
        filepath = os.path.join(peer_dir, filename)
        
        if os.path.exists(filepath):
            filename = f"{int(timestamp)}_{msg_id_short}_{os.urandom(2).hex()}.lxb"
            filepath = os.path.join(peer_dir, filename)
        
        packed_bytes = message.packed if hasattr(message, 'packed') else b''
        
        container = {
            'lxmf_bytes': packed_bytes,
            'timestamp': timestamp,
            'rssi': rssi,
            'snr': snr,
            'q': q,
            'attachments': attachments,
            'file_path': local_filename,
            'content': content,
            'direction': 'outgoing',
            'status': status,
            'total_size': file_size,
            'msg_type': msg_type,
            'message_id': msg_id,
            'progress': 100 if 'delivered' in status else 0,
            'elapsed': elapsed,
            'speed': speed,
            'bytes_sent': file_size if 'delivered' in status else None,
            'raw_hex': packed_bytes.hex() if packed_bytes else None,
            'raw_ascii': ''.join(chr(b) if 32 <= b < 127 else '.' for b in packed_bytes) if packed_bytes else None,
            'raw_size': len(packed_bytes) if packed_bytes else 0,
            'from_hash': self.client.get_identity_info()['delivery_hash'],
            'to_hash': dest_hash,
            'display_name': self.client.identity_name,
            'lxmf_file': filename
        }
        
        with open(filepath, 'wb') as f:
            f.write(msgpack.packb(container))
        
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('message_id') == msg_id:
                    m['lxmf_file'] = filename
                    m['total_size'] = len(packed_bytes)
                    break
    
    def save_received_message(self, message, from_hash, to_hash, content, 
                              attachments=None, telemetry=None, battery=None,
                              location=None, appearance=None, rssi=None, 
                              snr=None, q=None, msg_type='text', raw_hex=None,
                              raw_ascii=None, raw_size=None, command_type=None,
                              command_data=None, from_display=None):
        """Salva un messaggio ricevuto con tutti i metadati"""
        peer_hash = self._clean_hash(from_hash)
        peer_dir = os.path.join(self.conversations_dir, peer_hash)
        os.makedirs(peer_dir, exist_ok=True)
        
        msg_id_part = ""
        if hasattr(message, 'message_id') and message.message_id:
            clean_id = self._clean_hash(message.message_id)
            if clean_id:
                msg_id_part = f"_{clean_id[:8]}"
        
        filename = f"{int(time.time())}{msg_id_part}.lxb"
        filepath = os.path.join(peer_dir, filename)
        
        if os.path.exists(filepath):
            filename = f"{int(time.time())}_{os.urandom(2).hex()}.lxb"
            filepath = os.path.join(peer_dir, filename)
        
        packed_bytes = message.packed if hasattr(message, 'packed') else b''
        
        container = {
            'lxmf_bytes': packed_bytes,
            'timestamp': time.time(),
            'from_hash': from_hash,
            'to_hash': to_hash,
            'from_display': from_display,
            'content': content,
            'direction': 'incoming',
            'status': 'delivered',
            'rssi': rssi,
            'snr': snr,
            'q': q,
            'attachments': attachments or [],
            'telemetry': telemetry,
            'battery': battery,
            'location': location,
            'appearance': appearance,
            'msg_type': msg_type,
            'raw_hex': raw_hex or (packed_bytes.hex() if packed_bytes else None),
            'raw_ascii': raw_ascii or (''.join(chr(b) if 32 <= b < 127 else '.' for b in packed_bytes) if packed_bytes else None),
            'raw_size': raw_size or (len(packed_bytes) if packed_bytes else 0),
            'command_type': command_type,
            'command_data': command_data,
            'total_size': len(packed_bytes) if packed_bytes else 0,
            'lxmf_file': filename
        }
        
        with open(filepath, 'wb') as f:
            f.write(msgpack.packb(container))
        
        container_for_buffer = self._bytes_to_json_compatible(container)
        
        with self.messages_lock:
            exists = False
            for m in self.messages_buffer:
                if (m.get('timestamp') == container_for_buffer['timestamp'] and 
                    m.get('from') == from_hash and
                    m.get('content') == content):
                    exists = True
                    break
            
            if not exists:
                self.messages_buffer.append(container_for_buffer)
                if len(self.messages_buffer) > 200:
                    self.messages_buffer[:] = self.messages_buffer[-200:]
        
        print(f"💾 Messaggio ricevuto salvato: {filename}")
        return filename
    
    # ========== METODI DI UTILITY ==========
    
    def _generate_message_id(self):
        """Genera ID univoco per messaggio"""
        return RNS.prettyhexrep(RNS.Identity.full_hash(
            str(time.time()).encode() + os.urandom(16)
        ))[:16]
    
    def _clean_hash(self, hash_str):
        """Pulisce un hash - rimuove TUTTI i caratteri non esadecimali"""
        if not hash_str:
            return ''
        
        if isinstance(hash_str, bytes):
            return hash_str.hex()
        
        hash_str = str(hash_str)
        return re.sub(r'[^a-fA-F0-9]', '', hash_str).lower()
    
    def _bytes_to_json_compatible(self, obj):
        """Converte bytes in formato JSON compatibile"""
        if obj is None:
            return None
        elif isinstance(obj, bytes):
            return obj.hex()
        elif isinstance(obj, (str, int, float, bool)):
            return obj
        elif isinstance(obj, dict):
            new_dict = {}
            for k, v in obj.items():
                key = k.hex() if isinstance(k, bytes) else k
                new_dict[key] = self._bytes_to_json_compatible(v)
            return new_dict
        elif isinstance(obj, (list, tuple)):
            return [self._bytes_to_json_compatible(item) for item in obj]
        else:
            try:
                json.dumps(obj)
                return obj
            except:
                return str(obj)
    
    def _format_size(self, size):
        """Formatta size"""
        if size is None or size == 0:
            return '0 B'
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    
    def _format_speed(self, bytes_per_sec):
        """Formatta velocità"""
        if bytes_per_sec is None or bytes_per_sec <= 0:
            return ''
        bits_per_sec = bytes_per_sec * 8
        if bits_per_sec < 1000:
            return f"{bits_per_sec:.1f} b/s"
        elif bits_per_sec < 1000000:
            return f"{bits_per_sec/1000:.1f} Kb/s"
        else:
            return f"{bits_per_sec/1000000:.1f} Mb/s"
    
    def _convert_opus_to_codec2(self, opus_bytes, target_mode):
        """Converte audio Opus in Codec2"""
        temp_opus_path = None
        temp_wav_path = None
        
        try:
            print(f"🎯 Conversione Opus ({len(opus_bytes)} bytes) -> Codec2 mode {target_mode}")
            
            with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as temp_opus:
                temp_opus.write(opus_bytes)
                temp_opus_path = temp_opus.name
            print(f"📁 Temp Opus: {temp_opus_path}")
            
            temp_wav_path = temp_opus_path.replace('.opus', '.wav')
            
            cmd = [
                'ffmpeg', '-i', temp_opus_path,
                '-acodec', 'pcm_s16le',
                '-ac', '1',
                '-ar', '8000',
                '-f', 'wav',
                '-y', temp_wav_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"❌ FFMPEG ERROR: {result.stderr}")
                return None
            
            if not os.path.exists(temp_wav_path):
                print(f"❌ File WAV non creato")
                return None
            
            print(f"✅ WAV creato: {temp_wav_path}")
            
            with wave.open(temp_wav_path, 'rb') as wav:
                channels = wav.getnchannels()
                framerate = wav.getframerate()
                sampwidth = wav.getsampwidth()
                
                if channels != 1 or framerate != 8000 or sampwidth != 2:
                    print(f"❌ WAV non conforme: deve essere mono 8kHz 16-bit")
                    return None
                
                frames = wav.readframes(wav.getnframes())
                samples = np.frombuffer(frames, dtype=np.int16)
                print(f"📊 Samples: {len(samples)}")
            
            c2_bytes = encode_codec2(samples, target_mode)
            
            if c2_bytes:
                compression_ratio = (len(c2_bytes) / len(opus_bytes)) * 100
                print(f"✅ Codec2 encoded: {len(c2_bytes)} bytes ({compression_ratio:.1f}%)")
            else:
                print(f"❌ encode_codec2 ha restituito None")
            
            return c2_bytes
            
        except Exception as e:
            print(f"❌ ERRORE CONVERSIONE: {e}")
            import traceback
            traceback.print_exc()
            return None
        
        finally:
            try:
                if temp_opus_path and os.path.exists(temp_opus_path):
                    os.unlink(temp_opus_path)
            except:
                pass
            try:
                if temp_wav_path and os.path.exists(temp_wav_path):
                    os.unlink(temp_wav_path)
            except:
                pass
    
    def _log_debug(self, event_type, data):
        """Logga eventi di debug"""
        pass