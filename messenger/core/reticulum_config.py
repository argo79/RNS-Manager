#!/usr/bin/env python3
"""Safe helpers for reading and editing the local Reticulum config.

Only interface metadata is exposed to the web UI. Secrets such as IFAC
passphrases and network identities are never returned.
"""

import os
import re
import shutil
import stat
import tempfile

import RNS
from RNS.vendor.configobj import ConfigObj


DEFAULT_CONFIG_PATH = "~/.reticulum/config"
BACKUP_SUFFIX = ".rns_manager.bak"
SAFE_INTERFACE_FIELDS = (
    "port",
    "frequency",
    "bandwidth",
    "spreadingfactor",
    "codingrate",
    "txpower",
    "channel",
    "modulation",
    "discovery_name",
    "reachable_on",
    "target_host",
    "target_port",
    "listen_ip",
    "listen_port",
    "speed",
    "peers",
    "connectable",
    "device",
)
INT_RANGES = {
    "frequency": (137000000, 3000000000),
    "bandwidth": (1, 1000000000),
    "spreadingfactor": (7, 12),
    "codingrate": (5, 8),
    "txpower": (0, 30),
    "listen_port": (1, 65535),
    "target_port": (1, 65535),
    "speed": (1, 10000000),
}

INTERFACE_SCHEMAS = {
    "RNodeInterface": {
        "fields": ("port", "frequency", "bandwidth", "spreadingfactor", "codingrate", "txpower"),
        "defaults": {"txpower": "17"},
        "required": ("port", "frequency", "bandwidth", "spreadingfactor", "codingrate"),
        "match": ("frequency", "bandwidth", "spreadingfactor", "codingrate"),
    },
    "BackboneClientInterface": {
        "fields": ("target_host", "target_port"),
        "defaults": {"target_port": "4242"},
        "required": ("target_host",),
        "match": ("target_host", "target_port"),
    },
    "TCPClientInterface": {
        "fields": ("target_host", "target_port"),
        "defaults": {"target_port": "4242"},
        "required": ("target_host",),
        "match": ("target_host", "target_port"),
    },
    "KISSInterface": {
        "fields": ("port", "speed"),
        "defaults": {"speed": "9600"},
        "required": ("port",),
        "match": ("port",),
    },
    "WeaveInterface": {
        "fields": ("port", "speed"),
        "defaults": {"speed": "3000000"},
        "required": ("port",),
        "match": ("port",),
    },
    "I2PInterface": {
        "fields": ("peers", "connectable"),
        "defaults": {"connectable": "no"},
        "required": ("peers",),
        "match": ("peers",),
    },
}


class ReticulumConfigError(Exception):
    """An expected, user-facing config error."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def config_path():
    """Return the config path used by the running Reticulum instance."""
    instance = None
    try:
        instance = RNS.Reticulum.get_instance()
    except Exception:
        instance = None
    path = getattr(instance, "configpath", None) if instance else None
    if not path:
        path = getattr(RNS.Reticulum, "configpath", None)
    if not path:
        path = DEFAULT_CONFIG_PATH
    return os.path.abspath(os.path.expanduser(str(path)))


def _read_config(path=None):
    path = os.path.abspath(os.path.expanduser(path or config_path()))
    if not os.path.isfile(path):
        raise ReticulumConfigError(
            f"Config Reticulum non trovato: {path}", status=404
        )
    try:
        return ConfigObj(path, encoding="utf-8"), path
    except Exception as exc:
        raise ReticulumConfigError(
            f"Impossibile leggere il config Reticulum: {exc}", status=500
        )


def _as_text(value, limit=160):
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()
    return text[:limit]


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _as_int(value, key, required=False, default=None):
    if value is None or str(value).strip() == "":
        if required:
            raise ReticulumConfigError(f"Campo obbligatorio: {key}")
        return default
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        raise ReticulumConfigError(f"Valore non valido per {key}")
    lo, hi = INT_RANGES[key]
    if not (lo <= number <= hi):
        raise ReticulumConfigError(f"{key} fuori range ({lo}-{hi})")
    return number


def _section_name(value):
    text = _as_text(value, limit=64)
    text = re.sub(r"[\x00-\x1f\x7f\[\]]", "", text).strip()
    if not text:
        text = "RNode Interface"
    return text


def _interface_enabled(section):
    if "interface_enabled" in section:
        return _as_bool(section.get("interface_enabled"))
    if "enabled" in section:
        return _as_bool(section.get("enabled"))
    return False


def _interface_summary(name, section):
    if not hasattr(section, "keys"):
        return None
    entry = {
        "name": str(name),
        "type": _as_text(section.get("type"), 64),
        "enabled": _interface_enabled(section),
    }
    for field in SAFE_INTERFACE_FIELDS:
        if field in section:
            entry[field] = _as_text(section.get(field), 200)
    return entry


def list_interfaces(path=None):
    """Return safe summaries of configured Reticulum interfaces."""
    config, path = _read_config(path)
    interfaces = config.get("interfaces", {}) or {}
    result = []
    for name, section in interfaces.items():
        entry = _interface_summary(name, section)
        if entry:
            result.append(entry)
    return {"config_path": path, "interfaces": result}


def _find_interface(config, name=None, interface_type=None,
                    discovery_name=None, match=None):
    interfaces = config.get("interfaces", {}) or {}
    name = str(name or "").strip()
    discovery_name = str(discovery_name or "").strip()
    match = match or {}

    for section_name, section in interfaces.items():
        entry = _interface_summary(section_name, section)
        if not entry:
            continue
        if name and entry["name"] == name:
            return section_name, entry

    for section_name, section in interfaces.items():
        entry = _interface_summary(section_name, section)
        if not entry:
            continue
        if interface_type and entry.get("type") != interface_type:
            continue
        if discovery_name and entry.get("discovery_name") == discovery_name:
            return section_name, entry
        if match and entry.get("type") == interface_type:
            if all(str(entry.get(key, "")) == str(value) for key, value in match.items()):
                return section_name, entry
    return None, None


def _clean_field(key, value):
    if key in INT_RANGES:
        return str(_as_int(value, key, required=True))
    if key == "connectable":
        return "yes" if _as_bool(value) else "no"
    text = _as_text(value, 200)
    if key in ("target_host", "listen_ip", "peers", "port", "device") and not text:
        raise ReticulumConfigError(f"Campo obbligatorio: {key}")
    if key == "peers" and re.search(r"[\x00-\x1f]", text):
        raise ReticulumConfigError("Elenco peers non valido")
    return text


def _default_section_name(interface_type, discovery_name=""):
    prefixes = {
        "RNodeInterface": "RNode",
        "BackboneClientInterface": "Backbone Client",
        "TCPClientInterface": "TCP Client",
        "KISSInterface": "KISS",
        "WeaveInterface": "Weave",
        "I2PInterface": "I2P",
    }
    prefix = prefixes.get(interface_type, interface_type.replace("Interface", "") or "Interface")
    suffix = _as_text(discovery_name, 48) or "Interface"
    return _section_name(f"{prefix} {suffix}")


def add_interface(data, path=None):
    """Add a supported Reticulum interface from discovered metadata."""
    if not isinstance(data, dict):
        raise ReticulumConfigError("Dati interfaccia non validi")
    interface_type = str(data.get("type") or "").strip()
    schema = INTERFACE_SCHEMAS.get(interface_type)
    if schema is None:
        raise ReticulumConfigError(
            f"Aggiunta automatica non supportata per {interface_type or 'questo tipo'}"
        )

    discovery_name = _as_text(data.get("discovery_name"), 160)
    name = _section_name(
        data.get("name") or _default_section_name(interface_type, discovery_name)
    )
    section = {
        "type": interface_type,
        "interface_enabled": "yes",
    }
    for key, value in schema.get("defaults", {}).items():
        section[key] = str(value)

    for key in schema.get("fields", ()):
        if key in data and str(data.get(key)).strip() != "":
            section[key] = _clean_field(key, data.get(key))

    for key in schema.get("required", ()):
        if not str(section.get(key, "")).strip():
            raise ReticulumConfigError(f"Campo obbligatorio: {key}")

    if discovery_name:
        section["discovery_name"] = discovery_name

    config, path = _read_config(path)
    if "interfaces" not in config or not hasattr(config["interfaces"], "keys"):
        config["interfaces"] = {}

    match = {}
    for key in schema.get("match", ()):
        value = section.get(key, "")
        if str(value).strip():
            match[key] = value
    existing_name, existing = _find_interface(
        config,
        name=name,
        interface_type=interface_type,
        discovery_name=discovery_name,
        match=match,
    )
    if existing:
        return {
            "created": False,
            "requires_restart": True,
            "config_path": path,
            "interface": existing,
            "message": f"Interfaccia già presente: {existing_name}",
        }

    config["interfaces"][name] = section
    _write_config(config, path)
    return {
        "created": True,
        "requires_restart": True,
        "config_path": path,
        "backup_path": path + BACKUP_SUFFIX,
        "interface": _interface_summary(name, config["interfaces"][name]),
    }


def add_rnode_interface(data, path=None):
    """Backward-compatible RNode-only wrapper."""
    if isinstance(data, dict) and not data.get("type"):
        data = dict(data)
        data["type"] = "RNodeInterface"
    return add_interface(data, path=path)


def set_interface_enabled(name, enabled, path=None, expected_type=None):
    """Enable or disable a configured interface with one atomic write."""
    name = str(name or "").strip()
    if not name:
        raise ReticulumConfigError("Nome interfaccia mancante")
    config, path = _read_config(path)
    section_name, entry = _find_interface(config, name=name)
    if not entry:
        raise ReticulumConfigError(
            f"Interfaccia non trovata nel config: {name}", status=404
        )
    if expected_type and entry.get("type") != expected_type:
        raise ReticulumConfigError("Tipo interfaccia non corrispondente", status=409)

    section = config["interfaces"][section_name]
    state = "yes" if enabled else "no"
    if "interface_enabled" in section:
        section["interface_enabled"] = state
    if "enabled" in section:
        section["enabled"] = state
    if "interface_enabled" not in section and "enabled" not in section:
        section["interface_enabled"] = state

    _write_config(config, path)
    return {
        "enabled": bool(enabled),
        "disabled": not bool(enabled),
        "requires_restart": True,
        "config_path": path,
        "backup_path": path + BACKUP_SUFFIX,
        "interface": _interface_summary(section_name, section),
    }


def enable_interface(name, path=None, expected_type=None):
    """Enable a configured interface."""
    return set_interface_enabled(
        name, True, path=path, expected_type=expected_type
    )


def disable_interface(name, path=None, expected_type=None):
    """Disable a configured interface without touching unrelated settings."""
    return set_interface_enabled(
        name, False, path=path, expected_type=expected_type
    )



def _write_config(config, path):
    """Write config through a temp file and keep a backup of the original."""
    directory = os.path.dirname(path) or "."
    mode = stat.S_IMODE(os.stat(path).st_mode)
    backup = path + BACKUP_SUFFIX
    shutil.copy2(path, backup)

    fd, tmp_path = tempfile.mkstemp(prefix=".config.", dir=directory)
    os.close(fd)
    try:
        config.filename = tmp_path
        config.write()
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
