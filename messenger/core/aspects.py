#!/usr/bin/env python3
"""Helpers for Reticulum announce aspects.

Only a small subset of Reticulum announcements can be represented directly on
the map.  In particular, ``rnstransport.discovery.interface`` announcements
carry an optional latitude/longitude/height triplet in their app_data.
"""

import re

import RNS
from RNS.vendor import umsgpack

try:
    from RNS.Stamps import STAMP_SIZE
except Exception:  # pragma: no cover - old RNS releases
    STAMP_SIZE = 32

try:
    from RNS.Discovery import (
        BANDWIDTH,
        CHANNEL,
        CODINGRATE,
        FREQUENCY,
        HEIGHT,
        INTERFACE_TYPE,
        LATITUDE,
        LONGITUDE,
        MODULATION,
        NAME,
        OP_ADDR,
        PORT,
        PR_COST,
        REACHABLE_ON,
        SPREADINGFACTOR,
        TRANSPORT,
        TRANSPORT_ID,
        TRANSPORT_IMPL,
        TRANSPORT_VERS,
        DST_COST,
    )
except Exception:  # pragma: no cover - keep the module importable
    BANDWIDTH = 0x0A
    CHANNEL = 0x0E
    CODINGRATE = 0x0C
    DST_COST = 0xFB
    FREQUENCY = 0x09
    HEIGHT = 0x05
    INTERFACE_TYPE = 0x00
    LATITUDE = 0x03
    LONGITUDE = 0x04
    MODULATION = 0x0D
    NAME = 0xFF
    OP_ADDR = 0xF0
    PORT = 0x06
    PR_COST = 0xFA
    REACHABLE_ON = 0x02
    SPREADINGFACTOR = 0x0B
    TRANSPORT = 0x01
    TRANSPORT_ID = 0xFE
    TRANSPORT_IMPL = 0xFD
    TRANSPORT_VERS = 0xFC

FLAG_ENCRYPTED = 0b00000010
DISCOVERY_ASPECT = 'rnstransport.discovery.interface'
LEGACY_DISCOVERY_ASPECT = 'discovery.interface'
DISCOVERY_ASPECTS = {DISCOVERY_ASPECT, LEGACY_DISCOVERY_ASPECT}
DEFAULT_ANNOUNCE_ASPECTS = [
    'lxmf.delivery',
    'lxmf.propagation',
    'lxst.telephony',
    DISCOVERY_ASPECT,
]
ASPECT_LABELS = {
    'lxmf.delivery': 'LXMF · Delivery',
    'lxmf.propagation': 'LXMF · Propagation',
    'lxst.telephony': 'LXST · Telephony',
    DISCOVERY_ASPECT: 'Reticulum · Interface Discovery',
    LEGACY_DISCOVERY_ASPECT: 'Reticulum · Interface Discovery (legacy)',
}
ASPECT_RE = re.compile(r'^[a-z0-9_.-]{1,64}$')
_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]+')
MAX_ANNOUNCE_ASPECTS = 32


def normalize_announce_aspects(value, ensure_delivery=False, strict=False,
                               max_items=MAX_ANNOUNCE_ASPECTS):
    """Return a deduplicated, normalized list of announce aspect names.

    ``strict`` is used by the configuration endpoint: invalid names raise a
    ValueError instead of being silently ignored.
    """
    if isinstance(value, str):
        items = value.split(',')
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    elif value is None:
        items = []
    else:
        if strict:
            raise ValueError('announce_aspects non valido')
        items = []

    aspects = []
    for item in items:
        aspect = str(item or '').strip().lower()
        if not aspect:
            continue
        if not ASPECT_RE.fullmatch(aspect):
            if strict:
                raise ValueError(f'Aspect non valido: {aspect}')
            continue
        if aspect not in aspects:
            aspects.append(aspect)

    if len(aspects) > max_items:
        if strict:
            raise ValueError(f'Troppi aspect (massimo {max_items})')
        aspects = aspects[:max_items]

    if ensure_delivery and 'lxmf.delivery' not in aspects:
        aspects.insert(0, 'lxmf.delivery')

    return aspects


def is_map_capable_aspect(aspect):
    return str(aspect or '').strip().lower() in DISCOVERY_ASPECTS


def is_discovery_aspect(aspect):
    return str(aspect or '').strip().lower() in DISCOVERY_ASPECTS


def _clean_text(value, fallback=None):
    if value is None:
        return fallback
    if isinstance(value, bytes):
        value = value.decode('utf-8', errors='ignore')
    if not isinstance(value, str):
        value = str(value)
    value = _CONTROL_RE.sub('', value).strip()
    return value[:96] if value else fallback


def _as_float(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _hex_bytes(value):
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, str):
        value = value.strip().lower()
        if re.fullmatch(r'[0-9a-f]+', value):
            return value
    return None


def parse_interface_discovery_app_data(app_data):
    """Parse ``rnstransport.discovery.interface`` app_data safely.

    The wire format is::

        [flags][msgpack payload][32 byte stamp]

    ``config_entry`` and IFAC secrets are deliberately not returned.  They are
    not needed by the UI and must never be sent to the browser.
    """
    if not isinstance(app_data, (bytes, bytearray, memoryview)):
        return None
    try:
        raw = bytes(app_data)
    except Exception:
        return None
    if len(raw) <= 1 + STAMP_SIZE:
        return None

    flags = raw[0]
    payload = raw[1:]
    if flags & FLAG_ENCRYPTED:
        try:
            has_network_identity = getattr(RNS.Transport, 'has_network_identity', None)
            network_identity = getattr(RNS.Transport, 'network_identity', None)
            if not has_network_identity or not has_network_identity() or not network_identity:
                return None
            payload = network_identity.decrypt(payload)
            if not payload:
                return None
        except Exception:
            return None

    if len(payload) <= STAMP_SIZE:
        return None
    packed = payload[:-STAMP_SIZE]

    try:
        unpacked = umsgpack.unpackb(packed)
    except Exception:
        return None
    if not isinstance(unpacked, dict) or INTERFACE_TYPE not in unpacked:
        return None

    interface_type = _clean_text(unpacked.get(INTERFACE_TYPE))
    if not interface_type:
        return None

    latitude = _as_float(unpacked.get(LATITUDE))
    longitude = _as_float(unpacked.get(LONGITUDE))
    height = _as_float(unpacked.get(HEIGHT))
    if latitude is not None and not (-90.0 <= latitude <= 90.0):
        return None
    if longitude is not None and not (-180.0 <= longitude <= 180.0):
        return None

    transport = unpacked.get(TRANSPORT)
    if not isinstance(transport, bool):
        return None

    name = _clean_text(unpacked.get(NAME), f'Discovered {interface_type}')
    info = {
        'aspect': DISCOVERY_ASPECT,
        'type': interface_type,
        'name': name,
        'transport': transport,
        'transport_id': _hex_bytes(unpacked.get(TRANSPORT_ID)),
        'implementation': _clean_text(unpacked.get(TRANSPORT_IMPL)),
        'version': _clean_text(unpacked.get(TRANSPORT_VERS)),
        'latitude': latitude,
        'longitude': longitude,
        'height': height,
        'reachable_on': _clean_text(unpacked.get(REACHABLE_ON)),
        'port': _as_int(unpacked.get(PORT)),
        'frequency': _as_int(unpacked.get(FREQUENCY)),
        'bandwidth': _as_int(unpacked.get(BANDWIDTH)),
        'spreading_factor': _as_int(unpacked.get(SPREADINGFACTOR)),
        'coding_rate': _as_int(unpacked.get(CODINGRATE)),
        'channel': _as_int(unpacked.get(CHANNEL)),
        'modulation': _as_int(unpacked.get(MODULATION)),
        'dst_cost': _as_int(unpacked.get(DST_COST)),
        'pr_cost': _as_int(unpacked.get(PR_COST)),
    }

    operator = unpacked.get(OP_ADDR)
    if isinstance(operator, bytes) and len(operator) == RNS.Identity.TRUNCATED_HASHLENGTH // 8:
        info['operator_lxmf_address'] = operator.hex()

    if info['transport_id'] and name:
        try:
            info['discovery_hash'] = RNS.Identity.full_hash(
                (info['transport_id'] + name).encode('utf-8')
            ).hex()
        except Exception:
            pass

    return info
