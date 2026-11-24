"""Command line client for lxst_telephony."""

import argparse
import os
import time

import RNS
from LXST.Primitives.Telephony import Profiles

from .core import LxstTelephony
from .native import NativeAudio


PROFILES = {
    "medium": Profiles.QUALITY_MEDIUM,
    "high": Profiles.QUALITY_HIGH,
    "max": Profiles.QUALITY_MAX,
    "low-bandwidth": Profiles.BANDWIDTH_LOW,
    "very-low-bandwidth": Profiles.BANDWIDTH_VERY_LOW,
    "ultra-low-bandwidth": Profiles.BANDWIDTH_ULTRA_LOW,
    "low-latency": Profiles.LATENCY_LOW,
    "ultra-low-latency": Profiles.LATENCY_ULTRA_LOW,
}


def _load_identity(path):
    path = os.path.expanduser(path)
    if os.path.isfile(path):
        identity = RNS.Identity.from_file(path)
        if identity:
            return identity
    os.makedirs(os.path.dirname(path), exist_ok=True)
    identity = RNS.Identity()
    identity.to_file(path)
    return identity


def _add_audio_args(parser):
    parser.add_argument("--speaker", default=None)
    parser.add_argument("--microphone", default=None)
    parser.add_argument("--ringer", default=None)
    parser.add_argument("--low-latency", action="store_true")
    parser.add_argument("--no-agc", action="store_true")
    parser.add_argument("--no-bandpass", action="store_true")
    parser.add_argument("--no-echo-cancel", action="store_true")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="medium")


def _phone(args):
    identity = _load_identity(args.identity)
    if args.rns_config:
        RNS.Reticulum(configdir=args.rns_config)
    else:
        RNS.Reticulum()
    return LxstTelephony(
        identity,
        profile=PROFILES[args.profile],
        auto_answer=args.auto_answer,
        speaker=args.speaker,
        microphone=args.microphone,
        ringer=args.ringer,
        low_latency=args.low_latency,
        agc=not args.no_agc,
        bandpass=not args.no_bandpass,
        echo_cancellation=not args.no_echo_cancel,
        on_incoming=lambda call: print(f"Incoming call: {call.peer_hash}", flush=True),
        on_state=lambda call: print(f"Call {call.peer_hash}: {call.state.value}", flush=True),
        on_ended=lambda call: print(f"Call ended: {call.peer_hash}", flush=True),
        on_busy=lambda call: print(f"Peer busy: {call.peer_hash}", flush=True),
        on_rejected=lambda call: print(f"Call rejected: {call.peer_hash}", flush=True),
    )


def _cmd_devices(args):
    print("Inputs:")
    for device in NativeAudio.inputs():
        print(f"  {device}")
    print("Outputs:")
    for device in NativeAudio.outputs():
        print(f"  {device}")


def _cmd_serve(args):
    phone = _phone(args)
    try:
        phone.start()
        phone.announce()
        print(f"LXST number: {phone.identity.hash.hex()}", flush=True)
        print("Listening for calls. Press Ctrl+C to stop.", flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        phone.stop()


def _cmd_peers(args):
    phone = _phone(args)
    try:
        phone.start()
        print("Waiting for lxst.telephony announces...", flush=True)
        time.sleep(args.wait)
        for peer in phone.peers.list_peers():
            print(f"{peer.identity_hash}  {peer.destination_hash}", flush=True)
    finally:
        phone.stop()


def _cmd_call(args):
    phone = _phone(args)
    try:
        phone.start()
        if args.peer_identity:
            remote_identity = RNS.Identity.from_file(os.path.expanduser(args.peer_identity))
            call = phone.call(remote_identity)
        else:
            call = phone.call_hash(args.peer, wait=args.wait)
        print(f"Calling {call.peer_hash}...", flush=True)
        while phone.active_call:
            time.sleep(0.25)
        if call.was_connected:
            duration = call.duration or 0
            print(f"Call ended after {duration:.1f}s", flush=True)
            return 0
        print(f"Call failed: {call.state.value}", flush=True)
        return 1
    finally:
        phone.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description="LXST peer-to-peer telephony")
    parser.add_argument("--identity", default="~/.lxst_telephony/identity")
    parser.add_argument("--rns-config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    devices = sub.add_parser("devices")
    devices.set_defaults(func=_cmd_devices)

    serve = sub.add_parser("serve")
    _add_audio_args(serve)
    serve.add_argument("--auto-answer", type=float, default=None)
    serve.set_defaults(func=_cmd_serve)

    peers = sub.add_parser("peers")
    _add_audio_args(peers)
    peers.add_argument("--wait", type=float, default=30)
    peers.set_defaults(func=_cmd_peers)

    call = sub.add_parser("call")
    _add_audio_args(call)
    call.add_argument("peer")
    call.add_argument("--wait", type=float, default=15)
    call.add_argument("--peer-identity", default=None,
                      help="path to the remote identity file (for local/lab calls)")
    call.set_defaults(func=_cmd_call)

    args = parser.parse_args(argv)
    if args.command != "devices":
        if not hasattr(args, "auto_answer"):
            args.auto_answer = None
    return args.func(args)


if __name__ == "__main__":
    main()
