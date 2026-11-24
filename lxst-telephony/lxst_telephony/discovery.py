"""Discovery of LXST telephony peers through Reticulum announces."""

import threading

import RNS

from .models import Peer


class PeerDirectory:
    aspect_filter = "lxst.telephony"

    def __init__(self, on_peer=None):
        self._peers = {}
        self._lock = threading.RLock()
        self._on_peer = on_peer
        self._registered = False

    def start(self):
        if self._registered:
            return
        register = getattr(RNS.Transport, "register_announce_handler", None)
        if register:
            register(self)
            self._registered = True
        RNS.log(f"Listening for {self.aspect_filter} announces", RNS.LOG_DEBUG)

    def stop(self):
        if not self._registered:
            return
        deregister = getattr(RNS.Transport, "deregister_announce_handler", None)
        if deregister is None:
            deregister = getattr(RNS.Transport, "unregister_announce_handler", None)
        if deregister:
            try:
                deregister(self)
            except Exception:
                pass
        self._registered = False

    def clear(self):
        with self._lock:
            self._peers.clear()

    def received_announce(self, destination_hash, announced_identity, app_data=None,
                          announce_packet_hash=None, **kwargs):
        if announced_identity is None:
            return
        identity_hash = announced_identity.hash.hex()
        peer = Peer(
            identity_hash=identity_hash,
            destination_hash=destination_hash.hex(),
            identity=announced_identity,
            app_data=app_data,
        )
        with self._lock:
            old = self._peers.get(identity_hash)
            if old and old.display_name == peer.display_name:
                peer.display_name = old.display_name
            self._peers[identity_hash] = peer
        if callable(self._on_peer):
            try:
                self._on_peer(peer)
            except Exception as e:
                RNS.log(f"Peer callback failed: {e}", RNS.LOG_ERROR)

    def list_peers(self):
        with self._lock:
            return sorted(self._peers.values(), key=lambda peer: peer.identity_hash)

    def get(self, value):
        if value is None:
            return None
        if isinstance(value, Peer):
            value = value.identity_hash
        if hasattr(value, "hash"):
            value = value.hash
        if isinstance(value, bytes):
            value = value.hex()
        value = str(value).lower()
        with self._lock:
            return self._peers.get(value)

    def resolve(self, value):
        peer = self.get(value)
        if peer:
            return peer.identity
        if hasattr(value, "hash"):
            return value
        if isinstance(value, str):
            try:
                return RNS.Identity.recall(bytes.fromhex(value))
            except Exception:
                return None
        if isinstance(value, bytes):
            try:
                return RNS.Identity.recall(value)
            except Exception:
                return None
        return None
