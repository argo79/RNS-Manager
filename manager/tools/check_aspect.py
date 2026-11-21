#!/usr/bin/env python3
"""
Verifica quale aspect corrisponde a un dest_hash noto, usando rnid.
Uso: python check_aspect.py <identity_hash_hex> <dest_hash_hex>
"""

import sys
import subprocess
import shutil
import re

ASPECT_CANDIDATES = [
    # Presenza / Qortal
    "presence", "presence.full", "presence-full", "presence.node", "presence.bridge",
    "qortal.relay", "qortal.masque", "qortal.presence", "qortal.node",
    "status", "status.node", "status.full",
    # App note
    "xprs.node", "xprs", "microrn.mgmt", "rmsp.maps",

    # Standard RNS/LXMF
#    "lxmf.delivery", "lxmf.propagation",
#    "nomadnetwork.node", "rrc.hub",
#    "lxst.telephony", "call.audio",
#    "rnx.execute", "rnsh", "rncp", "rncp.receive",
#    "rnstransport.probe", "rnstransport.info.blackhole",
#    "rnstransport.network", "rnstransport.remote.management",
#    "rnstransport.path.request", "rnstransport.discovery.interface",
#    "rnstransport.tunnel.synthesize", "rnstransport.network.instance",
    # Altri
    "retibbs.bbs", "styrene.delivery", "r3akt.emergency",
    "haven.status", "atak.cot", "demo.msg",
    "example_utilities.echo.request",
    "example_utilities.broadcast",
    "example_utilities.linkexample",
    "discovery.interface",
]

def compute_hash(identity_hash, aspect, use_request=False, timeout=5):
    """Calcola l'hash per un aspect. Restituisce l'hash o None."""
    rnid = shutil.which('rnid') or 'rnid'
    cmd = [rnid]
    if use_request:
        cmd.append('-R')
    cmd.extend(['-i', identity_hash, '-H', aspect])
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        # Regex robusta: cerca "destination for this Identity is <HASH>"
        match = re.search(r'destination for this Identity is <([0-9a-fA-F]+)>', result.stdout)
        if match:
            return match.group(1).lower()
        return None
        
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

def main():
    if len(sys.argv) < 3:
        print("Uso: python check_aspect.py <identity_hash_hex> <dest_hash_hex>")
        sys.exit(1)
    
    identity_hash = sys.argv[1]
    dest_hash = sys.argv[2].lower().strip()
    
    print(f"🔍 Identity hash: {identity_hash[:32]}...")
    print(f"🎯 Dest hash: {dest_hash}")
    print(f"📋 Candidati: {len(ASPECT_CANDIDATES)}")
    print()
    
    # PRIMA chiamata con -R per popolare la cache
    print("⏳ Prima chiamata con -R per recuperare l'identità dalla rete...")
    first_aspect = ASPECT_CANDIDATES[0]
    first_hash = compute_hash(identity_hash, first_aspect, use_request=True, timeout=20)
    
    if first_hash is None:
        print("❌ Identità non recuperabile dalla rete. Impossibile procedere.")
        sys.exit(1)
    
    print(f"✅ Identità recuperata (hash per '{first_aspect}': {first_hash})")
    print()
    
    matches = []
    
    for i, aspect in enumerate(ASPECT_CANDIDATES, 1):
        # Usa -R solo per la prima chiamata (già fatta), poi senza -R (cache)
        if i == 1:
            computed = first_hash
        else:
            computed = compute_hash(identity_hash, aspect, use_request=False, timeout=5)
        
        print(f"  [{i}/{len(ASPECT_CANDIDATES)}] {aspect}...", end=' ', flush=True)
        
        if computed == dest_hash:
            matches.append(aspect)
            print("✅ MATCH!")
        else:
            print(f"❌ ({computed or 'errore'})")
    
    print()
    if matches:
        print(f"✅ Aspect trovato: {', '.join(matches)}")
        print(f"   Aggiungi questa stringa a RNS_ASPECTS nel tuo monitor.")
    else:
        print("❌ Nessun candidato corrisponde.")
        print("   L'aspect è una stringa personalizzata non presente nella lista.")

if __name__ == "__main__":
    main()