# lxst-telephony

Importable peer-to-peer LXST telephony endpoint for Reticulum.

This package is intentionally independent from the Messenger and from Flask.
It exposes a small event-driven API around the standard `lxst.telephony`
aspect, so other programs can use it directly.

## Status

- peer-to-peer calls
- incoming and outgoing calls
- answer, reject, hangup, busy
- Opus and Codec2 profiles
- allow/block lists
- announce discovery for `lxst.telephony`
- native audio devices

Not implemented yet:

- browser audio bridge
- group rooms / Partyline room protocol
- call recording

## Installation

```bash
cd lxst-telephony
pip install -e .
```

## API

The caller must initialize Reticulum before creating the endpoint:

```python
import RNS
RNS.Reticulum()
```

```python
from lxst_telephony import LxstTelephony, CallState

def on_incoming(call):
    print("Incoming call from", call.peer_hash)

def on_state(call):
    print("State:", call.state)

phone = LxstTelephony(
    identity,
    on_incoming=on_incoming,
    on_state=on_state,
)
phone.start()
phone.call(remote_identity)
phone.answer()
phone.hangup()
phone.stop()
```

## CLI

```bash
lxst-telephony devices
lxst-telephony serve
lxst-telephony peers --wait 30
lxst-telephony call <identity-hash>   # phone/node identity hash, not an LXMF delivery hash
lxst-telephony call <identity-hash> --peer-identity ~/.rnphone/identity  # local/lab call
```

## Compatibility

The endpoint uses the standard LXST telephony aspect and signalling, so it is
designed to interoperate with:

- Sideband
- rnphone
- standard LXST clients
- Partyline dial-in, when enabled on the Partyline server

The complete Partyline room protocol is not implemented here.

## License notice

This project uses LXST as an external dependency. LXST is distributed under
the **CC BY-NC-ND 4.0** license: free for non-commercial, personal and
humanitarian use, with no derivatives without explicit permission.

This package is therefore intended for non-commercial use. See
[`THIRD_PARTY_LICENSES/LXST.txt`](THIRD_PARTY_LICENSES/LXST.txt).
