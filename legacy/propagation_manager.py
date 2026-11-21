"""
Propagation Manager per LXMF - Modulo separato
Contiene tutta la complessità della propagazione
Basato sul codice originale nativo
"""

import os
import time
import json
import base64
import threading
import msgpack
import RNS
import LXMF

# Costanti LXMF
APP_NAME = "lxmf"
FIELD_TICKET = 0x7F
FIELD_COMMANDS = 0x78

# ============================================
# LXMF PEER CLASS
# ============================================
class LXMPeer:
    """Peer LXMF per sync e propagazione (dal router originale)"""
    
    # Costanti
    MAX_UNREACHABLE = 3600 * 24 * 7  # 7 giorni
    SYNC_BACKOFF_MAX = 3600 * 24      # 24 ore
    
    # Stati
    IDLE = 0x00
    OFFER_REQUESTED = 0x01
    OFFER_RECEIVED = 0x02
    SYNCING = 0x03
    
    # Path per richieste
    OFFER_REQUEST_PATH = 0x60
    MESSAGE_GET_PATH = 0x61
    
    # Codici errore
    ERROR_NO_IDENTITY = 0xE1
    ERROR_NO_ACCESS = 0xE2
    
    def __init__(self, router, destination_hash):
        self.router = router
        self.destination_hash = destination_hash
        self.identity = RNS.Identity.recall(destination_hash)
        
        if self.identity:
            self.destination = RNS.Destination(
                self.identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                APP_NAME, "propagation"
            )
        else:
            self.destination = None
        
        self.alive = False
        self.last_heard = 0
        self.peering_timebase = 0
        self.sync_backoff = 0
        self.next_sync_attempt = 0
        
        # Metriche
        self.link_establishment_rate = 0
        self.successful_syncs = 0
        self.failed_syncs = 0
        self.state = self.IDLE
        
        # Code messaggi
        self.unhandled_messages = []
        self.handled_messages = {}  # transient_id -> [transient_id, timestamp, data]
        
        # Limiti
        self.propagation_transfer_limit = None
    
    def handle_message(self, transient_id):
        """Aggiunge messaggio alla coda per questo peer"""
        if transient_id not in self.handled_messages:
            self.unhandled_messages.append(transient_id)
    
    def sync(self):
        """Avvia sincronizzazione con peer"""
        if self.alive:
            self._sync_active()
        else:
            self._sync_unresponsive()
    
    def _sync_active(self):
        """Sync con peer attivo"""
        if self.state == self.IDLE and len(self.unhandled_messages) > 0:
            self.state = self.OFFER_REQUESTED
            # Implementare logica sync con peer attivo
            pass
    
    def _sync_unresponsive(self):
        """Tentativo con peer non attivo"""
        pass
    
    def to_bytes(self):
        """Serializza peer per storage"""
        data = [
            self.destination_hash.hex() if isinstance(self.destination_hash, bytes) else self.destination_hash,
            self.peering_timebase,
            self.unhandled_messages,
            self.propagation_transfer_limit
        ]
        return msgpack.packb(data)
    
    @classmethod
    def from_bytes(cls, data, router):
        """Deserializza peer da storage"""
        try:
            dest_hash = bytes.fromhex(data[0]) if isinstance(data[0], str) else data[0]
            peer = cls(router, dest_hash)
            peer.peering_timebase = data[1]
            peer.unhandled_messages = data[2]
            peer.propagation_transfer_limit = data[3]
            return peer
        except Exception as e:
            RNS.log(f"Error deserializing peer: {e}", RNS.LOG_ERROR)
            return None
    
    def __str__(self):
        return f"<LXMPeer {RNS.prettyhexrep(self.destination_hash)}>"


# ============================================
# PROPAGATION MANAGER
# ============================================
class PropagationManager:
    """Gestisce la propagazione dei messaggi (stile router originale)"""
    
    # Costanti stati
    PR_IDLE = 0x00
    PR_PATH_REQUESTED = 0x01
    PR_LINK_ESTABLISHING = 0x02
    PR_LINK_ESTABLISHED = 0x03
    PR_REQUEST_SENT = 0x04
    PR_RECEIVING = 0x05
    PR_RESPONSE_RECEIVED = 0x06
    PR_COMPLETE = 0x07
    PR_NO_PATH = 0xE1
    PR_LINK_FAILED = 0xE2
    PR_TRANSFER_FAILED = 0xE3
    PR_NO_IDENTITY_RCVD = 0xE4
    PR_NO_ACCESS = 0xE5
    PR_FAILED = 0xFF
    
    PR_ALL_MESSAGES = -1
    PR_PATH_TIMEOUT = 15  # secondi
    
    def __init__(self, router, storage_path):
        self.router = router
        self.storage_path = storage_path
        self.messagepath = os.path.join(storage_path, "messagestore")
        
        # Crea directory messaggi
        os.makedirs(self.messagepath, exist_ok=True)
        
        # Entrate propagazione
        self.propagation_entries = {}  # transient_id -> [destination_hash, filepath, received, size]
        
        # Cache ID processati
        self.locally_processed = {}     # transient_id -> timestamp
        
        # Limiti
        self.max_message_age = 3600 * 24 * 30  # 30 giorni
        self.message_storage_limit = None  # Nessun limite
        
        # Stato trasferimento
        self.transfer_state = self.PR_IDLE
        self.transfer_progress = 0.0
        self.transfer_max_messages = self.PR_ALL_MESSAGES
        self.transfer_last_result = None
        
        # Link e sync
        self.outbound_propagation_node = None
        self.outbound_propagation_link = None
        self.wants_download_on_path_available_from = None
        self.wants_download_on_path_available_to = None
        self.wants_download_on_path_available_timeout = 0
        
        # Carica messaggi esistenti
        self._load_messages()
    
    def _load_messages(self):
        """Carica messaggi dal message store"""
        try:
            for filename in os.listdir(self.messagepath):
                components = filename.split("_")
                if len(components) == 2:
                    if float(components[1]) > 0:
                        if len(components[0]) == RNS.Identity.HASHLENGTH//8*2:
                            try:
                                transient_id = bytes.fromhex(components[0])
                                received = float(components[1])
                                
                                filepath = os.path.join(self.messagepath, filename)
                                msg_size = os.path.getsize(filepath)
                                
                                with open(filepath, "rb") as f:
                                    destination_hash = f.read(LXMF.LXMessage.DESTINATION_LENGTH)
                                
                                self.propagation_entries[transient_id] = [
                                    destination_hash,
                                    filepath,
                                    received,
                                    msg_size,
                                ]
                                
                            except Exception as e:
                                print(f"Could not read LXM from message store: {e}")
            
            print(f"📚 Caricati {len(self.propagation_entries)} messaggi propagati")
            
        except Exception as e:
            print(f"Errore caricamento messaggi propagati: {e}")
    
    def receive_propagated(self, lxmf_data):
        """Riceve messaggio propagato"""
        try:
            transient_id = RNS.Identity.full_hash(lxmf_data)
            
            if transient_id in self.propagation_entries:
                return False  # Già ricevuto
            
            # Estrai destination hash
            destination_hash = lxmf_data[:LXMF.LXMessage.DESTINATION_LENGTH]
            
            # Salva su disco
            timestamp = time.time()
            filename = f"{RNS.hexrep(transient_id, delimit=False)}_{timestamp}.lxm"
            filepath = os.path.join(self.messagepath, filename)
            
            with open(filepath, 'wb') as f:
                f.write(lxmf_data)
            
            # Registra
            self.propagation_entries[transient_id] = [
                destination_hash,
                filepath,
                timestamp,
                len(lxmf_data)
            ]
            
            # Registra come processato localmente
            self.locally_processed[transient_id] = timestamp
            
            print(f"📦 Messaggio propagato ricevuto: {RNS.prettyhexrep(transient_id)}")
            
            return True
            
        except Exception as e:
            print(f"Errore ricezione messaggio propagato: {e}")
            return False
    
    def get_for_peer(self, peer_hash):
        """Ottiene messaggi da inviare a un peer"""
        messages = []
        
        for tid, entry in self.propagation_entries.items():
            dest_hash = entry[0]
            if isinstance(dest_hash, bytes):
                dest_hash = dest_hash.hex()
            
            if dest_hash == peer_hash:
                # Aggiungi se per questo peer
                try:
                    with open(entry[1], 'rb') as f:
                        messages.append(f.read())
                except Exception as e:
                    print(f"Errore lettura messaggio {RNS.prettyhexrep(tid)}: {e}")
        
        return messages
    
    def cleanup(self):
        """Pulisce messaggi vecchi"""
        now = time.time()
        to_delete = []
        
        for tid, entry in self.propagation_entries.items():
            age = now - entry[2]  # received timestamp
            if age > self.max_message_age:
                to_delete.append(tid)
                try:
                    os.unlink(entry[1])
                except:
                    pass
        
        for tid in to_delete:
            del self.propagation_entries[tid]
            
        if to_delete:
            print(f"🧹 Puliti {len(to_delete)} messaggi propagati vecchi")
    
    def storage_size(self):
        """Dimensione totale messaggi in store"""
        return sum(entry[3] for entry in self.propagation_entries.values())
    
    def set_propagation_node(self, node_hash):
        """Imposta nodo di propagazione"""
        self.outbound_propagation_node = node_hash
        print(f"📡 Nodo propagazione impostato: {node_hash}")
    
    def request_messages(self, identity, max_messages=PR_ALL_MESSAGES):
        """Richiede messaggi dal nodo di propagazione"""
        if max_messages is None:
            max_messages = self.PR_ALL_MESSAGES
        
        self.transfer_progress = 0.0
        self.transfer_max_messages = max_messages
        
        if self.outbound_propagation_node:
            node_bytes = self._hash_to_bytes(self.outbound_propagation_node)
            
            if self.outbound_propagation_link and self.outbound_propagation_link.status == RNS.Link.ACTIVE:
                self.transfer_state = self.PR_LINK_ESTABLISHED
                self.outbound_propagation_link.identify(identity)
                self.outbound_propagation_link.request(
                    LXMPeer.MESSAGE_GET_PATH,
                    [None, None],  # Both want and have fields to None to get message list
                    response_callback=self._message_list_response,
                    failed_callback=self._message_get_failed
                )
                self.transfer_state = self.PR_REQUEST_SENT
                return True
                
            else:
                if self.outbound_propagation_link is None:
                    if RNS.Transport.has_path(node_bytes):
                        self.wants_download_on_path_available_from = None
                        self.transfer_state = self.PR_LINK_ESTABLISHING
                        print(f"🔗 Stabilisco link con nodo propagazione {self.outbound_propagation_node[:16]}...")
                        
                        propagation_node_identity = RNS.Identity.recall(node_bytes)
                        if propagation_node_identity:
                            propagation_node_destination = RNS.Destination(
                                propagation_node_identity,
                                RNS.Destination.OUT,
                                RNS.Destination.SINGLE,
                                APP_NAME, "propagation"
                            )
                            
                            def msg_request_established_callback(link):
                                self.request_messages(identity, self.transfer_max_messages)
                            
                            self.outbound_propagation_link = RNS.Link(
                                propagation_node_destination,
                                established_callback=msg_request_established_callback
                            )
                            return True
                        else:
                            print(f"❌ Identità nodo propagazione non trovata")
                            return False
                    else:
                        print(f"🔍 Richiedo path per nodo propagazione {self.outbound_propagation_node[:16]}...")
                        RNS.Transport.request_path(node_bytes)
                        self.wants_download_on_path_available_from = node_bytes
                        self.wants_download_on_path_available_to = identity
                        self.wants_download_on_path_available_timeout = time.time() + self.PR_PATH_TIMEOUT
                        self.transfer_state = self.PR_PATH_REQUESTED
                        
                        # Avvia thread per monitorare path
                        threading.Thread(target=self._request_messages_path_job, daemon=True).start()
                        return True
                else:
                    print("⏳ In attesa che il link di propagazione diventi attivo...")
                    return False
        else:
            print("⚠️ Nessun nodo di propagazione configurato")
            return False
    
    def _request_messages_path_job(self):
        """Job per attendere path"""
        path_timeout = self.wants_download_on_path_available_timeout
        target = self.wants_download_on_path_available_from
        
        while not RNS.Transport.has_path(target) and time.time() < path_timeout:
            time.sleep(0.5)
        
        if RNS.Transport.has_path(target):
            self.request_messages(
                self.wants_download_on_path_available_to,
                self.transfer_max_messages
            )
        else:
            print("⏱️ Timeout richiesta path per nodo propagazione")
            self.transfer_state = self.PR_NO_PATH
    
    def _message_list_response(self, request_receipt):
        """Callback per lista messaggi"""
        if request_receipt.response == LXMPeer.ERROR_NO_IDENTITY:
            print("❌ Nodo propagazione: identità mancante")
            if self.outbound_propagation_link:
                self.outbound_propagation_link.teardown()
            self.transfer_state = self.PR_NO_IDENTITY_RCVD
            
        elif request_receipt.response == LXMPeer.ERROR_NO_ACCESS:
            print("❌ Nodo propagazione: accesso negato")
            if self.outbound_propagation_link:
                self.outbound_propagation_link.teardown()
            self.transfer_state = self.PR_NO_ACCESS
            
        else:
            if request_receipt.response and isinstance(request_receipt.response, list):
                haves = []
                wants = []
                
                if len(request_receipt.response) > 0:
                    for transient_id in request_receipt.response:
                        # Verifica se abbiamo già il messaggio
                        if transient_id in self.propagation_entries:
                            haves.append(transient_id)
                        else:
                            if self.transfer_max_messages == self.PR_ALL_MESSAGES or len(wants) < self.transfer_max_messages:
                                wants.append(transient_id)
                    
                    # Richiedi messaggi voluti
                    if wants:
                        request_receipt.link.request(
                            LXMPeer.MESSAGE_GET_PATH,
                            [wants, haves, None],  # Nessun limite di transfer
                            response_callback=self._message_get_response,
                            failed_callback=self._message_get_failed,
                            progress_callback=self._message_get_progress
                        )
                    else:
                        self.transfer_state = self.PR_COMPLETE
                        self.transfer_progress = 1.0
                        self.transfer_last_result = 0
                else:
                    self.transfer_state = self.PR_COMPLETE
                    self.transfer_progress = 1.0
                    self.transfer_last_result = 0
    
    def _message_get_response(self, request_receipt):
        """Callback per ricezione messaggi"""
        if request_receipt.response == LXMPeer.ERROR_NO_IDENTITY:
            print("❌ Nodo propagazione: identità mancante (get)")
            if self.outbound_propagation_link:
                self.outbound_propagation_link.teardown()
            self.transfer_state = self.PR_NO_IDENTITY_RCVD
            
        elif request_receipt.response == LXMPeer.ERROR_NO_ACCESS:
            print("❌ Nodo propagazione: accesso negato (get)")
            if self.outbound_propagation_link:
                self.outbound_propagation_link.teardown()
            self.transfer_state = self.PR_NO_ACCESS
            
        else:
            if request_receipt.response and len(request_receipt.response) > 0:
                haves = []
                for lxmf_data in request_receipt.response:
                    # Processa messaggio
                    self.router.lxmf_propagation(lxmf_data)
                    haves.append(RNS.Identity.full_hash(lxmf_data))
                
                # Notifica al nodo che abbiamo ricevuto i messaggi
                request_receipt.link.request(
                    LXMPeer.MESSAGE_GET_PATH,
                    [None, haves],
                    failed_callback=self._message_get_failed
                )
                
                print(f"✅ Ricevuti {len(request_receipt.response)} messaggi dal nodo propagazione")
            
            self.transfer_state = self.PR_COMPLETE
            self.transfer_progress = 1.0
            self.transfer_last_result = len(request_receipt.response) if request_receipt.response else 0
    
    def _message_get_progress(self, request_receipt):
        """Progresso ricezione"""
        self.transfer_state = self.PR_RECEIVING
        self.transfer_progress = request_receipt.get_progress()
    
    def _message_get_failed(self, request_receipt):
        """Fallimento richiesta"""
        print("❌ Richiesta messaggi al nodo propagazione fallita")
        if self.outbound_propagation_link:
            self.outbound_propagation_link.teardown()
        self.transfer_state = self.PR_FAILED
    
    def cancel_requests(self):
        """Annulla richieste in corso"""
        if self.outbound_propagation_link:
            self.outbound_propagation_link.teardown()
            self.outbound_propagation_link = None
        
        self.transfer_state = self.PR_IDLE
        self.transfer_progress = 0.0
        self.wants_download_on_path_available_from = None
        self.wants_download_on_path_available_to = None
    
    def _hash_to_bytes(self, hash_str):
        """Converte hash stringa in bytes"""
        if isinstance(hash_str, str):
            hash_str = hash_str.replace(":", "").replace(" ", "").replace("<", "").replace(">", "")
            return bytes.fromhex(hash_str)
        return hash_str