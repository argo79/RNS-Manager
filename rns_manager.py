#!/usr/bin/env python3
import subprocess
import os
import uuid
import tempfile
import shutil
import binascii
import base64
import shlex
import time
import threading
import queue
import json
import re
import multiprocessing
import socket
import signal
import sys
import webbrowser
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, Response, stream_with_context

# Importa il modulo monitor
import modules.rns_monitor as rns_monitor

app = Flask(__name__)

# === PERCORSI CONFIGURATI DALL'UTENTE ===
USER_DIRECTORIES = {
    'reticulum': "~/.reticulum",
    'nomadnet': "~/.nomadnetwork", 
    'lxmf': "~/.lxmf",
    'rnphone': "~/.rnphone",
    'meshchat': "~/.reticulum-meshchat",
    'rns_manager': "~/.rns_manager"
}

# === CODICE ORIGINALE - AUTOMATICO ===
execution_vars = {}
for key, path in USER_DIRECTORIES.items():
    expanded = os.path.expanduser(path)
    execution_vars[f"{key.upper()}_DIR"] = expanded
    
    if key == 'rnphone' or key == 'meshchat':
        execution_vars[f"{key.upper()}_STORAGE"] = expanded if os.path.exists(expanded) else None
    else:
        storage_path = os.path.join(expanded, "storage")
        execution_vars[f"{key.upper()}_STORAGE"] = storage_path
    
    if expanded:
        os.makedirs(expanded, exist_ok=True)
        if key != 'rnphone' and key != 'meshchat':
            os.makedirs(storage_path, exist_ok=True)

globals().update(execution_vars)

# Crea directory per Downloads e Cache
DOWNLOADS_DIR = os.path.expanduser("~/.rns_manager/Downloads")
CACHE_DIR = os.path.expanduser("~/.rns_manager/Cache")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# ============================================
# === INIZIALIZZA MONITOR RNS ===
# ============================================

# Crea istanza del monitor manager
monitor_manager = rns_monitor.RNSMonitorManager(
    socket_path=rns_monitor.SOCKET_PATH,
    aspects=rns_monitor.RNS_ASPECTS,
    cache_dir=CACHE_DIR,
    max_history=2000
)

# Avvia processi
monitor_manager.start_monitor_process()
monitor_manager.start_listener()

# Registra blueprint del monitor
app.register_blueprint(rns_monitor.create_monitor_blueprint(monitor_manager))

# Route di redirect per compatibilità con /monitor
@app.route('/monitor')
def redirect_monitor():
    from flask import redirect
    return redirect('/api/monitor')

# ============================================
# === CACHE IDENTITÀ (server-side) ===
# ============================================

class IdentityCache:
    """Cache server-side per le identità"""
    def __init__(self, cache_duration=300):
        self.cache = {}
        self.timestamps = {}
        self.cache_duration = cache_duration
        self.lock = threading.Lock()
        
    def get(self, key='all_identities'):
        with self.lock:
            if key in self.cache:
                age = time.time() - self.timestamps.get(key, 0)
                if age < self.cache_duration:
                    print(f"[Cache ID] Hit per {key} (età: {age:.1f}s)")
                    return self.cache[key]
                else:
                    print(f"[Cache ID] Scaduta per {key} (età: {age:.1f}s)")
            return None
    
    def set(self, data, key='all_identities'):
        with self.lock:
            self.cache[key] = data
            self.timestamps[key] = time.time()
            print(f"[Cache ID] Salvati {len(data)} elementi per {key}")
    
    def clear(self, key=None):
        with self.lock:
            if key:
                self.cache.pop(key, None)
                self.timestamps.pop(key, None)
                print(f"[Cache ID] Pulito {key}")
            else:
                self.cache.clear()
                self.timestamps.clear()
                print(f"[Cache ID] Pulita tutta la cache")
    
    def get_stats(self, key='all_identities'):
        with self.lock:
            if key in self.cache:
                age = time.time() - self.timestamps[key]
                return {
                    'exists': True,
                    'age': age,
                    'size': len(self.cache[key]),
                    'timestamp': self.timestamps[key]
                }
            return {'exists': False}

# Inizializza cache identità
identity_cache = IdentityCache(cache_duration=3600)

# ============================================
# === ROUTE RESET TOTALE ===
# ============================================

@app.route('/api/monitor/reset-all', methods=['POST'])
def monitor_reset_all():
    try:
        print("\n" + "="*60)
        print("🔄 RESET TOTALE RICHIESTO")
        print("="*60)
        
        monitor_manager.clear_all()
        
        if monitor_manager.announce_cache:
            monitor_manager.announce_cache.clear()
            cache_file = monitor_manager.announce_cache.db_path
            if os.path.exists(cache_file):
                os.remove(cache_file)
                print(f"[✓] File cache eliminato: {cache_file}")
        
        print("="*60)
        print("✅ RESET TOTALE COMPLETATO")
        print("="*60 + "\n")
        
        return jsonify({
            'success': True,
            'message': 'Reset totale completato',
            'announces': 0,
            'identities': 0
        })
        
    except Exception as e:
        print(f"[!] Errore durante reset: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        })

# ============================================
# === TUTTE LE ROUTE IDENTITY MANAGER ===
# ============================================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/rnid', methods=['POST'])
def execute_rnid():
    try:
        data = request.json
        command = data.get('command', '').strip()
        
        if not command:
            return jsonify({'success': False, 'error': 'No command provided'})
        
        if command.startswith('rnid'):
            try:
                parts = shlex.split(command)
            except:
                parts = command.split()
            
            if parts[0] == 'rnid':
                parts = parts[1:]
            
            result = subprocess.run(
                ['rnid'] + parts,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            stdout = result.stdout
            stderr = result.stderr
            
            stdout = re.sub(r'.\x08', '', stdout)
            stdout = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stdout)
            stdout = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', stdout)
            
            if len(stdout) > 1000 or '4U+6vC' in stdout:
                lines = []
                for line in stdout.split('\n'):
                    if len(line) > 80:
                        for i in range(0, len(line), 80):
                            lines.append(line[i:i+80])
                    else:
                        lines.append(line)
                stdout = '\n'.join(lines)
            
            return jsonify({
                'success': result.returncode == 0,
                'output': stdout,
                'error': re.sub(r'.\x08', '', stderr),
                'return_code': result.returncode
            })
            
        elif command.startswith(('rm -f ', 'echo ', 'base64 ', 'cat ', 'stat -c%s ', 'cp ', 'mkdir -p ', 'mv ')):
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10
            )
        else:
            return jsonify({'success': False, 'error': 'Comando non permesso'})
        
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr,
            'return_code': result.returncode
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/list', methods=['GET'])
def list_identities():
    force_refresh = request.args.get('force', 'false').lower() == 'true'
    
    if not force_refresh:
        cached = identity_cache.get()
        if cached is not None:
            return jsonify({
                'identities': cached,
                'from_cache': True,
                'cache_age': time.time() - identity_cache.timestamps.get('all_identities', 0)
            })
    
    print(f"[Cache ID] Scansione completa delle identità (force={force_refresh})")
    identities = []
    
    storage_dirs = [
        (RETICULUM_STORAGE, 'reticulum'),
        (NOMADNET_STORAGE, 'nomadnet'),
        (LXMF_STORAGE, 'lxmf'),
        (RNS_MANAGER_STORAGE, 'rns_manager')
    ]
    
    if RNPHONE_STORAGE and os.path.exists(RNPHONE_STORAGE):
        storage_dirs.append((RNPHONE_STORAGE, 'rnphone'))

    if MESHCHAT_STORAGE and os.path.exists(MESHCHAT_STORAGE):
        storage_dirs.append((MESHCHAT_STORAGE, 'meshchat'))
    
    for storage_path, app_name in storage_dirs:
        if storage_path and os.path.exists(storage_path):
            for item in os.listdir(storage_path):
                item_path = os.path.join(storage_path, item)
                
                if os.path.isfile(item_path):
                    file_size = os.path.getsize(item_path)
                    if file_size == 64:
                        identity = {
                            'name': item,
                            'path': item_path,
                            'app': app_name,
                            'size': file_size,
                            'rns_hash': None,
                            'aspect_hashes': {},
                            'valid': False
                        }
                        
                        try:
                            result = subprocess.run(
                                ['rnid', '-i', item_path, '--print-identity'],
                                capture_output=True,
                                text=True,
                                timeout=3
                            )
                            
                            if result.returncode == 0:
                                identity['valid'] = True
                                
                                for line in result.stdout.split('\n'):
                                    if 'Loaded Identity <' in line:
                                        start = line.find('<') + 1
                                        end = line.find('>', start)
                                        if start > 0 and end > start:
                                            identity['rns_hash'] = line[start:end]
                                            break
                            
                            aspects_to_check = rns_monitor.RNS_ASPECTS[:5]
                            for aspect in aspects_to_check:
                                try:
                                    hash_result = subprocess.run(
                                        ['rnid', '-i', item_path, '-H', aspect],
                                        capture_output=True,
                                        text=True,
                                        timeout=2
                                    )
                                    
                                    if hash_result.returncode == 0:
                                        pattern = f"The {aspect} destination for this Identity is <"
                                        for line in hash_result.stdout.split('\n'):
                                            if pattern in line:
                                                start = line.find('<') + 1
                                                end = line.find('>', start)
                                                if start > 0 and end > start:
                                                    hash_value = line[start:end]
                                                    identity['aspect_hashes'][aspect] = hash_value
                                                    break
                                        
                                        if aspect not in identity['aspect_hashes']:
                                            for line in hash_result.stdout.split('\n'):
                                                if 'Destination hash:' in line:
                                                    hash_value = line.split('Destination hash:')[-1].strip()
                                                    identity['aspect_hashes'][aspect] = hash_value
                                                    break
                                except:
                                    continue
                            
                        except Exception:
                            continue
                        
                        identities.append(identity)
    
    identities.sort(key=lambda x: (not x['valid'], x['name']))
    
    identity_cache.set(identities)
    
    return jsonify({
        'identities': identities,
        'from_cache': False,
        'cache_saved': True
    })

# ============================================
# === ENDPOINT GESTIONE CACHE IDENTITÀ ===
# ============================================

@app.route('/api/cache/identities/clear', methods=['POST'])
def cache_identities_clear():
    identity_cache.clear('all_identities')
    return jsonify({
        'success': True,
        'message': 'Cache identità pulita'
    })

@app.route('/api/cache/identities/status')
def cache_identities_status():
    stats = identity_cache.get_stats()
    return jsonify({
        'success': True,
        'cache': stats
    })

@app.route('/api/cache/identities/refresh', methods=['POST'])
def cache_identities_refresh():
    identity_cache.clear('all_identities')
    return jsonify({
        'success': True,
        'message': 'Cache invalidata, prossima richiesta farà scansione'
    })

# ============================================
# === TUTTE LE ALTRE ROUTE IDENTITY ===
# ============================================

@app.route('/api/identities/import/file', methods=['POST'])
def import_identity_file():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'})
        
        suggested_name = request.form.get('suggested_name', '').strip()
        if not suggested_name:
            suggested_name = file.filename.split('.')[0] if '.' in file.filename else file.filename
        
        dest_path = os.path.join(RNS_MANAGER_STORAGE, suggested_name)
        file.save(dest_path)
        
        file_size = os.path.getsize(dest_path)
        if file_size != 64:
            os.remove(dest_path)
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        result = subprocess.run(
            ['rnid', '-i', dest_path, '--print-identity'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            os.remove(dest_path)
            return jsonify({'success': False, 'error': 'File non è un\'identità RNS valida'})
        
        rns_hash = None
        for line in result.stdout.split('\n'):
            if 'Loaded Identity <' in line:
                start = line.find('<') + 1
                end = line.find('>', start)
                if start > 0 and end > start:
                    rns_hash = line[start:end]
                    break
        
        identity_cache.clear('all_identities')
        
        return jsonify({
            'success': True,
            'message': f'Identità importata come {suggested_name}',
            'name': suggested_name,
            'path': dest_path,
            'info': result.stdout,
            'size': file_size,
            'rns_hash': rns_hash
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/import/data', methods=['POST'])
def import_identity_data():
    try:
        data = request.json
        identity_data = data.get('data', '').strip()
        format_type = data.get('format', 'hex')
        suggested_name = data.get('suggested_name', '').strip()
        is_private = data.get('private', False)
        
        if not identity_data:
            return jsonify({'success': False, 'error': 'No identity data provided'})
        
        if not suggested_name:
            suggested_name = f"imported_{int(time.time())}"
        
        dest_path = os.path.join(RNS_MANAGER_STORAGE, suggested_name)
        
        try:
            if format_type == 'hex':
                identity_data = ''.join(identity_data.split())
                identity_bytes = bytes.fromhex(identity_data)
            elif format_type == 'base64':
                identity_bytes = base64.b64decode(identity_data)
            elif format_type == 'base32':
                identity_bytes = base64.b32decode(identity_data)
            else:
                return jsonify({'success': False, 'error': f'Formato non supportato: {format_type}'})
            
            print(f"[DEBUG] Decodificati {len(identity_bytes)} bytes")
            
        except Exception as e:
            return jsonify({
                'success': False, 
                'error': f'Errore nella decodifica dei dati: {str(e)}'
            })
        
        if len(identity_bytes) not in [64, 128]:
            return jsonify({
                'success': False,
                'error': f'Dati di {len(identity_bytes)} bytes, deve essere 64 (public) o 128 (private) bytes'
            })
        
        actual_is_private = (len(identity_bytes) == 128)
        
        with open(dest_path, 'wb') as f:
            f.write(identity_bytes)
        
        print(f"[DEBUG] Identity salvata in {dest_path} ({len(identity_bytes)} bytes)")
        
        verify_cmd = ['rnid', '-i', dest_path, '--print-identity']
        if actual_is_private:
            verify_cmd.append('-P')
        
        verify_result = subprocess.run(
            verify_cmd,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if verify_result.returncode != 0:
            os.remove(dest_path)
            return jsonify({
                'success': False, 
                'error': f'I dati non rappresentano un\'identità RNS valida: {verify_result.stderr[:200]}'
            })
        
        rns_hash = None
        for line in verify_result.stdout.split('\n'):
            if 'Loaded Identity <' in line:
                start = line.find('<') + 1
                end = line.find('>', start)
                if start > 0 and end > start:
                    rns_hash = line[start:end]
                    break
        
        identity_cache.clear('all_identities')
        print("[DEBUG] Cache identità invalidata dopo import")
        
        return jsonify({
            'success': True,
            'message': f'Identità {"privata" if actual_is_private else "pubblica"} importata come {suggested_name}',
            'name': suggested_name,
            'path': dest_path,
            'info': verify_result.stdout,
            'size': len(identity_bytes),
            'format': format_type,
            'rns_hash': rns_hash,
            'type': 'private' if actual_is_private else 'public'
        })
        
    except Exception as e:
        print(f"[DEBUG] Errore import: {str(e)}")
        return jsonify({'success': False, 'error': f'Errore: {str(e)}'})

@app.route('/api/identities/export', methods=['POST'])
def export_identity():
    try:
        data = request.json
        identity_path = data.get('path', '')
        format_type = data.get('format', 'hex')
        export_private = data.get('private', False)
        
        if not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'File identità non trovato'})
        
        file_size = os.path.getsize(identity_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        cmd = ['rnid', '-i', identity_path]
        
        if export_private:
            cmd.append('-X')
            cmd.append('-P')
        else:
            cmd.append('-x')
        
        if format_type == 'base64':
            cmd.append('-b')
        elif format_type == 'base32':
            cmd.append('-B')
        
        print(f"[DEBUG] Export command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({
                'success': False, 
                'error': f'Errore: {result.stderr[:200]}'
            })
        
        output = result.stdout
        
        output = re.sub(r'.\x08', '', output)
        output = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', output)
        output = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', output)
        
        key_data = None
        key_type = "Private" if export_private else "Public"
        
        for line in output.split('\n'):
            line = line.strip()
            if f"{key_type} Identity Keys" in line:
                if ": " in line:
                    key_data = line.split(": ", 1)[-1].strip()
                    break
        
        if not key_data:
            for line in reversed(output.split('\n')):
                line = line.strip()
                if line and not line.startswith('Loaded') and not line.startswith('Exported') and not line.startswith('['):
                    if re.match(r'^[0-9a-fA-F]+$', line) or re.match(r'^[A-Za-z0-9+/=]+$', line):
                        key_data = line
                        break
        
        if not key_data:
            for line in reversed(output.split('\n')):
                line = line.strip()
                if line and not line.startswith('Loaded') and not line.startswith('Exported'):
                    key_data = line
                    break
        
        if not key_data:
            return jsonify({
                'success': False,
                'error': f'Impossibile estrarre la chiave {key_type}'
            })
        
        return jsonify({
            'success': True,
            'data': key_data,
            'format': format_type,
            'length': len(key_data),
            'type': 'private' if export_private else 'public'
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Timeout del comando'})
    except Exception as e:
        print(f"[DEBUG] Errore export: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/generate', methods=['POST'])
def generate_identity():
    try:
        data = request.json
        name = data.get('name', 'identity').strip()
        
        if '.' in name:
            name = name.split('.')[0]
        
        dest_path = os.path.join(RNS_MANAGER_STORAGE, name)
        
        result = subprocess.run(
            ['rnid', '-g', dest_path, '--print-identity', '-P'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({'success': False, 'error': 'Errore nella generazione'})
        
        file_size = os.path.getsize(dest_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File generato di {file_size} bytes (dovrebbe essere 64)'})
        
        identity_cache.clear('all_identities')
        
        return jsonify({
            'success': True,
            'message': f'Identità generata in {dest_path}',
            'path': dest_path,
            'info': result.stdout,
            'size': file_size
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/info', methods=['POST'])
def get_identity_info():
    try:
        data = request.json
        identity_path = data.get('path', '')
        aspect = data.get('aspect', '')
        
        if not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'File identità non trovato'})
        
        file_size = os.path.getsize(identity_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        result = subprocess.run(
            ['rnid', '-i', identity_path, '--print-identity', '-P'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        info_text = ""
        rns_hash = None
        
        if result.returncode == 0:
            info_text = result.stdout
            for line in result.stdout.split('\n'):
                if 'Loaded Identity <' in line:
                    start = line.find('<') + 1
                    end = line.find('>', start)
                    if start > 0 and end > start:
                        rns_hash = line[start:end]
                        break
        
        aspect_hashes = {}
        
        if aspect and aspect in rns_monitor.RNS_ASPECTS:
            hash_result = subprocess.run(
                ['rnid', '-i', identity_path, '-H', aspect],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if hash_result.returncode == 0:
                pattern = f"The {aspect} destination for this Identity is <"
                for line in hash_result.stdout.split('\n'):
                    if pattern in line:
                        start = line.find('<') + 1
                        end = line.find('>', start)
                        if start > 0 and end > start:
                            aspect_hashes[aspect] = line[start:end]
                            break
                
                if aspect not in aspect_hashes:
                    for line in hash_result.stdout.split('\n'):
                        if 'Destination hash:' in line:
                            aspect_hashes[aspect] = line.split('Destination hash:')[-1].strip()
                            break
        
        return jsonify({
            'success': True,
            'info': info_text,
            'path': identity_path,
            'size': file_size,
            'rns_hash': rns_hash,
            'aspect_hashes': aspect_hashes
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === ROUTE PER CIFRATURA TESTO ===
# ============================================

@app.route('/api/identities/encrypt', methods=['POST'])
def encrypt_text():
    try:
        data = request.json
        identity_path = data.get('identity_path', '')
        text = data.get('text', '')
        
        if not identity_path or not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'Identità non trovata'})
        
        if not text:
            return jsonify({'success': False, 'error': 'Nessun testo da cifrare'})
        
        temp_input = "/tmp/web_input.txt"
        temp_output = "/tmp/web_input.txt.rfe"
        
        subprocess.run(f"rm -f {temp_input} {temp_output}", shell=True, capture_output=True)
        
        with open(temp_input, 'w') as f:
            f.write(text)
        
        # Cifra usando rnid
        if identity_path.startswith('public:'):
            rns_hash = identity_path.replace('public:', '')
            cmd = ['rnid', '-R', '-i', rns_hash, '-e', temp_input]
        else:
            cmd = ['rnid', '-i', identity_path, '-e', temp_input]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            subprocess.run(f"rm -f {temp_input} {temp_output}", shell=True, capture_output=True)
            return jsonify({'success': False, 'error': f'Errore cifratura: {result.stderr[:200]}'})
        
        if not os.path.exists(temp_output):
            subprocess.run(f"rm -f {temp_input} {temp_output}", shell=True, capture_output=True)
            return jsonify({'success': False, 'error': 'File cifrato non creato'})
        
        with open(temp_output, 'rb') as f:
            encrypted_bytes = f.read()
        encrypted_base64 = base64.b64encode(encrypted_bytes).decode('ascii')
        
        subprocess.run(f"rm -f {temp_input} {temp_output}", shell=True, capture_output=True)
        
        return jsonify({
            'success': True,
            'encrypted': encrypted_base64
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === ROUTE PER DECIFRATURA TESTO ===
# ============================================

@app.route('/api/identities/decrypt', methods=['POST'])
def decrypt_text():
    try:
        data = request.json
        identity_path = data.get('identity_path', '')
        encrypted_text = data.get('encrypted_text', '')
        
        if not identity_path or not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'Identità non trovata'})
        
        if not encrypted_text:
            return jsonify({'success': False, 'error': 'Nessun testo da decifrare'})
        
        if identity_path.startswith('public:'):
            return jsonify({'success': False, 'error': 'Per DECIFRARE serve un\'identità PRIVATA!'})
        
        clean_input = encrypted_text.strip()
        
        # 🔥 USA .rfe PER IL FILE CIFRATO!
        temp_input = "/tmp/web_encrypted.rfe"
        temp_output = "/tmp/web_decrypted.txt"
        
        subprocess.run(f"rm -f {temp_input} {temp_output}", shell=True, capture_output=True)
        
        try:
            encrypted_bytes = base64.b64decode(clean_input)
            with open(temp_input, 'wb') as f:
                f.write(encrypted_bytes)
        except Exception as e:
            subprocess.run(f"rm -f {temp_input} {temp_output}", shell=True, capture_output=True)
            return jsonify({'success': False, 'error': f'Base64 non valido: {str(e)}'})
        
        # 🔥 DECIFRA CON .rfe
        cmd = ['rnid', '-i', identity_path, '-d', temp_input, '-f']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            subprocess.run(f"rm -f {temp_input} {temp_output}", shell=True, capture_output=True)
            return jsonify({'success': False, 'error': f'Errore decifratura: {result.stderr[:200]}'})
        
        decrypted_text = ""
        if os.path.exists(temp_output):
            with open(temp_output, 'r') as f:
                decrypted_text = f.read().strip()
            
            subprocess.run(f"rm -f {temp_input} {temp_output}", shell=True, capture_output=True)
            
            return jsonify({
                'success': True,
                'decrypted': decrypted_text
            })
        else:
            subprocess.run(f"rm -f {temp_input} {temp_output}", shell=True, capture_output=True)
            return jsonify({'success': False, 'error': 'File decifrato non creato'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === ROUTE PER FIRMA ===
# ============================================

@app.route('/api/identities/sign', methods=['POST'])
def sign_text():
    try:
        data = request.json
        identity_path = data.get('identity_path', '')
        text = data.get('text', '')
        
        if not identity_path or not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'Identità non trovata'})
        
        if not text:
            return jsonify({'success': False, 'error': 'Nessun testo da firmare'})
        
        if identity_path.startswith('public:'):
            return jsonify({'success': False, 'error': 'Per FIRMARE serve un\'identità PRIVATA!'})
        
        temp_file = "/tmp/web_sign.txt"
        temp_sig = "/tmp/web_sign.txt.rsg"
        
        subprocess.run(f"rm -f {temp_file} {temp_sig}", shell=True, capture_output=True)
        
        with open(temp_file, 'w') as f:
            f.write(text)
        
        cmd = ['rnid', '-i', identity_path, '-s', temp_file, '-f']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        if result.returncode != 0:
            subprocess.run(f"rm -f {temp_file} {temp_sig}", shell=True, capture_output=True)
            return jsonify({'success': False, 'error': f'Errore firma: {result.stderr[:200]}'})
        
        if not os.path.exists(temp_sig):
            subprocess.run(f"rm -f {temp_file} {temp_sig}", shell=True, capture_output=True)
            return jsonify({'success': False, 'error': 'File firma non creato'})
        
        with open(temp_sig, 'rb') as f:
            sig_bytes = f.read()
        sig_base64 = base64.b64encode(sig_bytes).decode('ascii')
        
        hash_result = subprocess.run(
            ['rnid', '-i', identity_path, '--print-identity'],
            capture_output=True,
            text=True,
            timeout=5
        )
        rns_hash = None
        if hash_result.returncode == 0:
            match = re.search(r'Loaded Identity <([0-9a-f]+)>', hash_result.stdout)
            if match:
                rns_hash = match.group(1)
        
        subprocess.run(f"rm -f {temp_file} {temp_sig}", shell=True, capture_output=True)
        
        return jsonify({
            'success': True,
            'signature': sig_base64,
            'rns_hash': rns_hash,
            'message': 'Firma creata con successo'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === ROUTE PER VERIFICA FIRMA ===
# ============================================

@app.route('/api/identities/verify', methods=['POST'])
def verify_signature():
    try:
        data = request.json
        identity_path = data.get('identity_path', '')
        text = data.get('text', '')
        signature = data.get('signature', '')
        
        if not identity_path:
            return jsonify({'success': False, 'error': 'Identità non specificata'})
        
        if not text:
            return jsonify({'success': False, 'error': 'Nessun testo da verificare'})
        
        if not signature:
            return jsonify({'success': False, 'error': 'Nessuna firma da verificare'})
        
        temp_file = "/tmp/web_verify.txt"
        temp_sig = "/tmp/web_verify.txt.rsg"
        
        subprocess.run(f"rm -f {temp_file} {temp_sig}", shell=True, capture_output=True)
        
        with open(temp_file, 'w') as f:
            f.write(text)
        
        try:
            sig_bytes = base64.b64decode(signature)
            with open(temp_sig, 'wb') as f:
                f.write(sig_bytes)
        except Exception as e:
            subprocess.run(f"rm -f {temp_file} {temp_sig}", shell=True, capture_output=True)
            return jsonify({'success': False, 'error': f'Firma base64 non valida: {str(e)}'})
        
        if identity_path.startswith('public:'):
            rns_hash = identity_path.replace('public:', '')
            cmd = ['rnid', '-R', '-i', rns_hash, '-V', temp_sig]
        else:
            cmd = ['rnid', '-i', identity_path, '-V', temp_sig]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        
        subprocess.run(f"rm -f {temp_file} {temp_sig}", shell=True, capture_output=True)
        
        if result.returncode != 0:
            return jsonify({
                'success': False,
                'error': f'Errore verifica: {result.stderr[:200]}',
                'output': result.stdout
            })
        
        if 'is valid' in result.stdout.lower():
            return jsonify({
                'success': True,
                'valid': True,
                'message': '✅ FIRMA VALIDA',
                'output': result.stdout
            })
        elif 'is invalid' in result.stdout.lower():
            return jsonify({
                'success': True,
                'valid': False,
                'message': '❌ FIRMA NON VALIDA',
                'output': result.stdout
            })
        else:
            return jsonify({
                'success': True,
                'valid': None,
                'message': '⚠️ Verifica completata, risultato ambiguo',
                'output': result.stdout
            })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === UPLOAD E CLEANUP ===
# ============================================

@app.route('/api/upload/temp', methods=['POST'])
def upload_temp_file():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'})
        
        temp_dir = os.path.join(tempfile.gettempdir(), 'rnid_web')
        os.makedirs(temp_dir, exist_ok=True)
        
        unique_id = str(uuid.uuid4())[:8]
        original_name = file.filename
        safe_name = ''.join(c for c in original_name if c.isalnum() or c in '._- ')
        
        temp_filename = f"{unique_id}_{safe_name}"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        file.save(temp_path)
        
        return jsonify({
            'success': True,
            'temp_path': temp_path,
            'original_name': original_name,
            'output_dir': DOWNLOADS_DIR,
            'message': 'File salvato temporaneamente'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cleanup/temp', methods=['POST'])
def cleanup_temp_file():
    try:
        data = request.json
        temp_path = data.get('temp_path', '')
        
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
            return jsonify({'success': True, 'message': 'File temporaneo rimosso'})
        
        return jsonify({'success': False, 'error': 'File non trovato'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === COMANDI RNS (rnstatus, rnpath, rnprobe) ===
# ============================================

@app.route('/api/rns/status')
def rns_status():
    try:
        result = subprocess.run(
            ['rnstatus'],
            capture_output=True,
            text=True,
            timeout=10
        )
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rns/paths')
def rns_paths():
    try:
        destination = request.args.get('dest', '')
        
        print(f"[DEBUG] rnpath richiesto per destinazione: '{destination}'")
        
        if destination and destination.strip():
            cmd = ['rnpath', destination.strip()]
            print(f"[DEBUG] Esecuzione comando: {' '.join(cmd)}")
        else:
            cmd = ['rnpath']
            print(f"[DEBUG] Esecuzione comando: rnpath (senza argomenti)")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        print(f"[DEBUG] rnpath returncode: {result.returncode}")
        print(f"[DEBUG] rnpath stdout: {result.stdout[:200]}...")
        print(f"[DEBUG] rnpath stderr: {result.stderr[:200]}...")
        
        stdout = result.stdout
        stderr = result.stderr
        
        stdout = re.sub(r'.\x08', '', stdout)
        stdout = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stdout)
        stdout = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', stdout)
        
        stderr = re.sub(r'.\x08', '', stderr)
        stderr = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stderr)
        
        return jsonify({
            'success': result.returncode == 0,
            'output': stdout,
            'error': stderr,
            'destination': destination,
            'cmd': ' '.join(cmd) if destination else 'rnpath'
        })
    except subprocess.TimeoutExpired:
        print("[DEBUG] rnpath timeout scaduto")
        return jsonify({'success': False, 'error': 'Timeout del comando'})
    except Exception as e:
        print(f"[DEBUG] Errore rnpath: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rns/probe', methods=['POST'])
def rns_probe():
    try:
        data = request.json
        destination = data.get('destination', '')
        aspect = data.get('aspect', 'rnstransport.probe')
        
        if not destination:
            return jsonify({'success': False, 'error': 'Nessuna destinazione specificata'})
        
        cmd = ['rnprobe', aspect, destination]
        
        print(f"[DEBUG] Esecuzione probe: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        print(f"[DEBUG] Probe returncode: {result.returncode}")
        print(f"[DEBUG] Probe stdout: {result.stdout[:200]}")
        
        stdout = result.stdout
        stderr = result.stderr
        
        stdout = re.sub(r'.\x08', '', stdout)
        stdout = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stdout)
        stdout = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', stdout)
        
        stderr = re.sub(r'.\x08', '', stderr)
        stderr = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stderr)
        
        return jsonify({
            'success': result.returncode == 0,
            'output': stdout,
            'error': stderr,
            'destination': destination,
            'aspect': aspect,
            'cmd': ' '.join(cmd)
        })
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Timeout del comando (30s)'})
    except Exception as e:
        print(f"[DEBUG] Errore probe: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rns/probe/aspect', methods=['POST'])
def rns_probe_aspect():
    try:
        data = request.json
        identity_path = data.get('identity_path', '')
        aspect = data.get('aspect', 'rnstransport.probe')
        
        if not identity_path or not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'Identità non trovata'})
        
        if aspect not in rns_monitor.RNS_ASPECTS:
            return jsonify({'success': False, 'error': f'Aspect non valido: {aspect}'})
        
        hash_result = subprocess.run(
            ['rnid', '-i', identity_path, '-H', aspect],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if hash_result.returncode != 0:
            return jsonify({'success': False, 'error': 'Impossibile calcolare hash aspect'})
        
        dest_hash = None
        for line in hash_result.stdout.split('\n'):
            if 'Destination hash:' in line:
                dest_hash = line.split('Destination hash:')[-1].strip()
                break
        
        if not dest_hash:
            return jsonify({'success': False, 'error': 'Hash non trovato'})
        
        probe_result = subprocess.run(
            ['rnprobe', dest_hash],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        return jsonify({
            'success': probe_result.returncode == 0,
            'output': probe_result.stdout,
            'error': probe_result.stderr,
            'dest_hash': dest_hash,
            'aspect': aspect,
            'identity_path': identity_path
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/rns/paths/blackhole', methods=['POST'])
def rns_paths_blackhole():
    try:
        data = request.json
        identity_hash = data.get('destination', '')
        
        if not identity_hash:
            return jsonify({'success': False, 'error': 'Nessuna identità specificata'})
        
        cmd = ['rnpath', '-p', identity_hash]
        
        print(f"[DEBUG] Esecuzione rnpath -p per identità: {identity_hash}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        return jsonify({
            'success': result.returncode == 0,
            'output': result.stdout,
            'error': result.stderr,
            'identity': identity_hash,
            'cmd': ' '.join(cmd)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/identities/find/by-hash')
def find_identity_by_hash():
    try:
        hash_value = request.args.get('hash', '')
        if not hash_value:
            return jsonify({'success': False, 'error': 'Nessun hash specificato'})
        
        storage_dirs = [
            (RETICULUM_STORAGE, 'reticulum'),
            (NOMADNET_STORAGE, 'nomadnet'),
            (LXMF_STORAGE, 'lxmf'),
            (RNS_MANAGER_STORAGE, 'rns_manager')
        ]
        
        if RNPHONE_STORAGE and os.path.exists(RNPHONE_STORAGE):
            storage_dirs.append((RNPHONE_STORAGE, 'rnphone'))
        
        if MESHCHAT_STORAGE and os.path.exists(MESHCHAT_STORAGE):
            storage_dirs.append((MESHCHAT_STORAGE, 'meshchat'))
        
        for storage_path, app_name in storage_dirs:
            if storage_path and os.path.exists(storage_path):
                for item in os.listdir(storage_path):
                    item_path = os.path.join(storage_path, item)
                    
                    if os.path.isfile(item_path) and os.path.getsize(item_path) == 64:
                        result = subprocess.run(
                            ['rnid', '-i', item_path, '--print-identity'],
                            capture_output=True,
                            text=True,
                            timeout=3
                        )
                        
                        if result.returncode == 0:
                            for line in result.stdout.split('\n'):
                                if 'Loaded Identity <' in line:
                                    start = line.find('<') + 1
                                    end = line.find('>', start)
                                    if start > 0 and end > start:
                                        identity_hash = line[start:end]
                                        if hash_value in identity_hash:
                                            return jsonify({
                                                'success': True,
                                                'identity_path': item_path,
                                                'identity_name': item,
                                                'app': app_name,
                                                'full_hash': identity_hash
                                            })
        
        return jsonify({'success': False, 'error': 'Identità non trovata'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ============================================
# === GESTIONE CHIUSURA SICURA ===
# ============================================

def signal_handler(sig, frame):
    """Gestisce Ctrl+C e SIGTERM per fermare il server in modo sicuro"""
    print("\n" + "=" * 60)
    print("🛑 Arresto server in corso...")
    print("=" * 60)
    
    # Ferma il monitor manager
    try:
        monitor_manager.stop()
        print("[✓] Monitor fermato")
    except Exception as e:
        print(f"[!] Errore fermo monitor: {e}")
    
    # Pulisci file temporanei
    try:
        subprocess.run("rm -f /tmp/web_input.txt /tmp/web_input.txt.rfe /tmp/web_decrypted.txt /tmp/web_encrypted.rfe /tmp/web_sign.txt /tmp/web_sign.txt.rsg /tmp/web_verify.txt /tmp/web_verify.txt.rsg", shell=True, capture_output=True)
        print("[✓] File temporanei puliti")
    except Exception as e:
        print(f"[!] Errore pulizia file temporanei: {e}")
    
    print("=" * 60)
    print("✅ Server fermato correttamente")
    print("=" * 60)
    sys.exit(0)

def open_browser():
    """Apre il browser dopo un breve ritardo"""
    time.sleep(2)
    try:
        webbrowser.open('http://127.0.0.1:5000')
        print("🌐 Browser aperto su http://127.0.0.1:5000")
    except Exception as e:
        print(f"[!] Impossibile aprire il browser: {e}")

# ============================================
# === AVVIO SERVER ===
# ============================================

if __name__ == '__main__':
    # Registra il gestore di segnali per chiusura sicura
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    print("=" * 60)
    print("RNID Web Interface + RNS Aspect Monitor")
    print("=" * 60)
    print("\n📁 Directory configurate:")
    for key, path in USER_DIRECTORIES.items():
        expanded = os.path.expanduser(path)
        print(f"  {key}: {expanded}")
    
    print(f"\n📁 Downloads: {DOWNLOADS_DIR}")
    print(f"📁 Cache: {CACHE_DIR}")
    print("\n🌐 Accesso:")
    print(f"  http://localhost:5000/ - Identity Manager")
    print(f"  http://localhost:5000/monitor - Aspect Monitor")
    print("\n🚀 Avvio server...")
    print("   Premi Ctrl+C per fermare\n")
    
    # Avvia il thread per aprire il browser
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()
    
    try:
        app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False, threaded=True)
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)
    except Exception as e:
        print(f"\n[!] Errore imprevisto: {e}")
        signal_handler(signal.SIGTERM, None)