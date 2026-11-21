#!/usr/bin/env python3
"""
Testa TUTTE le combinazioni di aspect in-process, con tutti i separatori.
Recupera la chiave pubblica UNA VOLTA con rnid, poi calcola tutti gli hash localmente.
Uso: python3 check_aspect.py <identity_hash_hex> <dest_hash_hex> [--verbose]
"""

import sys
import subprocess
import shutil
import re
import itertools
import RNS

# === NAMESPACE / APP_NAME ===
NAMESPACES = [
    # Qortal (tutte le versioni)
    "qortal-hub-v1", "qortal-hub-v2", "qortal-hub-v3", "qortal-hub-v4",
    "qortal-hub", "qortal",
    "qortal.presence", "qortal-masque",
    "qhub", "hub",
    # Reticulum generici
    "presence", "reticulum", "presence-reticulum",
    "masque", "relay", "bridge",
    "node", "peer", "session", "instance",
    # XPRS
    "xprs", "xprs.node", "xprs.peer", "xprs.chat", "xprs.delivery", "xprs.presence",
    # Vappnet / PTT
    "vappnet", "vapp", "vappnet.ptt", "vappnet.node", "vappnet.chat", "vappnet.direct",
    # Berlin Mesh
    "berlinmesh", "berlin-mesh", "berlin", "mesh", "meshbus", "meshbus.v1",
    # Map / Data
    "map-data", "map-data-v1", "maps", "map",
    # Sofia / Wardrive
    "rns", "sofia", "rns.sofia", "wardrive",
    # Trail Mate
    "trail-mate", "trailmate", "trail",
    # Altre app
    "fuxxxchat", "fuxxx", "reticulumpi", "reticulum-pi",
    "qan", "qan1", "rwebserver", "meshage", "partyline",
    "starlink", "ttp", "lxst", "voice", "radio",
    "estonia", "public", "discovery",
    "relay", "transport", "endpoint", "metrics", "health",
    "mesh", "local", "peer", "session", "tunnel",
]

# === ASPECT / SECONDA PARTE ===
ASPECTS = [
    # Presenza / Qortal
    "presence", "presence-full", "presence.full", "presence.node", "presence.bridge",
    "presence.reticulum", "presence-reticulum", "presence.v3", "presence.v4",
    # Generici
    "bridge", "node", "peer", "hub", "relay", "masque", "masque.relay",
    "qortal", "reticulum", "session", "instance", "client", "server", "service",
    "status", "info", "announce", "monitor", "telemetry",
    # Nuovi indizi
    "ptt", "push-to-talk", "voice", "call", "radio", "audio",
    "directory", "public", "wardrive", "discovery",
    "git", "chat", "delivery", "message", "file", "transfer",
    "maps", "map", "data", "route", "path",
    "tcp", "endpoint", "health", "metrics", "telemetry",
    "local", "mesh", "discovery", "peer", "status",
    "handshake", "session", "tunnel", "server",
    # Versioni come aspect
    "v1", "v2", "v3", "v4", "v5", "1", "2", "3", "4", "5",
]

# === VERSIONI ===
VERSIONS = ["", "1", "v1", "2", "v2", "3", "v3", "4", "v4", "5", "v5", "0", "v0"]

# === SUB-ASPECTS (per combinazioni a 4 parti) ===
SUB_ASPECTS = [
    "direct", "group", "public", "private", "chat", "node",
    "peer", "client", "server", "service", "relay", "bridge",
    "v1", "v2", "v3", "v4", "1", "2", "3", "4",
]

# === PATTERN SPECIALI (stringhe esatte osservate) ===
SPECIAL_PATTERNS = [
    "map-data-v1",
    "meshbus.v1",
    "qortal-hub-v1.presence.v1",
    "qortal-hub-v2.presence.v2",
    "qortal-hub-v3.presence.v3",
    "qortal-hub-v4.presence.v4",
    "qortal-hub-v3.manual-smoke",
    "qortal-hub-v3.community-stun.v1","qortal-hub-v3.community-masque-relay.v1",
    "rns.sofia",
    "vappnet.ptt",
    "xprs",
    "xprs.node",
    "xprs.delivery",
    "xprs.chat","aurora.test","aurora.echo","aurora.resorce",
    "aurora.files",
    "berlinmesh",
    "berlin-mesh",
    "qortal-v1", "qortal-v2", "qortal-v3", "qortal-v4",
    "qortal-masque-relay",
    "masque.relay",
    "qortal.masque.relay",
    "qortal-masque",
    "reticulum_console.echo",
    "lxst.room",
    "rns.moscow",
]

def get_public_key(identity_hash):
    rnid = shutil.which('rnid') or 'rnid'
    cmd = [rnid, '-R', '-i', identity_hash, '-x', '-F']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        match = re.search(r'\b([0-9a-fA-F]{128})\b', result.stdout)
        if match:
            return match.group(1)
        return None
    except Exception as e:
        print(f"⚠️ Errore rnid: {e}")
        return None

def compute_dest_hash(identity, parts):
    try:
        clean_parts = [p for p in parts if p]
        dest = RNS.Destination(
            identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            *clean_parts
        )
        return dest.hash.hex()
    except Exception:
        return None

def test_one(identity, aspect_str, target, counter, total, verbose=True):
    """
    Testa una stringa in PIÙ interpretazioni:
    1) Stringa intera come singola parte
    2) Splittata su '.' (se contiene '.')
    3) Splittata su '-' (se contiene '-')
    4) Splittata su entrambi '.' e '-' (se contiene entrambi)
    """
    candidates = []
    
    # Interpretazione 1: stringa intera come singola parte
    candidates.append([aspect_str])
    
    # Interpretazione 2: split su '.'
    if '.' in aspect_str:
        candidates.append(aspect_str.split('.'))
    
    # Interpretazione 3: split su '-'
    if '-' in aspect_str:
        candidates.append(aspect_str.split('-'))
    
    # Interpretazione 4: split su entrambi
    if '.' in aspect_str and '-' in aspect_str:
        parts = re.split(r'[.\-]', aspect_str)
        if parts not in candidates:
            candidates.append(parts)
    
    # Interpretazione 5: split su '_' se contiene '_'
    if '_' in aspect_str:
        candidates.append(aspect_str.split('_'))
    
    for parts in candidates:
        computed = compute_dest_hash(identity, parts)
        if computed == target:
            if verbose:
                print(f"  [{counter:6d}/{total}] {aspect_str:<55} → {computed} ✅ MATCH! (parts: {parts})")
            return True
    
    if verbose:
        computed = compute_dest_hash(identity, [aspect_str])
        hash_str = computed if computed else "ERRORE"
        print(f"  [{counter:6d}/{total}] {aspect_str:<55} → {hash_str} ❌")
    
    return False

def main():
    if len(sys.argv) < 3:
        print("Uso: python3 check_aspect.py <identity_hash_hex> <dest_hash_hex> [--verbose]")
        sys.exit(1)
    
    identity_hash = sys.argv[1]
    target = sys.argv[2].lower().strip()
    verbose = "--verbose" in sys.argv
    
    print("=" * 120)
    print(f"🔍 Identity: {identity_hash}")
    print(f"🎯 Target:   {target}")
    print("=" * 120)
    print()
    
    print("⏳ Recupero chiave pubblica con rnid -R -x -F...")
    pubkey_hex = get_public_key(identity_hash)
    if pubkey_hex is None:
        print("❌ Impossibile recuperare la chiave pubblica.")
        sys.exit(1)
    print(f"✅ Chiave pubblica: {pubkey_hex[:32]}...")
    
    try:
        identity = RNS.Identity(create_keys=False)
        identity.load_public_key(bytes.fromhex(pubkey_hex))
        print(f"✅ Identità ricostruita: {identity.hash.hex()}")
    except Exception as e:
        print(f"❌ Errore ricostruzione identità: {e}")
        sys.exit(1)
    print()
    
    matches = []
    
    # FASE 1: pattern speciali
    print("=" * 120)
    print(f"🎯 FASE 1: Test {len(SPECIAL_PATTERNS)} pattern speciali")
    print("=" * 120)
    for i, pattern in enumerate(SPECIAL_PATTERNS, 1):
        if test_one(identity, pattern, target, i, len(SPECIAL_PATTERNS), verbose=True):
            matches.append(pattern)
    
    if matches:
        print()
        print(f"✅ TROVATO NEI PATTERN SPECIALI! Aspect: {', '.join(matches)}")
        return
    
    print()
    print("Nessun match nei pattern speciali. Passo alle combinazioni generate...")
    print()
    
    # FASE 2: genera TUTTE le combinazioni in TUTTI i formati
    print("📋 Generazione combinazioni (tutti i formati)...")
    combinations = set()
    
    # Separatori da usare
    SEPARATORS = [".", "-", "_", ""]
    
    # 1 parte: solo aspect
    for asp in ASPECTS:
        combinations.add(asp)
    
    # 2 parti: namespace + aspect (tutti i separatori)
    for ns, asp in itertools.product(NAMESPACES, ASPECTS):
        for sep in SEPARATORS:
            combinations.add(f"{ns}{sep}{asp}")
    
    # 3 parti: namespace + aspect + version (tutti i separatori)
    for ns, asp, ver in itertools.product(NAMESPACES, ASPECTS, VERSIONS):
        parts = [p for p in (ns, asp, ver) if p]
        if len(parts) >= 2:
            for sep in SEPARATORS:
                combinations.add(sep.join(parts))
    
    # 2 parti: namespace + version (es. map-data-v1, qortal-v1)
    for ns, ver in itertools.product(NAMESPACES, VERSIONS):
        if ver:
            for sep in SEPARATORS:
                combinations.add(f"{ns}{sep}{ver}")
    
    # 4 parti: namespace + aspect + subaspect + version (tutti i separatori)
    for ns, asp, sub, ver in itertools.product(NAMESPACES, ASPECTS, SUB_ASPECTS, VERSIONS):
        parts = [p for p in (ns, asp, sub, ver) if p]
        if len(parts) >= 2:
            for sep in SEPARATORS:
                combinations.add(sep.join(parts))
    
    # Varianti invertite (version.namespace.aspect)
    for ns in NAMESPACES:
        for asp in ASPECTS:
            for ver in ["v1", "v2", "v3", "v4", "1", "2", "3", "4"]:
                combinations.add(f"{ns}.{ver}.{asp}")
                combinations.add(f"{ns}-{ver}-{asp}")
                combinations.add(f"{ver}.{ns}.{asp}")
                combinations.add(f"{ver}-{ns}-{asp}")
    
    # Rimuovi quelle già testate come speciali
    combinations -= set(SPECIAL_PATTERNS)
    
    combinations = sorted(combinations)
    total = len(combinations)
    print(f"📋 Combinazioni totali: {total}")
    print("=" * 120)
    print(f"🎯 FASE 2: Test {total} combinazioni generate")
    print("=" * 120)
    print()
    
    for i, aspect_str in enumerate(combinations, 1):
        if test_one(identity, aspect_str, target, i, total, verbose=verbose):
            matches.append(aspect_str)
        elif i % 1000 == 0 and not verbose:
            print(f"  [{i}/{total}] testati...")
    
    print()
    print("=" * 120)
    if matches:
        print(f"✅ TROVATO! Aspect: {', '.join(matches)}")
    else:
        print("❌ Nessuna combinazione corrisponde.")
    print("=" * 120)

if __name__ == "__main__":
    main()