"""LXST telephony endpoint built on the public LXST Telephone API."""

import threading
import time

import RNS
from LXST.Primitives.Telephony import Profiles, Signalling, Telephone

from .discovery import PeerDirectory
from .errors import CallBusyError, CallNotFoundError, NoIdentityError
from .models import Call, CallDirection, CallState
from .native import NativeAudio


class LxstTelephony:
    """Event-driven peer-to-peer LXST telephony endpoint."""

    def __init__(self, identity, profile=None, ring_time=30, wait_time=60,
                 auto_answer=None, allowed=None, blocked=None, speaker=None,
                 microphone=None, ringer=None, low_latency=False, agc=True,
                 bandpass=True, echo_cancellation=True,
                 announce_on_start=False, on_incoming=None, on_state=None,
                 on_ended=None, on_busy=None, on_rejected=None,
                 on_established=None, peers=None, telephone_factory=None):
        self.identity = identity
        self.profile = profile or Profiles.QUALITY_MEDIUM
        self._lock = threading.RLock()
        self._call = None
        self._last_call = None
        self._running = False
        self._closed = False
        self._monitor_stop = threading.Event()

        self.on_incoming = on_incoming
        self.on_state = on_state
        self.on_ended = on_ended
        self.on_busy = on_busy
        self.on_rejected = on_rejected
        self.on_established = on_established

        self.peers = peers or PeerDirectory()
        factory = telephone_factory or Telephone
        self._telephone = factory(
            identity,
            ring_time=ring_time,
            wait_time=wait_time,
            auto_answer=auto_answer,
        )

        NativeAudio(
            speaker=speaker,
            microphone=microphone,
            ringer=ringer,
            low_latency=low_latency,
            agc=agc,
            bandpass=bandpass,
            echo_cancellation=echo_cancellation,
        ).configure(self._telephone)

        if allowed is not None:
            self._telephone.set_allowed(allowed)
        if blocked is not None:
            self._telephone.set_blocked(blocked)

        self._telephone.set_ringing_callback(self._on_ringing)
        self._telephone.set_established_callback(self._on_established)
        self._telephone.set_ended_callback(self._on_ended)
        self._telephone.set_busy_callback(self._on_busy)
        self._telephone.set_rejected_callback(self._on_rejected)

        if announce_on_start:
            self.announce()

    @property
    def telephone(self):
        return self._telephone

    @property
    def active_call(self):
        with self._lock:
            if self._call and self._call.state not in (
                CallState.ENDED, CallState.FAILED, CallState.BUSY, CallState.REJECTED
            ):
                return self._call
        return None

    @property
    def last_call(self):
        with self._lock:
            return self._last_call

    @property
    def state(self):
        call = self.active_call
        return call.state if call else CallState.IDLE

    @property
    def is_available(self):
        return self.state == CallState.IDLE

    def start(self):
        if self._running:
            return
        self._running = True
        self.peers.start()
        self._monitor_stop.clear()
        threading.Thread(target=self._monitor, daemon=True).start()

    def stop(self):
        if self._closed:
            return
        self._closed = True
        self._monitor_stop.set()
        self._running = False
        try:
            if getattr(self._telephone, "active_call", None):
                self._telephone.hangup()
                time.sleep(0.25)
        except Exception:
            pass
        self.peers.stop()
        try:
            self._telephone.teardown()
        except Exception:
            pass
        time.sleep(0.15)

    def announce(self):
        self._telephone.announce()

    def set_allowed(self, allowed):
        self._telephone.set_allowed(allowed)

    def set_blocked(self, blocked):
        self._telephone.set_blocked(blocked)

    def set_profile(self, profile):
        self.profile = profile
        if self._call:
            self._telephone.switch_profile(profile)

    def call(self, identity, profile=None):
        if identity is None:
            raise NoIdentityError("No identity specified")

        profile = profile or self.profile
        with self._lock:
            if self.active_call:
                raise CallBusyError("A call is already active")
            call = self._new_call(identity, CallDirection.OUTGOING, CallState.CALLING, profile)
            self._call = call
            self._last_call = call

        self._emit_state(call)
        try:
            self._telephone.call(identity, profile=profile)
        except Exception as e:
            self._set_state(call, CallState.FAILED, error=str(e))
            raise
        return call

    def call_hash(self, identity_hash, wait=15.0):
        identity = self.peers.resolve(identity_hash)
        if identity is None:
            try:
                hash_bytes = bytes.fromhex(identity_hash) if isinstance(identity_hash, str) else identity_hash
                destination_hash = RNS.Destination.hash_from_name_and_identity(
                    "lxst.telephony", hash_bytes
                )
                RNS.Transport.request_path(destination_hash)
                deadline = time.time() + max(0.0, float(wait))
                while time.time() < deadline:
                    identity = RNS.Identity.recall(hash_bytes)
                    if identity is not None:
                        break
                    time.sleep(0.25)
            except Exception:
                pass
        if identity is None:
            raise NoIdentityError(
                "Identity not known yet; wait for an lxst.telephony announce or path response"
            )
        return self.call(identity)

    def answer(self, identity=None):
        call = self.active_call
        if identity is None and call is not None:
            identity = call.peer
        if identity is None:
            active = getattr(self._telephone, "active_call", None)
            identity = active.get_remote_identity() if active else None
        if identity is None or call is None:
            raise CallNotFoundError("No incoming call")
        self._set_state(call, CallState.CONNECTING)
        return self._telephone.answer(identity)

    def reject(self):
        call = self.active_call
        if call:
            self._set_state(call, CallState.REJECTED)
        return self._telephone.hangup()

    def hangup(self):
        return self._telephone.hangup()

    def mute_transmit(self, mute=True):
        self._telephone.mute_transmit(mute)

    def mute_receive(self, mute=True):
        self._telephone.mute_receive(mute)

    def set_low_latency(self, enabled=True):
        self._telephone.set_low_latency_output(enabled)

    def switch_profile(self, profile):
        self.profile = profile
        self._telephone.switch_profile(profile)

    def _new_call(self, identity, direction, state, profile=None):
        peer_hash = identity.hash.hex() if hasattr(identity, "hash") else str(identity)
        destination_hash = None
        if hasattr(identity, "hash"):
            try:
                destination_hash = RNS.Destination.hash_from_name_and_identity(
                    "lxst.telephony", identity.hash
                ).hex()
            except Exception:
                pass
        return Call(
            id=peer_hash,
            peer_hash=peer_hash,
            direction=direction,
            state=state,
            peer=identity,
            destination_hash=destination_hash,
            profile=profile or self.profile,
        )

    def _set_state(self, call, state, error=None):
        with self._lock:
            if call is None:
                return
            call.state = state
            if error is not None:
                call.error = error
            if state == CallState.CONNECTED and call.answered_at is None:
                call.answered_at = time.time()
            if state in (CallState.ENDED, CallState.FAILED, CallState.BUSY, CallState.REJECTED):
                call.ended_at = call.ended_at or time.time()
        self._emit_state(call)

    def _emit_state(self, call):
        self._notify(self.on_state, call)

    def _notify(self, callback, call):
        if not callable(callback):
            return
        try:
            callback(call)
        except Exception as e:
            RNS.log(f"Telephony callback failed: {e}", RNS.LOG_ERROR)

    def _on_ringing(self, identity):
        with self._lock:
            call = self._call
            if call is None or call.peer_hash != identity.hash.hex():
                call = self._new_call(identity, CallDirection.INCOMING, CallState.RINGING)
                self._call = call
                self._last_call = call
            else:
                call.state = CallState.RINGING
        self._notify(self.on_incoming, call)
        self._emit_state(call)

    def _on_established(self, identity):
        with self._lock:
            call = self._call
            if call is None:
                call = self._new_call(identity, CallDirection.INCOMING, CallState.CONNECTED)
                self._call = call
                self._last_call = call
        self._set_state(call, CallState.CONNECTED)
        self._notify(self.on_established, call)

    def _on_ended(self, identity):
        with self._lock:
            call = self._call
        if call:
            self._set_state(call, CallState.ENDED)
            self._notify(self.on_ended, call)

    def _on_busy(self, identity):
        with self._lock:
            call = self._call
        if call:
            self._set_state(call, CallState.BUSY)
            self._notify(self.on_busy, call)

    def _on_rejected(self, identity):
        with self._lock:
            call = self._call
        if call:
            self._set_state(call, CallState.REJECTED)
            self._notify(self.on_rejected, call)

    def _monitor(self):
        states = {
            Signalling.STATUS_CALLING: CallState.CALLING,
            Signalling.STATUS_RINGING: CallState.RINGING,
            Signalling.STATUS_CONNECTING: CallState.CONNECTING,
            Signalling.STATUS_ESTABLISHED: CallState.CONNECTED,
        }
        while not self._monitor_stop.is_set():
            call = self.active_call
            status = getattr(self._telephone, "call_status", None)
            link = getattr(self._telephone, "active_call", None)
            if call and link is not None and getattr(link, "established_at", None):
                if call.answered_at is None:
                    self._set_state(call, CallState.CONNECTED)
            elif call and status in states and call.state != states[status]:
                self._set_state(call, states[status])
            self._monitor_stop.wait(0.2)
