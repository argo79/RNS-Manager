import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lxst-telephony'))

import RNS
from LXST.Primitives.Telephony import Profiles, Signalling

from lxst_telephony import CallState, LxstTelephony, PeerDirectory
from lxst_telephony.errors import CallBusyError, NoIdentityError


class FakeLink:
    def __init__(self, identity):
        self.identity = identity

    def get_remote_identity(self):
        return self.identity


class FakeTelephone:
    def __init__(self, identity, ring_time=30, wait_time=60, auto_answer=None):
        self.identity = identity
        self.ring_time = ring_time
        self.wait_time = wait_time
        self.auto_answer = auto_answer
        self.active_call = None
        self.call_status = Signalling.STATUS_AVAILABLE
        self.announces = 0
        self.hangups = 0
        self.answers = []
        self.calls = []
        self.allowed = None
        self.blocked = None
        self.callbacks = {}
        self.muted_tx = False
        self.muted_rx = False

    def set_ringing_callback(self, callback):
        self.callbacks['ringing'] = callback

    def set_established_callback(self, callback):
        self.callbacks['established'] = callback

    def set_ended_callback(self, callback):
        self.callbacks['ended'] = callback

    def set_busy_callback(self, callback):
        self.callbacks['busy'] = callback

    def set_rejected_callback(self, callback):
        self.callbacks['rejected'] = callback

    def set_allowed(self, allowed):
        self.allowed = allowed

    def set_blocked(self, blocked):
        self.blocked = blocked

    def set_speaker(self, device):
        pass

    def set_microphone(self, device):
        pass

    def set_ringer(self, device):
        pass

    def set_low_latency_output(self, enabled):
        pass

    def enable_agc(self, enabled):
        pass

    def enable_bandpass(self, enabled):
        pass

    def enable_echo_cancellation(self, enabled):
        pass

    def announce(self, attached_interface=None):
        self.announces += 1

    def call(self, identity, profile=None, mode=None):
        self.calls.append((identity, profile, mode))
        self.active_call = FakeLink(identity)
        self.call_status = Signalling.STATUS_CALLING

    def answer(self, identity):
        self.answers.append(identity)
        self.call_status = Signalling.STATUS_CONNECTING
        return True

    def hangup(self, reason=None):
        self.hangups += 1
        self.active_call = None
        self.call_status = Signalling.STATUS_AVAILABLE

    def teardown(self):
        self.hangup()

    def mute_transmit(self, mute=True):
        self.muted_tx = mute

    def mute_receive(self, mute=True):
        self.muted_rx = mute

    def switch_profile(self, profile):
        self.profile = profile

    def ring(self, identity):
        self.callbacks['ringing'](identity)

    def establish(self, identity):
        self.callbacks['established'](identity)

    def end(self, identity):
        self.callbacks['ended'](identity)

class LxstTelephonyTests(unittest.TestCase):
    def setUp(self):
        self.identity = types.SimpleNamespace(hash=b'\x11' * 16)
        self.remote = types.SimpleNamespace(hash=b'\x22' * 16)
        self.fake = None
        self.events = []

    def factory(self, identity, ring_time=30, wait_time=60, auto_answer=None):
        self.fake = FakeTelephone(identity, ring_time, wait_time, auto_answer)
        return self.fake

    def phone(self, **kwargs):
        with mock.patch.object(
            RNS.Destination, 'hash_from_name_and_identity', return_value=b'\x33' * 16
        ):
            return LxstTelephony(
                self.identity,
                telephone_factory=self.factory,
                on_incoming=lambda call: self.events.append(('incoming', call.state.value)),
                on_state=lambda call: self.events.append(('state', call.state.value)),
                on_ended=lambda call: self.events.append(('ended', call.state.value)),
                **kwargs,
            )

    def test_call_and_state_callbacks(self):
        phone = self.phone(announce_on_start=True)
        self.assertEqual(self.fake.announces, 1)
        call = phone.call(self.remote)
        self.assertEqual(call.direction.value, 'outgoing')
        self.assertEqual(call.state, CallState.CALLING)
        self.fake.ring(self.remote)
        self.assertEqual(phone.active_call.state, CallState.RINGING)
        self.fake.establish(self.remote)
        self.assertEqual(phone.active_call.state, CallState.CONNECTED)
        self.assertTrue(phone.active_call.was_connected)
        self.fake.end(self.remote)
        self.assertEqual(phone.active_call, None)
        self.assertEqual(phone.last_call.state, CallState.ENDED)
        self.assertIsNotNone(phone.last_call.duration)
        self.assertIn(('incoming', 'ringing'), self.events)
        self.assertIn(('ended', 'ended'), self.events)

    def test_answer_reject_mute_and_busy(self):
        phone = self.phone()
        self.fake.ring(self.remote)
        self.assertTrue(phone.answer())
        self.assertEqual(self.fake.answers, [self.remote])
        phone.mute_transmit(True)
        phone.mute_receive(True)
        self.assertTrue(self.fake.muted_tx)
        self.assertTrue(self.fake.muted_rx)
        phone.reject()
        self.assertEqual(self.fake.hangups, 1)

        phone.call(self.remote)
        with self.assertRaises(CallBusyError):
            phone.call(self.remote)

    def test_unknown_identity(self):
        phone = self.phone()
        with self.assertRaises(NoIdentityError):
            phone.call_hash('aa' * 16, wait=0)

    def test_peer_directory(self):
        directory = PeerDirectory()
        seen = []
        directory._on_peer = seen.append
        directory.received_announce(b'\x44' * 16, self.remote, b'')
        peers = directory.list_peers()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0].identity_hash, self.remote.hash.hex())
        self.assertEqual(directory.get(self.remote).identity, self.remote)
        self.assertEqual(len(seen), 1)
        directory.clear()
        self.assertEqual(directory.list_peers(), [])

    def test_profile_and_model_serialization(self):
        phone = self.phone()
        phone.set_profile(Profiles.QUALITY_HIGH)
        call = phone.call(self.remote)
        self.assertEqual(call.to_dict()['direction'], 'outgoing')
        self.assertIn('state', call.to_dict())


if __name__ == '__main__':
    unittest.main()
