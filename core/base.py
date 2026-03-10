#!/usr/bin/env python3
# core/base.py - Costanti e funzioni condivise tra i moduli

import RNS
import LXMF

# Costanti dei campi LXMF (standard)
FIELD_TELEMETRY        = 0x02
FIELD_TELEMETRY_STREAM = 0x03
FIELD_ICON_APPEARANCE  = 0x04
FIELD_FILE_ATTACHMENTS = 0x05
FIELD_IMAGE            = 0x06
FIELD_AUDIO            = 0x07
FIELD_COMMANDS         = 0x09

class Commands:
    """Codici dei comandi LXMF."""
    TELEMETRY_REQUEST = 0x01
    PING              = 0x02
    ECHO              = 0x03
    SIGNAL_REPORT     = 0x04

def guess_image_format(data):
    """
    Determina il formato di un'immagine dai primi byte (magic number).
    Restituisce una stringa come "JPEG", "PNG", "GIF", "WebP", "BMP" o "Unknown".
    """
    if data.startswith(b'\xff\xd8'):
        return "JPEG"
    elif data.startswith(b'\x89PNG\r\n\x1a\n'):
        return "PNG"
    elif data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):
        return "GIF"
    elif data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return "WebP"
    elif data.startswith(b'BM'):
        return "BMP"
    return "Unknown"