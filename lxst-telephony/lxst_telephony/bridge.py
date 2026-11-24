"""PCM/WebSocket-oriented LXST telephony endpoint.

This module keeps the LXST wire protocol compatible with the standard
Telephone primitive, but replaces local sound-card input/output with an
injectable PCM sink and a PCM feed.
"""

import threading
import time

import numpy as np
import RNS
from RNS.vendor import umsgpack as mp

from LXST.Network import FIELD_SIGNALLING, LinkSource, Packetizer
from LXST.Primitives.Telephony import Profiles, Signalling
from LXST.Sinks import LocalSink

from .discovery import PeerDirectory
from .errors import CallBusyError, CallNotFoundError, NoIdentityError
from .models import Call, CallDirection, CallState


class _Endpoint:
    def __init__(self, samplerate=48000, channels=1):
        self.samplerate = samplerate
        self.channels = channels


class BridgeSink(LocalSink):
    def __init__(self, on_frame=None, samplerate=48000, channels=1):
        self.on_frame = on_frame
        self.samplerate = samplerate
        self.channels = channels
        self.should_run = True

    def can_receive(self, from_source=None):
        return True

    def handle_frame(self, frame, source=None, decoded=True):
        if self.should_run and callable(self.on_frame):
            self.on_frame(np.asarray(frame, dtype="float32"))

    def start(self):
        self.should_run = True

    def stop(self):
        self.should_run = False


class BridgeTelephony:
    """LXST endpoint that exchanges PCM frames instead of using a sound card."""

    RING_TIME = 30
    WAIT_TIME = 60

    def __init__(self, identity, profile=None, allowed=None, blocked=None,
                 on_incoming=None, on_state=None, on_ended=None,
                 on_audio=None, on_busy=None, on_rejected=None,
                 peers=None):
        self.identity = identity
        self.profile = profile or Profiles.QUALITY_MEDIUM
        self.allowed = allowed
        self.blocked = blocked or []
        self.on_incoming = on_incoming
        self.on_state = on_state
        self.on_ended = on_ended
        self.on_audio = on_audio
        self.on_busy = on_busy
        self.on_rejected = on_rejected
        self.peers = peers or PeerDirectory()

        self._lock = threading.RLock()
        self._call = None
        self._last_call = None
        self._link = None
        self._link_source = None
        self._tx_codec = None
        self._tx_packetizer = None
        self._tx_profile = None
        self._tx_buffer = np.zeros((0, 1), dtype="float32")
        self._tx_samplerate = 48000
        self._timers = []
        self._running = False
        self._closed = False

        self._rx_sink = BridgeSink(self._receive_pcm, samplerate=48000, channels=1)
        RNS.Reticulum.get_instance().wants_destination_type("lxst.telephony")
        self.destination = RNS.Destination(
            identity,
            RNS.Destination.IN,
            RNS.Destination.SINGLE,
            "lxst",
            "telephony",
        )
        self.destination.set_proof_strategy(RNS.Destination.PROVE_NONE)
        self.destination.set_link_established_callback(self._incoming_link)

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

    def start(self):
        if not self._running:
            self._running = True
            self.peers.start()

    def stop(self):
        if self._closed:
            return
        self._closed = True
        self._cancel_timers()
        self.hangup()
        self.peers.stop()
        try:
            RNS.Transport.deregister_destination(self.destination)
        except Exception:
            pass
        self.destination = None

    def announce(self):
        if self.destination:
            self.destination.announce()

    def call(self, identity, profile=None):
        if identity is None:
            raise NoIdentityError("No identity specified")
        with self._lock:
            if self.active_call:
                raise CallBusyError("A call is already active")
            call = self._new_call(identity, CallDirection.OUTGOING, CallState.CALLING, profile)
            self._call = call
            self._last_call = call

        self._emit_state(call)
        try:
            destination = RNS.Destination(
                identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxst", "telephony"
            )
            if not RNS.Transport.has_path(destination.hash):
                RNS.Transport.request_path(destination.hash)
            link = RNS.Link(
                destination,
                established_callback=self._outgoing_link_established,
                closed_callback=self._link_closed,
            )
            link.is_incoming = False
            link.is_outgoing = True
            link.answered = False
            link.established_at = None
            link.call_mode = Profiles.MODE_FULL_DUPLEX
            link.profile = self.profile
            with self._lock:
                self._link = link
                self._link_source = LinkSource(link, signalling_receiver=self, sink=self._rx_sink)
            self._timer(self.WAIT_TIME, self._call_timeout)
        except Exception as e:
            self._end_call(call, CallState.FAILED, str(e))
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
            raise NoIdentityError("Identity not known yet")
        return self.call(identity)

    def answer(self):
        call = self.active_call
        if call is None or call.direction != CallDirection.INCOMING:
            raise CallNotFoundError("No incoming call")
        if self._link:
            self._link.answered = True
        self._send_signal(Signalling.STATUS_CONNECTING)
        self._open_audio(call.profile)
        self._send_signal(Signalling.STATUS_ESTABLISHED)
        self._set_state(call, CallState.CONNECTED)
        return True

    def reject(self):
        call = self.active_call
        if call:
            self._send_signal(Signalling.STATUS_REJECTED)
            self._set_state(call, CallState.REJECTED)
        self.hangup()
        return True

    def hangup(self):
        link = None
        with self._lock:
            link = self._link
            self._link = None
        self._cancel_timers()
        if link is not None:
            try:
                link.teardown()
            except Exception:
                pass
        call = self.active_call
        if call:
            self._end_call(call, CallState.ENDED)

    def send_audio(self, pcm):
        if self._tx_codec is None or self._tx_packetizer is None:
            return False
        samples = np.asarray(pcm, dtype="float32")
        if samples.ndim == 1:
            samples = samples.reshape((-1, 1))
        if samples.shape[1] != 1:
            samples = samples[:, :1]
        samples_per_frame = int(self._tx_samplerate * Profiles.get_frame_time(self._tx_profile) / 1000)
        with self._lock:
            self._tx_buffer = np.concatenate([self._tx_buffer, samples])
            while self._tx_buffer.shape[0] >= samples_per_frame:
                frame = self._tx_buffer[:samples_per_frame]
                self._tx_buffer = self._tx_buffer[samples_per_frame:]
                try:
                    encoded = self._tx_codec.encode(frame)
                    self._tx_packetizer.handle_frame(encoded, self)
                except Exception as e:
                    RNS.log(f"Bridge audio encode failed: {e}", RNS.LOG_ERROR)
        return True


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

    def _send_signal(self, signal):
        link = self._link
        if link is None or link.status != RNS.Link.ACTIVE:
            return
        signals = signal if isinstance(signal, list) else [signal]
        RNS.Packet(link, mp.packb({FIELD_SIGNALLING: signals}), create_receipt=False).send()

    def _incoming_link(self, link):
        link.is_incoming = True
        link.is_outgoing = False
        link.answered = False
        link.established_at = None
        link.call_mode = Profiles.MODE_FULL_DUPLEX
        link.profile = self.profile
        link.set_remote_identified_callback(self._identified)
        link.set_link_closed_callback(self._link_closed)
        with self._lock:
            self._link = link
            self._link_source = LinkSource(link, signalling_receiver=self, sink=self._rx_sink)
        self._send_signal(Signalling.STATUS_AVAILABLE)

    def _outgoing_link_established(self, link):
        with self._lock:
            if self._link is None:
                self._link = link
                self._link_source = LinkSource(link, signalling_receiver=self, sink=self._rx_sink)
        RNS.log(f"Bridge call link established with {link.get_remote_identity()}", RNS.LOG_DEBUG)

    def _identified(self, link, identity):
        if link is not self._link:
            return
        if self.active_call or not self._is_allowed(identity):
            self._send_signal(Signalling.STATUS_BUSY)
            link.teardown()
            return
        with self._lock:
            call = self._new_call(identity, CallDirection.INCOMING, CallState.RINGING)
            call.peer = identity
            self._call = call
            self._last_call = call
        link.profile = self.profile
        link.call_mode = Profiles.MODE_FULL_DUPLEX
        self._send_signal(Signalling.STATUS_RINGING)
        self._notify(self.on_incoming, call)
        self._emit_state(call)
        self._timer(self.RING_TIME, self._ring_timeout)

    def _is_allowed(self, identity):
        identity_hash = identity.hash
        if identity_hash in self.blocked:
            return False
        if self.allowed is None:
            return True
        if callable(self.allowed):
            return bool(self.allowed(identity_hash))
        if isinstance(self.allowed, (list, tuple, set)):
            return identity_hash in self.allowed
        return True

    def _ring_timeout(self):
        call = self.active_call
        if call and call.direction == CallDirection.INCOMING and call.state == CallState.RINGING:
            self.reject()

    def _call_timeout(self):
        call = self.active_call
        if call and call.state not in (CallState.CONNECTED, CallState.ENDED):
            self.hangup()


    def signalling_received(self, signals, source):
        if source is not self._link:
            return
        for signal in signals:
            if getattr(source, "is_incoming", False) and not getattr(source, "answered", False) and signal < Signalling.PREFERRED_MODE:
                continue
            if signal == Signalling.STATUS_AVAILABLE:
                source.identify(self.identity)
            elif signal == Signalling.STATUS_RINGING:
                call = self.active_call
                if call:
                    self._set_state(call, CallState.RINGING)
                    self._send_signal([
                        Signalling.PREFERRED_PROFILE + call.profile,
                        Signalling.PREFERRED_MODE + Profiles.MODE_FULL_DUPLEX,
                    ])
            elif signal == Signalling.STATUS_CONNECTING:
                call = self.active_call
                if call:
                    self._open_audio(call.profile)
                    self._set_state(call, CallState.CONNECTING)
            elif signal == Signalling.STATUS_ESTABLISHED:
                call = self.active_call
                if call:
                    self._set_state(call, CallState.CONNECTED)
            elif signal == Signalling.STATUS_BUSY:
                call = self.active_call
                if call:
                    self._set_state(call, CallState.BUSY)
                    self._notify(self.on_busy, call)
                self.hangup()
            elif signal == Signalling.STATUS_REJECTED:
                call = self.active_call
                if call:
                    self._set_state(call, CallState.REJECTED)
                    self._notify(self.on_rejected, call)
                self.hangup()
            elif signal >= Signalling.PREFERRED_PROFILE:
                call = self.active_call
                if call:
                    call.profile = signal - Signalling.PREFERRED_PROFILE
            elif signal >= Signalling.PREFERRED_MODE:
                pass

    def _open_audio(self, profile):
        if self._tx_codec is not None:
            return
        link = self._link
        if link is None:
            return
        codec = Profiles.get_codec(profile)
        source = _Endpoint(self._tx_samplerate, 1)
        source.codec = codec
        codec.source = source
        packetizer = Packetizer(link, failure_callback=self._transmit_failure)
        packetizer.source = source
        packetizer.start()
        with self._lock:
            self._tx_codec = codec
            self._tx_packetizer = packetizer
            self._tx_profile = profile
            self._tx_buffer = np.zeros((0, 1), dtype="float32")

    def _receive_pcm(self, frame):
        self._notify(self.on_audio, frame)

    def _transmit_failure(self):
        RNS.log("Bridge transmission failed, hanging up", RNS.LOG_ERROR)
        self.hangup()

    def _link_closed(self, link):
        with self._lock:
            if link is self._link:
                self._link = None
        call = self.active_call
        if call:
            self._end_call(call, CallState.ENDED)

    def _set_state(self, call, state, error=None):
        with self._lock:
            if call is None:
                return
            call.state = state
            if error:
                call.error = error
            if state == CallState.CONNECTED and call.answered_at is None:
                call.answered_at = time.time()
            if state in (CallState.ENDED, CallState.FAILED, CallState.BUSY, CallState.REJECTED):
                call.ended_at = call.ended_at or time.time()
                self._tx_buffer = np.zeros((0, 1), dtype="float32")
                self._tx_codec = None
                self._tx_packetizer = None
                self._tx_profile = None
        self._emit_state(call)

    def _end_call(self, call, state, error=None):
        self._set_state(call, state, error)
        self._notify(self.on_ended, call)

    def _emit_state(self, call):
        self._notify(self.on_state, call)

    def _notify(self, callback, value):
        if not callable(callback):
            return
        try:
            callback(value)
        except Exception as e:
            RNS.log(f"Bridge callback failed: {e}", RNS.LOG_ERROR)

    def _timer(self, seconds, callback):
        timer = threading.Timer(seconds, callback)
        timer.daemon = True
        self._timers.append(timer)
        timer.start()

    def _cancel_timers(self):
        for timer in self._timers:
            timer.cancel()
        self._timers = []
