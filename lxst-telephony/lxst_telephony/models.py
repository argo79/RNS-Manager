"""Data models for the LXST telephony package."""

from dataclasses import dataclass, field
from enum import Enum
import time


class CallState(str, Enum):
    IDLE = "idle"
    CALLING = "calling"
    RINGING = "ringing"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    BUSY = "busy"
    REJECTED = "rejected"
    ENDED = "ended"
    FAILED = "failed"


class CallDirection(str, Enum):
    INCOMING = "incoming"
    OUTGOING = "outgoing"


@dataclass
class Call:
    id: str
    peer_hash: str
    direction: CallDirection
    state: CallState = CallState.CALLING
    peer: object = None
    destination_hash: str = None
    profile: int = None
    started_at: float = field(default_factory=time.time)
    answered_at: float = None
    ended_at: float = None
    error: str = None

    @property
    def was_connected(self):
        return self.answered_at is not None

    @property
    def duration(self):
        if self.ended_at is None:
            return None
        start = self.answered_at or self.started_at
        return max(0.0, self.ended_at - start)

    def to_dict(self):
        return {
            "id": self.id,
            "peer_hash": self.peer_hash,
            "direction": self.direction.value,
            "state": self.state.value,
            "destination_hash": self.destination_hash,
            "profile": self.profile,
            "started_at": self.started_at,
            "answered_at": self.answered_at,
            "ended_at": self.ended_at,
            "was_connected": self.was_connected,
            "duration": self.duration,
            "error": self.error,
        }


@dataclass
class Peer:
    identity_hash: str
    destination_hash: str
    identity: object = None
    display_name: str = None
    app_data: bytes = None
    last_seen: float = field(default_factory=time.time)

    def to_dict(self):
        return {
            "identity_hash": self.identity_hash,
            "destination_hash": self.destination_hash,
            "display_name": self.display_name,
            "last_seen": self.last_seen,
        }
