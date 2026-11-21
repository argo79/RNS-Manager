# lxmf_message_sender.py
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
    "max_stamp_retries": 3
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
    """
    
    def __init__(self, lxmf_client, messages_lock, messages_buffer, conversations_dir, downloads_dir):
        """
        Args:
            lxmf_client: istanza del client LXMF
            messages_lock: lock per il buffer messaggi
            messages_buffer: buffer messaggi
            conversations_dir: directory conversazioni
            downloads_dir: directory downloads
        """
        self.client = lxmf_client
        self.messages_lock = messages_lock
        self.messages_buffer = messages_buffer
        self.conversations_dir = conversations_dir
        self.downloads_dir = downloads_dir
        self.config = load_config()
        
        # Inizializza propagation node se configurato
        if self.config.get("propagation_node"):
            self._set_propagation_node_internal(self.config["propagation_node"])
        
    # ========== METODI PUBBLICI PER CONFIGURAZIONE PROPAGATION ==========
    
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
    
    def configure_delivery_mode(self, mode, propagation_node=None):
        """
        Configura la modalità di consegna
        
        mode: 
            - "direct": solo diretto
            - "propagation": sempre via propagation node
            - "hybrid": diretto con fallback a propagation (dopo max_retries fallimenti)
            - "auto": lascia decidere a LXMF (try_propagation_on_fail)
        """
        valid_modes = ["direct", "propagation", "hybrid", "auto"]
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
    
    # ========== METODI PUBBLICI PER INVIO ==========
    
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
        mode = self.config.get("delivery_mode", "direct")
        
        if mode == "propagation":
            # Forza invio via propagation node
            return self._send_propagated(dest_hash, content, is_file, file_path,
                                         description, audio_codec, audio_mode)
        elif mode == "hybrid":
            # Prova diretto, poi propagation dopo fallimenti
            return self._send_hybrid(dest_hash, content, is_file, file_path,
                                     description, audio_codec, audio_mode)
        elif mode == "auto":
            # Lascia decidere a LXMF (try_propagation_on_fail=True)
            return self._send_auto(dest_hash, content, is_file, file_path,
                                   description, audio_codec, audio_mode)
        else:  # direct
            # Solo diretto
            return self._send_direct(dest_hash, content, is_file, file_path,
                                     description, audio_codec, audio_mode)
    
    # ========== METODI PRIVATI PER LE VARIE MODALITÀ ==========
    
    def _send_direct(self, dest_hash, content, is_file=False, file_path=None,
                     description="", audio_codec="opus", audio_mode=None):
        """
        Invia messaggio in modalità DIRECT (solo diretto)
        """
        try:
            # Prepara messaggio
            msg_id = self._generate_message_id()
            
            self._log_debug('send_direct', {
                'to': dest_hash[:16],
                'is_file': is_file,
                'msg_id': msg_id
            })
            
            # Ottieni identità destinatario
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                return False, "Destinazione sconosciuta", None
            
            # Crea destinazione
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            # Prepara messaggio
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
                file_size = len(message.packed) if hasattr(message, 'packed') else 0
            
            # Configura modalità DIRECT
            message.desired_method = LXMF.LXMessage.DIRECT
            message.try_propagation_on_fail = False
            
            # NON chiamare pack() - LXMF lo farà quando serve
            
            # Aggiungi al buffer
            with self.messages_lock:
                self.messages_buffer.append(msg_data)
                if len(self.messages_buffer) > 200:
                    self.messages_buffer[:] = self.messages_buffer[-200:]
            
            # Registra callbacks
            if is_file:
                self._register_file_callbacks(
                    message, dest_hash, file_path, file_size, msg_data, msg_id
                )
            else:
                self._register_text_callbacks(
                    message, dest_hash, content, msg_data, msg_id
                )
            
            # Invia
            self.client.router.handle_outbound(message)
            
            print(f"📤 Messaggio diretto inviato a {dest_hash[:16]}")
            return True, "Messaggio inviato in modalità DIRECT", msg_data
            
        except Exception as e:
            self._log_debug('send_direct_error', {'error': str(e)})
            import traceback
            traceback.print_exc()
            return False, str(e), None
    
    def _send_propagated(self, dest_hash, content, is_file=False, file_path=None,
                         description="", audio_codec="opus", audio_mode=None):
        """
        Invia messaggio in modalità PROPAGATED (sempre via propagation node)
        """
        try:
            # Verifica che ci sia un propagation node configurato
            prop_node = self.config.get("propagation_node")
            if not prop_node:
                return False, "Nessun propagation node configurato", None
            
            # Prepara messaggio
            msg_id = self._generate_message_id()
            
            self._log_debug('send_propagated', {
                'to': dest_hash[:16],
                'propagation_node': prop_node[:16],
                'is_file': is_file,
                'msg_id': msg_id
            })
            
            # Ottieni identità destinatario finale
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                # Richiedi path
                dest_bytes = bytes.fromhex(self._clean_hash(dest_hash)[:32])
                RNS.Transport.request_path(dest_bytes)
                time.sleep(2)
                dest_identity = RNS.Identity.recall(dest_bytes)
                if not dest_identity:
                    return False, "Destinatario sconosciuto", None
            
            # Crea destinazione per il destinatario FINALE
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            # Prepara messaggio
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
                file_size = len(message.packed) if hasattr(message, 'packed') else 0
            
            # 🔥 IMPOSTA STAMP COST PER PROPAGATION
            prop_stamp_cost = self.config.get("propagation_stamp_cost", 16)
            if prop_stamp_cost > 0:
                message.stamp_cost = prop_stamp_cost
            
            # 🔥 FORZA L'USO DEL PROPAGATION NODE
            message.desired_method = LXMF.LXMessage.PROPAGATED
            message.try_propagation_on_fail = False
            
            # Assicurati che il propagation node sia impostato nel router
            prop_bytes = bytes.fromhex(self._clean_hash(prop_node))
            self.client.router.set_outbound_propagation_node(prop_bytes)
            
            # NON chiamare pack() - LXMF lo farà quando serve (incluso per gli stamp)
            
            # Aggiungi al buffer (con flag speciale)
            msg_data['propagation_mode'] = 'forced'
            msg_data['propagation_node'] = prop_node
            msg_data['stamp_cost'] = prop_stamp_cost
            
            with self.messages_lock:
                self.messages_buffer.append(msg_data)
                if len(self.messages_buffer) > 200:
                    self.messages_buffer[:] = self.messages_buffer[-200:]
            
            # Registra callbacks
            if is_file:
                self._register_file_callbacks(
                    message, dest_hash, file_path, file_size, msg_data, msg_id
                )
            else:
                self._register_text_callbacks(
                    message, dest_hash, content, msg_data, msg_id
                )
            
            # Invia
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
        """
        Invia messaggio in modalità AUTO (try_propagation_on_fail=True)
        Lascia che LXMF decida se usare propagation node in caso di fallimento diretto
        """
        try:
            # Prepara messaggio
            msg_id = self._generate_message_id()
            
            self._log_debug('send_auto', {
                'to': dest_hash[:16],
                'is_file': is_file,
                'msg_id': msg_id
            })
            
            # Ottieni identità destinatario
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                # Richiedi path
                dest_bytes = bytes.fromhex(self._clean_hash(dest_hash)[:32])
                RNS.Transport.request_path(dest_bytes)
                time.sleep(2)
                dest_identity = RNS.Identity.recall(dest_bytes)
                if not dest_identity:
                    return False, "Destinatario sconosciuto", None
            
            # Crea destinazione
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            # Prepara messaggio
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
                file_size = len(message.packed) if hasattr(message, 'packed') else 0
            
            # 🔥 CONFIGURAZIONE AUTO: prova diretto, se fallisce usa propagation
            message.desired_method = LXMF.LXMessage.DIRECT
            message.try_propagation_on_fail = True
            
            # Imposta stamp cost per propagation (usato se cade in fallback)
            prop_stamp_cost = self.config.get("propagation_stamp_cost", 16)
            if prop_stamp_cost > 0:
                message.stamp_cost = prop_stamp_cost
            
            # Assicurati che il propagation node sia impostato (se configurato)
            prop_node = self.config.get("propagation_node")
            if prop_node:
                prop_bytes = bytes.fromhex(self._clean_hash(prop_node))
                self.client.router.set_outbound_propagation_node(prop_bytes)
                msg_data['propagation_node'] = prop_node
            
            # NON chiamare pack()
            
            # Aggiungi al buffer
            msg_data['propagation_mode'] = 'auto'
            
            with self.messages_lock:
                self.messages_buffer.append(msg_data)
                if len(self.messages_buffer) > 200:
                    self.messages_buffer[:] = self.messages_buffer[-200:]
            
            # Registra callbacks
            if is_file:
                self._register_file_callbacks(
                    message, dest_hash, file_path, file_size, msg_data, msg_id
                )
            else:
                self._register_text_callbacks(
                    message, dest_hash, content, msg_data, msg_id
                )
            
            # Invia
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
        """
        Modalità ibrida: prova DIRECT per max_retries volte, poi passa a PROPAGATED
        """
        max_retries = self.config.get("max_retries", 3)
        prop_node = self.config.get("propagation_node")
        
        if not prop_node:
            print("⚠️ Nessun propagation node configurato per modalità hybrid, uso DIRECT")
            return self._send_direct(dest_hash, content, is_file, file_path,
                                     description, audio_codec, audio_mode)
        
        # Genera ID univoco per questo tentativo ibrido
        hybrid_id = f"hybrid_{int(time.time())}_{os.urandom(4).hex()}"
        
        # Prepara messaggio
        msg_id = self._generate_message_id()
        
        self._log_debug('send_hybrid_start', {
            'to': dest_hash[:16],
            'is_file': is_file,
            'max_retries': max_retries,
            'hybrid_id': hybrid_id,
            'msg_id': msg_id
        })
        
        # Ottieni identità destinatario
        dest_identity = self._get_destination_identity(dest_hash)
        if not dest_identity:
            # Richiedi path
            dest_bytes = bytes.fromhex(self._clean_hash(dest_hash)[:32])
            RNS.Transport.request_path(dest_bytes)
            time.sleep(2)
            dest_identity = RNS.Identity.recall(dest_bytes)
            if not dest_identity:
                return False, "Destinatario sconosciuto", None
        
        # Crea destinazione
        dest = RNS.Destination(
            dest_identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            "lxmf",
            "delivery"
        )
        
        # Prepara messaggio
        if is_file:
            message, msg_data, file_size = self._prepare_file_message(
                dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
            )
        else:
            message, msg_data = self._prepare_text_message(
                dest, dest_hash, content, msg_id
            )
            file_size = len(message.packed) if hasattr(message, 'packed') else 0
        
        # Configurazione iniziale: DIRECT
        message.desired_method = LXMF.LXMessage.DIRECT
        message.try_propagation_on_fail = False
        
        # Imposta stamp cost per eventuale fallback
        prop_stamp_cost = self.config.get("propagation_stamp_cost", 16)
        if prop_stamp_cost > 0:
            message.stamp_cost = prop_stamp_cost
        
        # NON chiamare pack()
        
        # Aggiungi metadati per hybrid
        msg_data['hybrid_id'] = hybrid_id
        msg_data['hybrid_attempts'] = 0
        msg_data['hybrid_max_retries'] = max_retries
        msg_data['propagation_node'] = prop_node
        msg_data['original_dest'] = dest_hash
        msg_data['is_file'] = is_file
        if is_file:
            msg_data['file_path'] = file_path
            msg_data['description'] = description
            msg_data['audio_codec'] = audio_codec
            msg_data['audio_mode'] = audio_mode
        
        # Aggiungi al buffer
        with self.messages_lock:
            self.messages_buffer.append(msg_data)
            if len(self.messages_buffer) > 200:
                self.messages_buffer[:] = self.messages_buffer[-200:]
        
        # Registra callbacks con gestione hybrid
        if is_file:
            self._register_hybrid_file_callbacks(
                message, dest_hash, file_path, file_size, msg_data, hybrid_id, max_retries
            )
        else:
            self._register_hybrid_text_callbacks(
                message, dest_hash, content, msg_data, hybrid_id, max_retries
            )
        
        # Invia primo tentativo
        self.client.router.handle_outbound(message)
        
        print(f"📤 Tentativo hybrid 1/{max_retries} per {dest_hash[:16]}")
        return True, f"Tentativo hybrid 1/{max_retries} avviato", msg_data
    
    def _retry_hybrid_direct(self, hybrid_id, dest_hash, content, is_file, file_path,
                             description, audio_codec, audio_mode, attempt, max_retries):
        """Riprova invio diretto per modalità hybrid"""
        try:
            self._log_debug('hybrid_retry', {
                'to': dest_hash[:16],
                'attempt': attempt,
                'max': max_retries,
                'hybrid_id': hybrid_id
            })
            
            # Ottieni identità destinatario
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                print(f"❌ Tentativo {attempt}: destinatario sconosciuto")
                if attempt >= max_retries:
                    self._escalate_to_propagation(hybrid_id, dest_hash, content, is_file,
                                                  file_path, description, audio_codec, audio_mode)
                return
            
            # Crea destinazione
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            # Prepara messaggio
            msg_id = self._generate_message_id()
            
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
                file_size = len(message.packed) if hasattr(message, 'packed') else 0
            
            # Configura DIRECT
            message.desired_method = LXMF.LXMessage.DIRECT
            message.try_propagation_on_fail = False
            
            # Imposta stamp cost per eventuale fallback
            prop_stamp_cost = self.config.get("propagation_stamp_cost", 16)
            if prop_stamp_cost > 0:
                message.stamp_cost = prop_stamp_cost
            
            # NON chiamare pack()
            
            # Mantieni traccia hybrid
            msg_data['hybrid_id'] = hybrid_id
            msg_data['hybrid_attempts'] = attempt
            msg_data['hybrid_max_retries'] = max_retries
            msg_data['propagation_node'] = self.config.get("propagation_node")
            
            # Aggiungi al buffer
            with self.messages_lock:
                self.messages_buffer.append(msg_data)
                if len(self.messages_buffer) > 200:
                    self.messages_buffer[:] = self.messages_buffer[-200:]
            
            # Registra callbacks
            if is_file:
                self._register_hybrid_file_callbacks(
                    message, dest_hash, file_path, file_size, msg_data, hybrid_id, max_retries
                )
            else:
                self._register_hybrid_text_callbacks(
                    message, dest_hash, content, msg_data, hybrid_id, max_retries
                )
            
            # Invia
            self.client.router.handle_outbound(message)
            
            print(f"📤 Tentativo hybrid {attempt}/{max_retries} per {dest_hash[:16]}")
            
        except Exception as e:
            print(f"❌ Errore retry hybrid: {e}")
            if attempt >= max_retries:
                self._escalate_to_propagation(hybrid_id, dest_hash, content, is_file,
                                              file_path, description, audio_codec, audio_mode)
    
    def _escalate_to_propagation(self, hybrid_id, dest_hash, content, is_file, file_path,
                                 description, audio_codec, audio_mode):
        """Passa a PROPAGATED dopo fallimenti DIRECT"""
        print(f"⚠️ {self.config.get('max_retries')} tentativi falliti, passo a propagation node")
        
        try:
            prop_node = self.config.get("propagation_node")
            if not prop_node:
                print("❌ Nessun propagation node configurato")
                return
            
            # Ottieni identità destinatario finale
            dest_identity = self._get_destination_identity(dest_hash)
            if not dest_identity:
                dest_bytes = bytes.fromhex(self._clean_hash(dest_hash)[:32])
                RNS.Transport.request_path(dest_bytes)
                time.sleep(2)
                dest_identity = RNS.Identity.recall(dest_bytes)
                if not dest_identity:
                    print("❌ Destinatario sconosciuto anche per propagation")
                    return
            
            # Crea destinazione per destinatario FINALE
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                "lxmf",
                "delivery"
            )
            
            # Prepara messaggio
            msg_id = self._generate_message_id()
            
            if is_file:
                message, msg_data, file_size = self._prepare_file_message(
                    dest, dest_hash, file_path, description, audio_codec, audio_mode, msg_id
                )
            else:
                message, msg_data = self._prepare_text_message(
                    dest, dest_hash, content, msg_id
                )
            
            # 🔥 FORZA PROPAGATION
            message.desired_method = LXMF.LXMessage.PROPAGATED
            message.try_propagation_on_fail = False
            
            # Imposta stamp cost
            prop_stamp_cost = self.config.get("propagation_stamp_cost", 16)
            if prop_stamp_cost > 0:
                message.stamp_cost = prop_stamp_cost
            
            # Imposta propagation node
            prop_bytes = bytes.fromhex(self._clean_hash(prop_node))
            self.client.router.set_outbound_propagation_node(prop_bytes)
            
            # NON chiamare pack()
            
            # Aggiungi metadati
            msg_data['hybrid_id'] = hybrid_id
            msg_data['propagation_mode'] = 'escalated'
            msg_data['propagation_node'] = prop_node
            msg_data['hybrid_escalated'] = True
            msg_data['stamp_cost'] = prop_stamp_cost
            
            with self.messages_lock:
                self.messages_buffer.append(msg_data)
                if len(self.messages_buffer) > 200:
                    self.messages_buffer[:] = self.messages_buffer[-200:]
            
            # Registra callbacks normali
            if is_file:
                self._register_file_callbacks(
                    message, dest_hash, file_path, file_size, msg_data, msg_id
                )
            else:
                self._register_text_callbacks(
                    message, dest_hash, content, msg_data, msg_id
                )
            
            # Invia
            self.client.router.handle_outbound(message)
            
            print(f"📤 Messaggio escalato a propagation node {prop_node[:16]} per {dest_hash[:16]}")
            
        except Exception as e:
            print(f"❌ Errore escalation a propagation: {e}")
    
    # ========== METODI PER PREPARAZIONE MESSAGGI ==========
    
    def _prepare_text_message(self, dest, dest_hash, content, msg_id):
        """Prepara messaggio di testo"""
        message = LXMF.LXMessage(
            destination=dest,
            source=self.client.destination,
            content=content,
            title=""
        )
        # NON chiamare pack() qui
        
        msg_data = {
            'timestamp': time.time(),
            'from': self.client.get_identity_info()['delivery_hash'],
            'to': dest_hash,
            'content': content,
            'direction': 'outgoing',
            'status': 'sending',
            'progress': 0,
            'total_size': 0,  # Sarà aggiornato dopo il packing
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

        # Determina MIME type
        if file_name.endswith(('.c2_3', '.c2_4', '.c2_8', '.c2_9')):
            mime = 'audio/codec2'
        else:
            mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

        fields = {}
        attachments = []
        
        # Gestione in base al tipo
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
            # Mappa codec audio
            audio_mode_map = {
                'c2_450pwb': 1,
                'c2_450': 2,
                'c2_700': 3,
                'c2_1200': 4,
                'c2_1300': 5,
                'c2_1400': 6,
                'c2_1600': 7,
                'c2_2400': 8,
                'c2_3200': 9,
                'opus': 16,
            }
            
            if audio_mode is not None:
                audio_mode = int(audio_mode)
            else:
                audio_mode = audio_mode_map.get(audio_codec, 16)
            
            # Conversione Opus -> Codec2 se necessario
            original_bytes = file_bytes
            original_size = len(file_bytes)
            
            if audio_mode < 16 and CODEC2_AVAILABLE and audio_codec != f'c2_{audio_mode}':
                if file_size > 2000:
                    print(f"🔄 Conversione Opus ({file_size} bytes) -> Codec2 mode {audio_mode}...")
                    c2_bytes = self._convert_opus_to_codec2(file_bytes, audio_mode)
                    if c2_bytes:
                        file_bytes = c2_bytes
                        file_size = len(c2_bytes)
                        print(f"✅ Conversione riuscita: {original_size} bytes -> {file_size} bytes")
                    else:
                        print("⚠️ Conversione fallita, invio come Opus")
                        audio_mode = 16
                        audio_codec = 'opus'
            
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
        # NON chiamare pack() qui
        
        # Salva copia locale
        safe_name = ''.join(c for c in file_name if c.isalnum() or c in '._- ')
        local_filename = f"{msg_type}_{int(time.time())}.{ext}"
        local_filepath = os.path.join(self.downloads_dir, local_filename)
        
        with open(local_filepath, 'wb') as f:
            f.write(file_bytes)
        
        # Se è Codec2, crea anche WAV per player
        if msg_type.startswith('audio_codec2') and CODEC2_AVAILABLE:
            try:
                audio_mode = int(msg_type.split('_')[-1])
                pcm_data = decode_codec2(file_bytes, audio_mode)
                
                if pcm_data:
                    wav_filename = f"audio_{int(time.time())}_c2_{audio_mode}.wav"
                    wav_path = os.path.join(self.downloads_dir, wav_filename)
                    
                    if samples_to_wav(pcm_data, wav_path):
                        attachments[0]['converted'] = True
                        attachments[0]['wav_file'] = wav_filename
                        local_filename = wav_filename  # Per il player
            except Exception as e:
                print(f"❌ Errore creazione WAV: {e}")
        
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
        
        # Aggiorna buffer
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('message_id') == msg_id:
                    m['status'] = 'delivered'
                    m['progress'] = 100
                    m['rssi'] = rssi
                    m['snr'] = snr
                    m['q'] = q
                    # Aggiorna size dopo packing
                    if hasattr(message, 'packed') and message.packed:
                        m['total_size'] = len(message.packed)
                    break
        
        # Salva
        self._save_text_message(message, dest_hash, content, time.time(), 'delivered', msg_id, rssi, snr, q)
    
    def _on_text_failed(self, msg, message, dest_hash, content, msg_data, msg_id):
        """Callback per consegna testo fallita"""
        print(f"❌ Consegna fallita per {dest_hash[:16]}")
        
        # Aggiorna buffer
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('message_id') == msg_id:
                    m['status'] = 'failed'
                    break
        
        # Salva
        self._save_text_message(message, dest_hash, content, time.time(), 'failed', msg_id)
    
    def _register_file_callbacks(self, message, dest_hash, file_path, file_size, msg_data, msg_id):
        """Registra callbacks per file"""
        
        def on_delivery(msg):
            self._on_file_delivery(msg, message, dest_hash, file_path, file_size, msg_data, msg_id)
        
        def on_failed(msg):
            self._on_file_failed(msg, message, dest_hash, file_path, file_size, msg_data, msg_id)
        
        message.register_delivery_callback(on_delivery)
        message.register_failed_callback(on_failed)
        
        # Avvia monitor progresso
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
        
        # Aggiorna buffer
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
        
        # Salva
        self._save_file_message(message, time.time(), dest_hash, msg_data.get('attachments', []),
                               msg_data.get('file_path'), msg_data.get('content', ''), 
                               'delivered', file_size, msg_data.get('msg_type', 'file'), msg_id,
                               rssi, snr, q, elapsed, avg_speed)
        
        print(f"✅ File consegnato a {dest_hash[:16]}: {os.path.basename(file_path)}")
        print(f"   ⏱️ {elapsed:.1f}s | ⚡ {self._format_speed(avg_speed)} | 📡 RSSI:{rssi} SNR:{snr}")
    
    def _on_file_failed(self, msg, message, dest_hash, file_path, file_size, msg_data, msg_id):
        """Callback per consegna file fallita"""
        print(f"❌ Consegna file fallita per {dest_hash[:16]}: {os.path.basename(file_path)}")
        
        # Aggiorna buffer
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('message_id') == msg_id:
                    m['status'] = 'failed'
                    break
        
        # Salva
        self._save_file_message(message, time.time(), dest_hash, msg_data.get('attachments', []),
                               msg_data.get('file_path'), msg_data.get('content', ''), 
                               'failed', file_size, msg_data.get('msg_type', 'file'), msg_id)
    
    # ========== CALLBACKS PER MODALITÀ HYBRID ==========
    
    def _register_hybrid_text_callbacks(self, message, dest_hash, content, msg_data, hybrid_id, max_retries):
        """Registra callbacks per testo in modalità hybrid"""
        attempt = msg_data.get('hybrid_attempts', 0) + 1
        
        def on_delivery(msg):
            self._on_hybrid_text_delivery(msg, message, dest_hash, content, msg_data, hybrid_id)
        
        def on_failed(msg):
            self._on_hybrid_text_failed(msg, message, dest_hash, content, msg_data, hybrid_id, attempt, max_retries)
        
        message.register_delivery_callback(on_delivery)
        message.register_failed_callback(on_failed)
    
    def _register_hybrid_file_callbacks(self, message, dest_hash, file_path, file_size, msg_data, hybrid_id, max_retries):
        """Registra callbacks per file in modalità hybrid"""
        attempt = msg_data.get('hybrid_attempts', 0) + 1
        
        def on_delivery(msg):
            self._on_hybrid_file_delivery(msg, message, dest_hash, file_path, file_size, msg_data, hybrid_id)
        
        def on_failed(msg):
            self._on_hybrid_file_failed(msg, message, dest_hash, file_path, file_size, msg_data, hybrid_id, attempt, max_retries)
        
        message.register_delivery_callback(on_delivery)
        message.register_failed_callback(on_failed)
        
        # Monitor progresso
        self._start_progress_monitor(message, dest_hash, file_path, file_size, msg_data, msg_data.get('message_id'))
    
    def _on_hybrid_text_delivery(self, msg, message, dest_hash, content, msg_data, hybrid_id):
        """Callback per consegna testo riuscita in modalità hybrid"""
        print(f"✅ Hybrid: messaggio consegnato a {dest_hash[:16]}")
        
        rssi = getattr(msg, 'rssi', None)
        snr = getattr(msg, 'snr', None)
        q = getattr(msg, 'q', None)
        
        # Aggiorna tutti i messaggi con questo hybrid_id
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('hybrid_id') == hybrid_id:
                    m['status'] = 'delivered'
                    m['progress'] = 100
                    m['rssi'] = rssi
                    m['snr'] = snr
                    m['q'] = q
                    m['hybrid_resolved'] = True
                    m['hybrid_final_attempt'] = msg_data.get('hybrid_attempts', 0) + 1
                    if hasattr(message, 'packed') and message.packed:
                        m['total_size'] = len(message.packed)
                    break
        
        # Salva
        self._save_text_message(message, dest_hash, content, time.time(), 'delivered', 
                               msg_data.get('message_id'), rssi, snr, q)
    
    def _on_hybrid_text_failed(self, msg, message, dest_hash, content, msg_data, hybrid_id, attempt, max_retries):
        """Callback per consegna testo fallita in modalità hybrid"""
        print(f"⚠️ Hybrid tentativo {attempt}/{max_retries} fallito per {dest_hash[:16]}")
        
        # Aggiorna buffer
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('hybrid_id') == hybrid_id:
                    m['status'] = f'failed_attempt_{attempt}'
                    m['hybrid_attempts'] = attempt
        
        # Salva tentativo fallito
        self._save_text_message(message, dest_hash, content, time.time(), f'failed_attempt_{attempt}', 
                               msg_data.get('message_id'))
        
        if attempt >= max_retries:
            # Passa a propagation
            print(f"⚠️ Hybrid: {max_retries} tentativi falliti, passo a propagation node")
            self._escalate_to_propagation(
                hybrid_id, dest_hash, content, False, None, "", "opus", None
            )
        else:
            # Aspetta e riprova con backoff
            wait_time = 5 * attempt
            print(f"⏳ Hybrid: riprovo tra {wait_time}s (tentativo {attempt+1}/{max_retries})")
            
            def delayed_retry():
                time.sleep(wait_time)
                self._retry_hybrid_direct(
                    hybrid_id, dest_hash, content, False, None, "", "opus", None,
                    attempt + 1, max_retries
                )
            
            threading.Thread(target=delayed_retry, daemon=True).start()
    
    def _on_hybrid_file_delivery(self, msg, message, dest_hash, file_path, file_size, msg_data, hybrid_id):
        """Callback per consegna file riuscita in modalità hybrid"""
        elapsed = time.time() - msg_data.get('transfer_start', time.time())
        avg_speed = file_size / elapsed if elapsed > 0 else 0
        
        rssi = getattr(msg, 'rssi', None)
        snr = getattr(msg, 'snr', None)
        q = getattr(msg, 'q', None)
        
        print(f"✅ Hybrid: file consegnato a {dest_hash[:16]}")
        
        # Aggiorna tutti i messaggi con questo hybrid_id
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('hybrid_id') == hybrid_id:
                    m['status'] = 'delivered'
                    m['progress'] = 100
                    m['speed'] = avg_speed
                    m['elapsed'] = elapsed
                    m['rssi'] = rssi
                    m['snr'] = snr
                    m['q'] = q
                    m['bytes_sent'] = file_size
                    m['delivery_time'] = time.time()
                    m['hybrid_resolved'] = True
                    m['hybrid_final_attempt'] = msg_data.get('hybrid_attempts', 0) + 1
                    break
        
        # Salva
        self._save_file_message(message, time.time(), dest_hash, msg_data.get('attachments', []),
                               msg_data.get('file_path'), msg_data.get('content', ''), 
                               'delivered', file_size, msg_data.get('msg_type', 'file'), 
                               msg_data.get('message_id'), rssi, snr, q, elapsed, avg_speed)
    
    def _on_hybrid_file_failed(self, msg, message, dest_hash, file_path, file_size, msg_data, hybrid_id, attempt, max_retries):
        """Callback per consegna file fallita in modalità hybrid"""
        print(f"⚠️ Hybrid tentativo {attempt}/{max_retries} fallito per file a {dest_hash[:16]}")
        
        # Aggiorna buffer
        with self.messages_lock:
            for m in self.messages_buffer:
                if m.get('hybrid_id') == hybrid_id:
                    m['status'] = f'failed_attempt_{attempt}'
                    m['hybrid_attempts'] = attempt
        
        # Salva tentativo fallito
        self._save_file_message(message, time.time(), dest_hash, msg_data.get('attachments', []),
                               msg_data.get('file_path'), msg_data.get('content', ''), 
                               f'failed_attempt_{attempt}', file_size, msg_data.get('msg_type', 'file'), 
                               msg_data.get('message_id'))
        
        if attempt >= max_retries:
            # Passa a propagation
            print(f"⚠️ Hybrid: {max_retries} tentativi falliti, passo a propagation node per file")
            self._escalate_to_propagation(
                hybrid_id, dest_hash, None, True, file_path,
                msg_data.get('description', ''), 
                msg_data.get('audio_codec', 'opus'),
                msg_data.get('audio_mode', None)
            )
        else:
            # Aspetta e riprova con backoff
            wait_time = 5 * attempt
            print(f"⏳ Hybrid: riprovo tra {wait_time}s (tentativo {attempt+1}/{max_retries})")
            
            def delayed_retry():
                time.sleep(wait_time)
                self._retry_hybrid_direct(
                    hybrid_id, dest_hash, None, True, file_path,
                    msg_data.get('description', ''), 
                    msg_data.get('audio_codec', 'opus'),
                    msg_data.get('audio_mode', None),
                    attempt + 1, max_retries
                )
            
            threading.Thread(target=delayed_retry, daemon=True).start()
    
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
        
        filename = f"{int(timestamp)}_{msg_id[:8]}{'_failed' if 'failed' in status else ''}.lxb"
        
        # Ottieni i bytes impacchettati (LXMF li avrà generati a questo punto)
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
        
        filepath = os.path.join(peer_dir, filename)
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
        
        filename = f"{int(timestamp)}_{msg_id[:8]}{'_failed' if 'failed' in status else ''}.lxb"
        
        # Ottieni i bytes impacchettati
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
            'progress': 100 if 'delivered' in status else (0 if 'failed' in status else 50),
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
        
        filepath = os.path.join(peer_dir, filename)
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
        
        filename = f"{int(time.time())}.lxb"
        
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
            'raw_size': raw_size or len(packed_bytes),
            'command_type': command_type,
            'command_data': command_data,
            'total_size': len(packed_bytes),
            'lxmf_file': filename
        }
        
        filepath = os.path.join(peer_dir, filename)
        with open(filepath, 'wb') as f:
            f.write(msgpack.packb(container))
        
        container_for_buffer = self._bytes_to_json_compatible(container)
        
        with self.messages_lock:
            self.messages_buffer.append({
                'timestamp': container_for_buffer['timestamp'],
                'from': from_hash,
                'to': to_hash,
                'from_display': from_display,
                'content': content,
                'direction': 'incoming',
                'status': 'delivered',
                'rssi': rssi,
                'snr': snr,
                'q': q,
                'attachments': attachments or [],
                'lxmf_file': filename,
                'telemetry': telemetry,
                'battery': battery,
                'location': location,
                'appearance': appearance,
                'msg_type': msg_type,
                'raw_hex': raw_hex or (packed_bytes.hex() if packed_bytes else None),
                'raw_ascii': raw_ascii or (''.join(chr(b) if 32 <= b < 127 else '.' for b in packed_bytes) if packed_bytes else None),
                'raw_size': raw_size or len(packed_bytes),
                'command_type': command_type,
                'command_data': command_data,
                'total_size': len(packed_bytes),
                'progress': 100,
                'is_receiving': False
            })
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
    
    def _get_destination_identity(self, dest_hash, timeout=10):
        """Ottiene identità della destinazione"""
        try:
            clean_hash = self._clean_hash(dest_hash)
            if len(clean_hash) > 32:
                clean_hash = clean_hash[:32]
            
            dest_bytes = bytes.fromhex(clean_hash)
            
            # Richiedi path se necessario
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
    
    def _clean_hash(self, hash_str):
        """Pulisce un hash"""
        if not hash_str:
            return ''
        return hash_str.replace(":", "").replace(" ", "").replace("<", "").replace(">", "").lower()
    
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
        try:
            with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as temp_opus:
                temp_opus.write(opus_bytes)
                temp_opus_path = temp_opus.name
            
            temp_wav_path = temp_opus_path.replace('.opus', '.wav')
            
            cmd = [
                'ffmpeg', '-i', temp_opus_path,
                '-acodec', 'pcm_s16le',
                '-ac', '1',
                '-ar', '8000',
                '-y', temp_wav_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"❌ ffmpeg error: {result.stderr}")
                return None
            
            with wave.open(temp_wav_path, 'rb') as wav:
                frames = wav.readframes(wav.getnframes())
                samples = np.frombuffer(frames, dtype=np.int16)
            
            c2_bytes = encode_codec2(samples, target_mode)
            
            try:
                os.unlink(temp_opus_path)
                os.unlink(temp_wav_path)
            except:
                pass
            
            print(f"✅ Conversione Opus ({len(opus_bytes)} bytes) -> Codec2 mode {target_mode} ({len(c2_bytes)} bytes)")
            return c2_bytes
            
        except Exception as e:
            print(f"❌ Errore conversione Opus->Codec2: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _log_debug(self, event_type, data):
        """Logga eventi di debug"""
        # print(f"[DEBUG] {event_type}: {data}")
        pass