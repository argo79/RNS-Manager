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

# ============================================
# === INIZIALIZZA MONITOR RNS ===
# ============================================

# Crea istanza del monitor manager
monitor_manager = rns_monitor.RNSMonitorManager(
    socket_path=rns_monitor.SOCKET_PATH,
    aspects=rns_monitor.RNS_ASPECTS,
    cache_dir=CACHE_DIR,
    max_history=1000
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
    def __init__(self, cache_duration=300):  # 5 minuti default
        self.cache = {}
        self.timestamps = {}
        self.cache_duration = cache_duration
        self.lock = threading.Lock()
        
    def get(self, key='all_identities'):
        """Recupera dalla cache se non scaduta"""
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
        """Salva in cache"""
        with self.lock:
            self.cache[key] = data
            self.timestamps[key] = time.time()
            print(f"[Cache ID] Salvati {len(data)} elementi per {key}")
    
    def clear(self, key=None):
        """Pulisce cache"""
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
        """Statistiche cache"""
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
identity_cache = IdentityCache(cache_duration=3600)  # 6 ore


# ============================================
# === ROUTE RESET TOTALE ===
# ============================================

@app.route('/api/monitor/reset-all', methods=['POST'])
def monitor_reset_all():
    """Reset totale di storico e cache"""
    try:
        print("\n" + "="*60)
        print("🔄 RESET TOTALE RICHIESTO")
        print("="*60)
        
        # Usa il monitor_manager per pulire tutto
        monitor_manager.clear_all()
        
        # Pulisci anche la cache esplicitamente
        if monitor_manager.announce_cache:
            monitor_manager.announce_cache.clear()
            
            # Elimina file cache
            cache_file = monitor_manager.announce_cache.cache_file
            if os.path.exists(cache_file):
                os.remove(cache_file)
                print(f"[✓] File cache eliminato: {cache_file}")
            
            # Crea file cache vuoto
            with open(cache_file, 'w') as f:
                json.dump([], f)
            print(f"[✓] Nuovo file cache vuoto creato")
        
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
            
            # ===== PULIZIA OUTPUT =====
            stdout = result.stdout
            stderr = result.stderr
            
            # Rimuovi caratteri di controllo e backspace
            stdout = re.sub(r'.\x08', '', stdout)  # Rimuovi backspace
            stdout = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stdout)  # Caratteri controllo
            stdout = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', stdout)  # Codici ANSI
            stdout = re.sub(r'[\u2800-\u28FF]', '', stdout)  # Braille
            
            # Per output base64/encrypted, mantieni ma formatta meglio
            if len(stdout) > 1000 or '4U+6vC' in stdout:
                # Suddividi in righe di lunghezza ragionevole
                lines = []
                for line in stdout.split('\n'):
                    if len(line) > 80:
                        # Spezza righe molto lunghe ogni 80 caratteri
                        for i in range(0, len(line), 80):
                            lines.append(line[i:i+80])
                    else:
                        lines.append(line)
                stdout = '\n'.join(lines)
            
            return jsonify({
                'success': result.returncode == 0,
                'output': stdout,
                'error': re.sub(r'.\x08', '', stderr),  # Pulisci anche stderr
                'return_code': result.returncode
            })
            
        elif command.startswith(('rm -f ', 'echo ', 'base64 ', 'cat ', 'stat -c%s ', 'cp ', 'mkdir -p ')):
            allowed_paths = [
                '/tmp/web_input.txt',
                '/tmp/web_encrypted.enc', 
                '/tmp/web_decrypted.txt',
                '/tmp/rnid_web/',
                '/tmp/rnid_web_signed/',
                DOWNLOADS_DIR,
                CACHE_DIR
            ]
            
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
    # Controlla parametro force
    force_refresh = request.args.get('force', 'false').lower() == 'true'
    
    # Se non forza refresh, prova a usare cache
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
                            
                            # Limita a 10 aspect per performance
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
    
    # Salva in cache
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
    """Pulisce cache identità"""
    identity_cache.clear('all_identities')
    return jsonify({
        'success': True,
        'message': 'Cache identità pulita'
    })

@app.route('/api/cache/identities/status')
def cache_identities_status():
    """Stato cache identità"""
    stats = identity_cache.get_stats()
    return jsonify({
        'success': True,
        'cache': stats
    })

@app.route('/api/cache/identities/refresh', methods=['POST'])
def cache_identities_refresh():
    """Forza refresh cache"""
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
        
        if not identity_data:
            return jsonify({'success': False, 'error': 'No identity data provided'})
        
        if not suggested_name:
            suggested_name = f"imported_{int(time.time())}"
        
        dest_path = os.path.join(RNS_MANAGER_STORAGE, suggested_name)
        
        cmd_parts = ['rnid']
        if format_type == 'base32':
            cmd_parts.append('-B')
        elif format_type == 'base64':
            cmd_parts.append('-b')
        
        cmd_parts.extend(['-m', identity_data, '-P', '--print-identity', '--export', '-w', dest_path])
        
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({'success': False, 'error': f'Import failed: {result.stderr[:200]}'})
        
        if not os.path.exists(dest_path):
            return jsonify({'success': False, 'error': f'File non creato in {dest_path}'})
        
        file_size = os.path.getsize(dest_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        verify_result = subprocess.run(
            ['rnid', '-i', dest_path, '--print-identity'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if verify_result.returncode != 0:
            os.remove(dest_path)
            return jsonify({'success': False, 'error': 'Il file salvato non è un\'identità valida'})
        
        rns_hash = None
        for line in verify_result.stdout.split('\n'):
            if 'Loaded Identity <' in line:
                start = line.find('<') + 1
                end = line.find('>', start)
                if start > 0 and end > start:
                    rns_hash = line[start:end]
                    break
        
        return jsonify({
            'success': True,
            'message': f'Identità importata come {suggested_name}',
            'name': suggested_name,
            'path': dest_path,
            'info': result.stdout,
            'size': file_size,
            'format': format_type,
            'rns_hash': rns_hash
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Errore: {str(e)}'})

@app.route('/api/identities/export', methods=['POST'])
def export_identity():
    try:
        data = request.json
        identity_path = data.get('path', '')
        format_type = data.get('format', 'hex')
        
        if not os.path.exists(identity_path):
            return jsonify({'success': False, 'error': 'File identità non trovato'})
        
        file_size = os.path.getsize(identity_path)
        if file_size != 64:
            return jsonify({'success': False, 'error': f'File di {file_size} bytes, deve essere 64 bytes'})
        
        cmd = ['rnid']
        if format_type == 'base64':
            cmd.append('-b')
        elif format_type == 'base32':
            cmd.append('-B')
        
        cmd.extend(['-i', identity_path, '--export'])
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({'success': False, 'error': f'Errore: {result.stderr[:100]}'})
        
        exported_data = result.stdout.strip()
        lines = exported_data.split('\n')
        clean_data = ""
        
        for line in lines:
            if "Exported Identity : " in line:
                clean_line = line.split("Exported Identity : ")[-1].strip()
                clean_line = re.sub(r'\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]', '', clean_line).strip()
                clean_data = clean_line
                break
        
        if not clean_data and lines:
            clean_data = lines[-1].strip()
            clean_data = re.sub(r'\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]', '', clean_data).strip()
        
        return jsonify({
            'success': True,
            'data': clean_data,
            'format': format_type,
            'path': identity_path,
            'length': len(clean_data)
        })
        
    except Exception as e:
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
            # rnpath aspetta l'hash come argomento
            cmd = ['rnpath', destination.strip()]
            print(f"[DEBUG] Esecuzione comando: {' '.join(cmd)}")
        else:
            # Se nessuna destinazione, mostra tutte le route
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
        
        # ===== PULIZIA OUTPUT =====
        stdout = result.stdout
        stderr = result.stderr
        
        # Rimuovi caratteri backspace (molto importanti!)
        stdout = re.sub(r'.\x08', '', stdout)
        stdout = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stdout)  # Caratteri controllo
        stdout = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', stdout)  # Codici ANSI
        stdout = re.sub(r'[\u2800-\u28FF]', '', stdout)  # Caratteri braille
        
        # Pulisci anche stderr
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
        
        # Comando corretto: rnprobe <aspect> <destination>
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
        
        # ===== PULIZIA OUTPUT =====
        stdout = result.stdout
        stderr = result.stderr
        
        # Rimuovi caratteri backspace (molto importanti!)
        stdout = re.sub(r'.\x08', '', stdout)
        stdout = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', stdout)  # Caratteri controllo
        stdout = re.sub(r'\x1B\[[0-9;]*[a-zA-Z]', '', stdout)  # Codici ANSI
        stdout = re.sub(r'[\u2800-\u28FF]', '', stdout)  # Caratteri braille
        
        # Pulisci anche stderr
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
        
        # Prima ottieni l'hash dell'aspect
        hash_result = subprocess.run(
            ['rnid', '-i', identity_path, '-H', aspect],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if hash_result.returncode != 0:
            return jsonify({'success': False, 'error': 'Impossibile calcolare hash aspect'})
        
        # Estrai l'hash
        dest_hash = None
        for line in hash_result.stdout.split('\n'):
            if 'Destination hash:' in line:
                dest_hash = line.split('Destination hash:')[-1].strip()
                break
        
        if not dest_hash:
            return jsonify({'success': False, 'error': 'Hash non trovato'})
        
        # Esegui probe
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
        identity_hash = data.get('destination', '')  # Qui arriva l'identity hash
        
        if not identity_hash:
            return jsonify({'success': False, 'error': 'Nessuna identità specificata'})
        
        # Comando rnpath con flag -p usando l'identity hash
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
        
        # Cerca nelle directory storage
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
                        # Verifica se questa identità corrisponde all'hash
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
# === AVVIO SERVER ===
# ============================================

if __name__ == '__main__':
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
    print("\n🚀 Avvio server...\n")
    
    try:
        app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False, threaded=True)
    except KeyboardInterrupt:
        print("\n🛑 Arresto server...")
        # Ferma monitor manager
        monitor_manager.stop()
        print("✅ Server fermato")