#!/usr/bin/env python3
# app.py - Server web per LXMF Messenger

from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
import json
import os
import time
import threading
import queue
import sqlite3
import base64
import traceback
import signal
import uuid
import tempfile
import argparse
from messenger import Messenger, Commands, guess_image_format
import LXMF
import RNS
from messenger import Messenger, Commands, guess_image_format, CODEC2_AVAILABLE

# 🔴 COSTANTI LXMF
FIELD_TELEMETRY = 0x02
FIELD_TELEMETRY_STREAM = 0x03
FIELD_ICON_APPEARANCE = 0x04
FIELD_FILE_ATTACHMENTS = 0x05
FIELD_IMAGE = 0x06
FIELD_AUDIO = 0x07
FIELD_COMMANDS = 0x09

original_signal = signal.signal

def patched_signal(sig, handler):
    if threading.current_thread() is threading.main_thread():
        return original_signal(sig, handler)
    return None
signal.signal = patched_signal

app = Flask(__name__, static_folder='static', template_folder='templates')
app.config['SECRET_KEY'] = 'rns-manager-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Istanza globale del messenger
messenger = None
message_queue = queue.Queue()
messenger_lock = threading.Lock()

def on_progress(info):
    """Callback quando il progresso di invio cambia"""
    socketio.emit('progress_update', info)

def message_callback(msg):
    """Callback per messaggi in arrivo - invia via websocket"""
    try:
        # Estrai comandi se presenti
        commands = None
        if msg.get('fields') and FIELD_COMMANDS in msg['fields']:
            commands = msg['fields'][FIELD_COMMANDS]
            print(f"🎮 Comandi nel messaggio: {commands}")
        
        # 🔴 CONVERTI APPEARANCE DA BYTES A STRINGA
        appearance = msg.get('appearance')
        if appearance and len(appearance) >= 3:
            fg = appearance[1]
            bg = appearance[2]
            
            if isinstance(fg, bytes):
                fg = fg.hex()
            if isinstance(bg, bytes):
                bg = bg.hex()
            
            appearance = [
                appearance[0],
                fg,
                bg
            ]
            print(f"🎨 APPEARANCE convertita: {appearance}")
        
        # 🔴 ESTRAI TELEMETRIA
        telemetry_data = None
        if msg.get('telemetry'):
            if hasattr(msg['telemetry'], 'read_all'):
                try:
                    telemetry_data = msg['telemetry'].read_all()
                    print(f"📊 TELEMETRIA RICEVUTA: {telemetry_data}")
                except Exception as e:
                    print(f"❌ Errore lettura telemetria: {e}")
                    telemetry_data = {'error': str(e)}
            else:
                telemetry_data = {'info': 'Telemetria non decodificabile'}
        
        # 🔴 CONVERTI ATTACHMENTS
        attachments = msg.get('attachments', [])
        serializable_attachments = []
        for att in attachments:
            if len(att) == 2 and att[0] in ['image', 'audio', 'file']:
                att_type = att[0]
                att_data = att[1]
                
                if att_type == 'image':
                    serializable_attachments.append({
                        'type': 'image',
                        'format': att_data.get('type', 'jpg'),
                        'size': att_data.get('size', 0),
                        'data': att_data.get('bytes')
                    })
                elif att_type == 'audio':
                    serializable_attachments.append({
                        'type': 'audio',
                        'mode': att_data.get('mode', 16),
                        'size': att_data.get('size', 0),
                        'data': att_data.get('bytes')
                    })
                elif att_type == 'file':
                    serializable_attachments.append({
                        'type': 'file',
                        'name': att_data.get('name', 'file.bin'),
                        'size': att_data.get('size', 0),
                        'data': att_data.get('bytes')
                    })
        
        serializable_msg = {
            'from': msg.get('from'),
            'to': msg.get('to'),
            'content': msg.get('content', ''),
            'title': msg.get('title', ''),
            'time': msg.get('time'),
            'hash': msg.get('hash'),
            'size': msg.get('size', 0),
            'packed_size': msg.get('packed_size', 0),
            'method': msg.get('method'),
            'signature_valid': msg.get('signature_valid', False),
            'attachments': serializable_attachments,
            'appearance': appearance,
            'rssi': msg.get('rssi'),
            'snr': msg.get('snr'),
            'q': msg.get('q'),
            'hops': msg.get('hops'),
            'raw_hex': msg.get('raw_hex'),
            'raw_ascii': msg.get('raw_ascii'),
            'commands': commands,
            'telemetry_data': telemetry_data,
        }
        
        socketio.emit('new_message', serializable_msg)
        message_queue.put(serializable_msg)
    except Exception as e:
        print(f"Errore in message_callback: {e}")
        traceback.print_exc()

def create_messenger(identity_file=None):
    """Crea una nuova istanza del messenger con l'identità specificata"""
    global messenger
    try:
        if messenger is not None:
            print("⚠️ Messenger già inizializzato, uso quello esistente")
            return True
            
        print(f"🔄 Inizializzazione messenger con identità: {identity_file}")
        
        with messenger_lock:
            if identity_file:
                storagepath = os.path.expanduser("~/.rns_manager/storage")
                identity_path = os.path.join(storagepath, identity_file)
                messenger = Messenger(identity_path=identity_path)
            else:
                messenger = Messenger()
            
            messenger.local_dest = messenger.dest
            messenger.message_callback = message_callback
            messenger.announce()
        
        print(f"✅ Messenger inizializzato: {messenger.display_name}")
        print(f"🆔 Identity hash: {messenger.get_hash()}")
        return True
        
    except Exception as e:
        print(f"❌ Errore inizializzazione messenger: {e}")
        traceback.print_exc()
        return False

# ==================== API ROUTES ====================

@app.route('/')
def index():
    """Serve la pagina principale"""
    return render_template('lxmf_chat.html')

@app.route('/api/status')
def get_status():
    """Restituisce lo stato corrente"""
    if not messenger:
        return jsonify({'status': 'not_initialized', 'message': 'Nessuna identità selezionata'})
    
    aspect_hash = None
    try:
        dest = RNS.Destination(
            messenger.identity,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            "lxmf",
            "delivery"
        )
        aspect_hash = dest.hash.hex()
    except:
        aspect_hash = messenger.dest.hash.hex() if hasattr(messenger, 'dest') else None
    
    return jsonify({
        'status': 'ok',
        'identity': messenger.get_hash(),
        'aspect': 'lxmf.delivery',
        'aspect_hash': aspect_hash,
        'name': messenger.display_name,
        'propagation_node': messenger.config.get('propagation_node'),
        'connected': True
    })

@app.route('/api/identities')
def list_identities():
    """Lista le identità disponibili"""
    storagepath = os.path.expanduser("~/.rns_manager/storage")
    identities = []
    
    if not os.path.exists(storagepath):
        return jsonify([])
    
    for f in os.listdir(storagepath):
        fpath = os.path.join(storagepath, f)
        if os.path.isfile(fpath) and os.path.getsize(fpath) == 64:
            name_file = os.path.join(storagepath, f"{f}.name")
            name = None
            if os.path.exists(name_file):
                with open(name_file, 'r') as nf:
                    name = nf.read().strip()
            
            try:
                with open(fpath, 'rb') as idf:
                    data = idf.read(32)
                    hash_preview = data.hex()[:16]
            except:
                hash_preview = f[:16]
            
            identities.append({
                'file': f,
                'hash': f[:64] if len(f) >= 64 else f,
                'preview': hash_preview,
                'name': name or f[:8]
            })
    
    return jsonify(identities)

@app.route('/api/identity/select', methods=['POST'])
def select_identity():
    """Seleziona un'identità e inizializza il messenger"""
    global messenger
    data = request.json
    identity_file = data.get('identity')
    display_name = data.get('name', '').strip()
    
    if not identity_file:
        return jsonify({'success': False, 'error': 'Nessuna identità specificata'})
    
    try:
        if messenger is not None:
            print("⚠️ Messenger già attivo, procedo con shutdown...")
            shutdown_result = shutdown_messenger()
            if not shutdown_result.json['success']:
                print("⚠️ Shutdown precedente non completato, procedo comunque...")
        
        time.sleep(1.0)
        
        print(f"🔄 Inizializzazione messenger con identità: {identity_file}")
        success = create_messenger(identity_file)
        
        if success and messenger:
            if display_name:
                storagepath = os.path.expanduser("~/.rns_manager/storage")
                name_file = os.path.join(storagepath, f"{identity_file}.name")
                with open(name_file, 'w') as f:
                    f.write(display_name)
            
            return jsonify({
                'success': True,
                'identity': messenger.get_hash(),
                'aspect_hash': messenger.get_delivery_hash(),
                'name': messenger.display_name
            })
        else:
            return jsonify({'success': False, 'error': 'Inizializzazione fallita'})
    
    except Exception as e:
        print(f"❌ Errore in select_identity: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/peers')
def get_peers():
    """Restituisce la lista dei peer con tutti i dati"""
    if not messenger:
        return jsonify([])
    
    try:
        # Usa direttamente list_peers() che ora restituisce tutto
        peers = messenger.list_peers()
        
        # 🔴 AGGIUNGI I DATI DELL'ULTIMA TELEMETRIA
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            for peer in peers:
                # Recupera gli ultimi dati telemetria per questo peer
                c.execute('''
                    SELECT last_latitude, last_longitude, last_altitude,
                           last_speed, last_bearing, last_accuracy,
                           last_battery, last_battery_charging,
                           last_temperature, last_humidity, last_pressure,
                           last_light, last_uptime, last_processor,
                           last_ram, last_nvm, last_telemetry_time,
                           trust_level, allow_telemetry, allow_commands,
                           messages_sent, messages_received, telemetry_count,
                           is_favorite, group_name, notes,
                           destination_hash, last_seen, hops, rssi, snr, q
                    FROM destinations 
                    WHERE identity_hash = ? AND aspect = 'lxmf.delivery'
                    ORDER BY last_seen DESC LIMIT 1
                ''', (peer['hash'],))
                
                row = c.fetchone()
                if row:
                    peer.update({
                        'dest_hash': row[26],
                        'last_seen': row[27],
                        'hops': row[28],
                        'rssi': row[29],
                        'snr': row[30],
                        'q': row[31],
                        'last_location': {
                            'latitude': row[0],
                            'longitude': row[1],
                            'altitude': row[2],
                            'speed': row[3],
                            'bearing': row[4],
                            'accuracy': row[5]
                        } if row[0] and row[1] else None,
                        'last_battery': {
                            'percent': row[6],
                            'charging': bool(row[7])
                        } if row[6] is not None else None,
                        'last_temperature': row[8],
                        'last_humidity': row[9],
                        'last_pressure': row[10],
                        'last_light': row[11],
                        'last_uptime': row[12],
                        'last_processor': row[13],
                        'last_ram': row[14],
                        'last_nvm': row[15],
                        'last_telemetry_time': row[16],
                        'trust_level': row[17],
                        'allow_telemetry': bool(row[18]),
                        'allow_commands': bool(row[19]),
                        'messages_sent': row[20],
                        'messages_received': row[21],
                        'telemetry_count': row[22],
                        'favorite': bool(row[23]),
                        'group': row[24] or 'public',
                        'notes': row[25]
                    })
                else:
                    peer['favorite'] = False
                    peer['group'] = 'public'
            
            conn.close()
        
        return jsonify(peers)
    
    except Exception as e:
        print(f"Errore in get_peers: {e}")
        traceback.print_exc()
        return jsonify([])

@app.route('/api/conversation/<dest_hash>')
def get_conversation(dest_hash):
    """Restituisce i messaggi con un peer (dal database)"""
    if not messenger:
        return jsonify([])
    
    messages = []
    
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
            if not c.fetchone():
                conn.close()
                return jsonify([])
            
            c.execute('''
                SELECT hash, source_hash, destination_hash, content, title, timestamp,
                       method, packed_size, rssi, snr, q, hops, incoming,
                       raw_hex, raw_ascii, attachments
                FROM messages 
                WHERE source_hash = ? OR destination_hash = ?
                ORDER BY timestamp ASC
            ''', (dest_hash, dest_hash))
            
            rows = c.fetchall()
            conn.close()
        
        print(f"📁 Caricati {len(rows)} messaggi dal database per {dest_hash[:8]}...")
        
        for row in rows:
            hash_msg, source, dest, content, title, ts, method, packed_size, rssi, snr, q, hops, incoming, raw_hex, raw_ascii, attachments_json = row
            
            msg_attachments = []
            if attachments_json:
                try:
                    atts = json.loads(attachments_json)
                    for att in atts:
                        if att['type'] == 'image':
                            msg_attachments.append({
                                'type': 'image',
                                'format': att['format'],
                                'size': att['size']
                            })
                        elif att['type'] == 'audio':
                            msg_attachments.append({
                                'type': 'audio',
                                'mode': att['mode'],
                                'size': att['size']
                            })
                        elif att['type'] == 'file':
                            msg_attachments.append({
                                'type': 'file',
                                'name': att['name'],
                                'size': att['size']
                            })
                except Exception as e:
                    print(f"❌ Errore parsing attachments: {e}")
            
            messages.append({
                'hash': hash_msg,
                'from': source,
                'to': dest,
                'content': content or '',
                'title': title or '',
                'time': ts,
                'incoming': bool(incoming),
                'method': method,
                'packed_size': packed_size,
                'rssi': rssi,
                'snr': snr,
                'q': q,
                'hops': hops,
                'raw_hex': raw_hex,
                'raw_ascii': raw_ascii,
                'attachments': msg_attachments,
                'status': 'delivered' if incoming else 'sent'
            })
        
        messages.sort(key=lambda x: x['time'])
        print(f"✅ Totale messaggi caricati: {len(messages)}")
        
    except Exception as e:
        print(f"❌ Errore caricamento conversazione: {e}")
        traceback.print_exc()
    
    return jsonify(messages)

@app.route('/api/send', methods=['POST'])
def send_message():
    """Invia un messaggio - STANDARD LXMF"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    temp_files = []
    
    try:
        dest = request.form.get('destination')
        content = request.form.get('content', '')
        title = request.form.get('title', '')
        file = request.files.get('file')
        
        if not dest:
            return jsonify({'success': False, 'error': 'Destinazione mancante'})
        
        image_path = None
        audio_path = None
        file_paths = None
        file_type = request.form.get('type', 'file')
        
        if file:
            upload_dir = os.path.expanduser("~/.rns_manager/uploads")
            os.makedirs(upload_dir, exist_ok=True)
            
            unique_id = str(uuid.uuid4())[:8]
            safe_filename = f"{int(time.time())}_{unique_id}_{file.filename}"
            temp_path = os.path.join(upload_dir, safe_filename)
            file.save(temp_path)
            temp_files.append(temp_path)
            
            if file_type == 'image':
                image_path = temp_path
            elif file_type == 'audio':
                audio_path = temp_path
            else:
                file_paths = [(file.filename, temp_path)]
        
        def on_delivery(info):
            print(f"✅ Consegna riuscita per {info.get('hash')}")
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except:
                        pass
            socketio.emit('delivery_update', info)
        
        def on_failed(info):
            print(f"❌ Invio fallito per {info.get('hash')}")
            for temp_file in temp_files:
                if os.path.exists(temp_file):
                    try:
                        os.unlink(temp_file)
                    except:
                        pass
            socketio.emit('delivery_update', info)
        
        def on_progress(info):
            socketio.emit('progress_update', info)

        callbacks = {
            'delivery': on_delivery, 
            'failed': on_failed,
            'progress': on_progress
        }
        
        result = messenger.send(
            dest, content, title=title,
            image_path=image_path,
            audio_path=audio_path,
            file_attachments=file_paths,
            callbacks=callbacks
        )
        
        return jsonify(result)
    
    except Exception as e:
        traceback.print_exc()
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                try:
                    os.unlink(temp_file)
                except:
                    pass
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/command', methods=['POST'])
def send_command():
    """Invia un comando (PING, ECHO, SIGNAL)"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    data = request.json
    dest = data.get('destination')
    cmd = data.get('command')
    text = data.get('text')
    
    if not dest or not cmd:
        return jsonify({'success': False, 'error': 'Parametri mancanti'})
    
    cmd_map = {
        'ping': Commands.PING,
        'echo': Commands.ECHO,
        'signal': Commands.SIGNAL_REPORT
    }
    
    if cmd not in cmd_map:
        return jsonify({'success': False, 'error': 'Comando sconosciuto'})
    
    cmd_type = cmd_map[cmd]
    cmd_data = text.encode('utf-8') if cmd == 'echo' and text else None
    
    result = messenger.send_command(dest, cmd_type, cmd_data)
    return jsonify(result)

@app.route('/api/telemetry/request', methods=['POST'])
def request_telemetry():
    """Richiede telemetria"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    data = request.json
    dest = data.get('destination')
    
    if not dest:
        return jsonify({'success': False, 'error': 'Destinazione mancante'})
    
    result = messenger.request_telemetry(dest)
    return jsonify(result)

# ==================== ENDPOINT PER TELEMETRIA ====================

@app.route('/api/telemetry/config', methods=['GET', 'POST'])
def handle_telemetry_config():
    """Gestisce la configurazione della telemetria"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'})
    
    if request.method == 'GET':
        config = {
            'location_source': messenger.config.get('location_source', 'fixed'),
            'fixed_location': messenger.config.get('fixed_location', {
                'latitude': 45.6052,
                'longitude': 12.2430,
                'altitude': 12
            }),
            'enable_location': messenger.config.get('enable_location', True),
            'enable_system': messenger.config.get('enable_system', True),
            'gpsd_host': messenger.config.get('gpsd_host', 'localhost'),
            'gpsd_port': messenger.config.get('gpsd_port', 2947),
            'appearance': messenger.config.get('appearance', ['antenna', '4c9aff', '1a1f2e'])
        }
        return jsonify(config)
    
    else:
        try:
            data = request.json
            
            if 'location_source' in data:
                messenger.config['location_source'] = data['location_source']
            if 'fixed_location' in data:
                messenger.config['fixed_location'] = data['fixed_location']
            if 'enable_location' in data:
                messenger.config['enable_location'] = data['enable_location']
            if 'enable_system' in data:
                messenger.config['enable_system'] = data['enable_system']
            if 'gpsd_host' in data:
                messenger.config['gpsd_host'] = data['gpsd_host']
            if 'gpsd_port' in data:
                messenger.config['gpsd_port'] = data['gpsd_port']
            if 'appearance' in data:
                messenger.config['appearance'] = data['appearance']
            
            with open(messenger.configpath, 'w') as f:
                json.dump(messenger.config, f, indent=4)
            
            if hasattr(messenger, 'telemetry_provider'):
                messenger.telemetry_provider.config = messenger.config
            
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

@app.route('/api/telemetry/history/<peer_hash>')
def get_telemetry_history(peer_hash):
    """Restituisce lo storico telemetria per un peer"""
    if not messenger:
        return jsonify([])
    
    history = []
    telemetry_dir = messenger.telemetry_dir
    
    if os.path.exists(telemetry_dir):
        for filename in os.listdir(telemetry_dir):
            if filename.startswith(peer_hash) and filename.endswith('.json'):
                filepath = os.path.join(telemetry_dir, filename)
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                        ts = int(filename.split('_')[1].split('.')[0])
                        data['timestamp'] = ts
                        history.append(data)
                except Exception as e:
                    print(f"❌ Errore lettura {filename}: {e}")
                    continue
    
    history.sort(key=lambda x: x['timestamp'], reverse=True)
    print(f"📊 Caricati {len(history)} record di telemetria per {peer_hash[:8]}...")
    return jsonify(history[:50])

@app.route('/api/telemetry/test', methods=['POST'])
def test_telemetry():
    """Testa e restituisce i dati telemetria correnti"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'})
    
    if not hasattr(messenger, 'telemetry_provider') or not messenger.telemetry_provider:
        return jsonify({'error': 'Telemetry provider non disponibile'})
    
    try:
        data = messenger.telemetry_provider.get_telemetry_data()
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/peer/<identity_hash>/telemetry/last')
def get_last_telemetry(identity_hash):
    """Restituisce l'ultima telemetria conosciuta di un peer"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'}), 404
    
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            c.execute('''
                SELECT last_latitude, last_longitude, last_altitude,
                       last_speed, last_bearing, last_accuracy,
                       last_battery, last_battery_charging,
                       last_temperature, last_humidity, last_pressure,
                       last_light, last_uptime, last_processor,
                       last_ram, last_nvm, last_telemetry_time
                FROM destinations 
                WHERE identity_hash = ? AND aspect = 'lxmf.delivery'
                ORDER BY last_seen DESC LIMIT 1
            ''', (identity_hash,))
            
            row = c.fetchone()
            conn.close()
        
        if not row or not row[16]:
            return jsonify({'error': 'Nessuna telemetria disponibile'}), 404
        
        telemetry = {
            'location': {
                'latitude': row[0],
                'longitude': row[1],
                'altitude': row[2],
                'speed': row[3],
                'bearing': row[4],
                'accuracy': row[5]
            } if row[0] and row[1] else None,
            'battery': {
                'percent': row[6],
                'charging': bool(row[7])
            } if row[6] is not None else None,
            'temperature': row[8],
            'humidity': row[9],
            'pressure': row[10],
            'light': row[11],
            'uptime': row[12],
            'processor': row[13],
            'ram': row[14],
            'nvm': row[15],
            'timestamp': row[16]
        }
        
        return jsonify(telemetry)
        
    except Exception as e:
        print(f"❌ Errore get_last_telemetry: {e}")
        return jsonify({'error': str(e)}), 500

# ==================== ENDPOINT PER RAW ====================

@app.route('/api/raw/history')
def get_raw_history_all():
    """Restituisce lo storico dei pacchetti raw (globale)"""
    if not messenger:
        return jsonify([])
    
    history = []
    try:
        raw_files = sorted(os.listdir(messenger.raw_dir), reverse=True)[:50]
        for filename in raw_files:
            if filename.endswith('.raw'):
                filepath = os.path.join(messenger.raw_dir, filename)
                with open(filepath, 'rb') as f:
                    raw_data = f.read()
                
                json_file = filename.replace('.raw', '.json')
                json_path = os.path.join(messenger.raw_dir, json_file)
                metadata = {}
                if os.path.exists(json_path):
                    with open(json_path, 'r') as jf:
                        metadata = json.load(jf)
                
                history.append({
                    'hash': filename.replace('.raw', ''),
                    'time': metadata.get('time', os.path.getmtime(filepath)),
                    'from': metadata.get('from', '?'),
                    'to': metadata.get('to', '?'),
                    'method': metadata.get('method', '?'),
                    'packed_size': len(raw_data),
                    'raw_hex': raw_data.hex()[:128] + '...' if len(raw_data) > 64 else raw_data.hex(),
                    'raw_ascii': ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw_data[:64])
                })
    except Exception as e:
        print(f"❌ Errore caricamento storico raw: {e}")
    
    return jsonify(history)

@app.route('/api/raw/history/<dest_hash>')
def get_raw_history(dest_hash):
    """Restituisce lo storico dei raw per una conversazione"""
    if not messenger:
        return jsonify([])
    
    try:
        conn = sqlite3.connect(messenger.peers_db)
        c = conn.cursor()
        
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        if not c.fetchone():
            conn.close()
            return jsonify([])
        
        c.execute('''
            SELECT hash, raw_hex, raw_ascii, timestamp, source_hash, method, packed_size
            FROM messages 
            WHERE (source_hash = ? OR destination_hash = ?) 
              AND raw_hex IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT 50
        ''', (dest_hash, dest_hash))
        
        rows = c.fetchall()
        conn.close()
        
        raw_messages = []
        for row in rows:
            raw_messages.append({
                'hash': row[0],
                'raw_hex': row[1],
                'raw_ascii': row[2],
                'time': row[3],
                'from': row[4],
                'method': row[5],
                'packed_size': row[6]
            })
        return jsonify(raw_messages)
    except Exception as e:
        print(f"❌ Errore get_raw_history: {e}")
        return jsonify([])

@app.route('/api/raw/<hash>')
def get_raw(hash):
    """Restituisce il raw di un messaggio"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'})
    
    if hasattr(messenger, 'received_raw') and hash in messenger.received_raw:
        data = messenger.received_raw[hash]
        return jsonify({
            'hex': data['hex'],
            'ascii': data['ascii'],
            'source': 'memory'
        })
    
    hex_path = os.path.join(messenger.raw_dir, f"{hash}.hex")
    raw_path = os.path.join(messenger.raw_dir, f"{hash}.raw")
    
    if os.path.exists(hex_path):
        with open(hex_path, 'r') as f:
            hex_data = f.read().strip()
        
        if os.path.exists(raw_path):
            with open(raw_path, 'rb') as f:
                raw = f.read()
            ascii_data = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw)
        else:
            ascii_data = None
        
        return jsonify({
            'hex': hex_data,
            'ascii': ascii_data,
            'source': 'disk'
        })
    elif os.path.exists(raw_path):
        with open(raw_path, 'rb') as f:
            raw = f.read()
        hex_data = raw.hex()
        ascii_data = ''.join(chr(b) if 32 <= b < 127 else '.' for b in raw)
        return jsonify({
            'hex': hex_data,
            'ascii': ascii_data,
            'source': 'disk'
        })
    
    return jsonify({'error': 'Messaggio non trovato'}), 404

@app.route('/download/<filename>')
def download_file(filename):
    """Serve file per download - DEPRECATO"""
    return '', 404

# ==================== ENDPOINT PER CONFIG ====================

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    """Gestisce la configurazione"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'})
    
    if request.method == 'GET':
        config = messenger.config
        config['codec2_available'] = CODEC2_AVAILABLE
        return jsonify(config)
    else:
        try:
            data = request.json
            messenger.config.update(data)
            
            with open(messenger.configpath, 'w') as f:
                json.dump(messenger.config, f, indent=4)
            
            if 'propagation_node' in data:
                node_hash = data['propagation_node']
                if node_hash:
                    try:
                        messenger.propagation_node = bytes.fromhex(node_hash)
                        messenger.router.set_outbound_propagation_node(messenger.propagation_node)
                    except:
                        pass
            
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sync', methods=['POST'])
def sync_messages():
    """Sincronizza messaggi dal propagation node"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    result = messenger.sync_messages()
    
    if result['success']:
        socketio.emit('sync_complete', {'new': result['new']})
    
    return jsonify(result)

@app.route('/api/announce', methods=['POST'])
def send_announce():
    """Invia annuncio"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    messenger.announce()
    return jsonify({'success': True})

@app.route('/api/peers/import', methods=['POST'])
def import_peers():
    """Importa peer dalla cache"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    count = messenger.import_peers_from_cache()
    return jsonify({'success': True, 'count': count})

@app.route('/api/shutdown', methods=['POST'])
def shutdown_messenger():
    """Pulisce le risorse PRIMA di inizializzare una nuova identità"""
    global messenger
    try:
        if messenger:
            print("🛑 Arresto del messenger in corso...")
            
            messenger.delivery_callbacks.clear()
            messenger.failed_callbacks.clear()
            messenger.progress_callbacks.clear()
            
            messenger.cleanup()
            
            messenger = None
            
            import time
            time.sleep(1.0)
            
            print("🧹 Messenger e risorse pulite correttamente")
            
        return jsonify({'success': True})
    except Exception as e:
        print(f"❌ Errore durante lo shutdown: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/cleanup_temp', methods=['POST'])
def cleanup_temp_files():
    """Pulisce i file temporanei più vecchi di 1 ora"""
    try:
        upload_dir = os.path.expanduser("~/.rns_manager/uploads")
        if not os.path.exists(upload_dir):
            return jsonify({'success': True, 'cleaned': 0})
        
        now = time.time()
        count = 0
        for f in os.listdir(upload_dir):
            fpath = os.path.join(upload_dir, f)
            if os.path.isfile(fpath):
                if now - os.path.getmtime(fpath) > 3600:
                    os.unlink(fpath)
                    count += 1
        
        return jsonify({'success': True, 'cleaned': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ==================== ENDPOINT PER ATTACHMENT ====================

@app.route('/api/attachment/<hash>')
def get_attachment(hash):
    """Recupera un attachment dal database per visualizzazione/download"""
    if not messenger:
        return jsonify({'error': 'Messenger non inizializzato'}), 404
    
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
            if not c.fetchone():
                conn.close()
                return jsonify({'error': 'Tabella non trovata'}), 404
            
            c.execute('SELECT attachments FROM messages WHERE hash = ?', (hash,))
            row = c.fetchone()
            conn.close()
        
        if not row or not row[0]:
            return jsonify({'error': 'Attachment non trovato'}), 404
        
        attachments = json.loads(row[0])
        
        if not attachments:
            return jsonify({'error': 'Nessun attachment'}), 404
        
        att = attachments[0]
        
        file_bytes = None
        filename = None
        mime_type = None
        is_inline = False
        
        if isinstance(att, dict):
            if 'bytes' in att:
                file_bytes = bytes.fromhex(att['bytes'])
                
                if att.get('type') == 'image':
                    img_format = att.get('format', 'jpg')
                    mime_type = f"image/{img_format}"
                    filename = f"image.{img_format}"
                    is_inline = True
                
                elif att.get('type') == 'audio':
                    mode = att.get('mode', 16)
                    if mode < 16:
                        mime_type = "application/octet-stream"
                        filename = f"audio_c2_{mode}.c2"
                    else:
                        mime_type = "audio/ogg"
                        filename = "audio.opus"
                        is_inline = True
                
                elif att.get('type') == 'file':
                    mime_type = "application/octet-stream"
                    filename = att.get('name', 'file.bin')
        
        elif isinstance(att, list) and len(att) == 2:
            att_type = att[0]
            att_data = att[1]
            
            if 'bytes' in att_data:
                file_bytes = bytes.fromhex(att_data['bytes'])
                
                if att_type == 'image':
                    img_format = att_data.get('format', 'jpg')
                    mime_type = f"image/{img_format}"
                    filename = f"image.{img_format}"
                    is_inline = True
                
                elif att_type == 'audio':
                    mode = att_data.get('mode', 16)
                    if mode < 16:
                        mime_type = "application/octet-stream"
                        filename = f"audio_c2_{mode}.c2"
                    else:
                        mime_type = "audio/ogg"
                        filename = "audio.opus"
                        is_inline = True
                
                elif att_type == 'file':
                    mime_type = "application/octet-stream"
                    filename = att_data.get('name', 'file.bin')
        
        if not file_bytes:
            return jsonify({'error': 'Formato attachment non supportato'}), 400
        
        from flask import Response
        disposition = "inline" if is_inline else "attachment"
        return Response(
            file_bytes,
            mimetype=mime_type,
            headers={
                "Content-Disposition": f"{disposition}; filename={filename}",
                "Content-Length": str(len(file_bytes)),
                "Cache-Control": "public, max-age=3600"
            }
        )
        
    except Exception as e:
        print(f"❌ Errore get_attachment: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ==================== ENDPOINT PER PREFERITI ====================

@app.route('/api/favorites', methods=['GET'])
def get_favorites():
    """Restituisce la lista dei preferiti"""
    if not messenger:
        return jsonify([])
    
    favorites = []
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            c.execute('''
                CREATE TABLE IF NOT EXISTS favorites (
                    peer_hash TEXT PRIMARY KEY,
                    added_at REAL
                )
            ''')
            
            c.execute('SELECT peer_hash FROM favorites ORDER BY added_at DESC')
            rows = c.fetchall()
            conn.close()
        
        for row in rows:
            favorites.append(row[0])
        
        return jsonify(favorites)
    except Exception as e:
        print(f"❌ Errore get_favorites: {e}")
        return jsonify([])

@app.route('/api/favorites/add', methods=['POST'])
def add_favorite():
    """Aggiunge un preferito"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    data = request.json
    peer_hash = data.get('peer_hash')
    
    if not peer_hash:
        return jsonify({'success': False, 'error': 'peer_hash mancante'})
    
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            c.execute('''
                CREATE TABLE IF NOT EXISTS favorites (
                    peer_hash TEXT PRIMARY KEY,
                    added_at REAL
                )
            ''')
            
            c.execute('INSERT OR REPLACE INTO favorites (peer_hash, added_at) VALUES (?, ?)',
                     (peer_hash, time.time()))
            
            conn.commit()
            conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/favorites/remove', methods=['POST'])
def remove_favorite():
    """Rimuove un preferito"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    data = request.json
    peer_hash = data.get('peer_hash')
    
    if not peer_hash:
        return jsonify({'success': False, 'error': 'peer_hash mancante'})
    
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            c.execute('DELETE FROM favorites WHERE peer_hash = ?', (peer_hash,))
            conn.commit()
            conn.close()
        
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# ==================== ENDPOINT PER GESTIONE PEER ====================

@app.route('/api/peer/update', methods=['POST'])
def update_peer():
    """Aggiorna gruppo, permessi e note di un peer"""
    if not messenger:
        return jsonify({'success': False, 'error': 'Messenger non inizializzato'})
    
    data = request.json
    identity_hash = data.get('identity_hash')
    
    if not identity_hash:
        return jsonify({'success': False, 'error': 'identity_hash mancante'})
    
    try:
        with messenger.db_lock:
            conn = sqlite3.connect(messenger.peers_db, timeout=10)
            c = conn.cursor()
            
            updates = []
            values = []
            
            if 'group' in data:
                updates.append("group_name=?")
                values.append(data['group'])
            
            if 'trust_level' in data:
                updates.append("trust_level=?")
                values.append(data['trust_level'])
            
            if 'allow_telemetry' in data:
                updates.append("allow_telemetry=?")
                values.append(1 if data['allow_telemetry'] else 0)
            
            if 'allow_commands' in data:
                updates.append("allow_commands=?")
                values.append(1 if data['allow_commands'] else 0)
            
            if 'notes' in data:
                updates.append("notes=?")
                values.append(data['notes'])
            
            if 'favorite' in data:
                updates.append("is_favorite=?")
                values.append(1 if data['favorite'] else 0)
            
            if updates:
                values.append(identity_hash)
                values.append(identity_hash)
                
                c.execute(f'''
                    UPDATE destinations 
                    SET {', '.join(updates)}
                    WHERE identity_hash = ? AND aspect = 'lxmf.delivery'
                ''', values)
                
                conn.commit()
            
            conn.close()
        
        return jsonify({'success': True})
        
    except Exception as e:
        print(f"Errore aggiornamento peer: {e}")
        return jsonify({'success': False, 'error': str(e)})

# ==================== SOCKET.IO EVENTS ====================

@socketio.on('connect')
def handle_connect():
    print('✅ Client web connesso')
    emit('connected', {'status': 'ok', 'identity': messenger.get_hash() if messenger else None})

@socketio.on('disconnect')
def handle_disconnect():
    print('❌ Client web disconnesso')

@socketio.on('ping_server')
def handle_ping():
    emit('pong', {'time': time.time()})

# ==================== PARSER ARGOMENTI ====================

def parse_arguments():
    """Parsing degli argomenti da riga di comando"""
    parser = argparse.ArgumentParser(description='LXMF Web Chat Server')
    parser.add_argument('-i', '--identity', 
                        type=str,
                        help='Percorso al file identità da caricare (es: ~/.rns_manager/storage/Prova1)')
    parser.add_argument('-p', '--port',
                        type=int,
                        default=5010,
                        help='Porta su cui avviare il server (default: 5010)')
    parser.add_argument('--host',
                        type=str,
                        default='0.0.0.0',
                        help='Host su cui avviare il server (default: 0.0.0.0)')
    return parser.parse_args()

# ==================== MAIN ====================

if __name__ == '__main__':
    args = parse_arguments()
    
    upload_dir = os.path.expanduser("~/.rns_manager/uploads")
    os.makedirs(upload_dir, exist_ok=True)
    
    if args.identity:
        print(f"🔑 Tentativo di caricamento identità: {args.identity}")
        
        if os.path.exists(args.identity):
            identity_file = os.path.basename(args.identity)
            storagepath = os.path.expanduser("~/.rns_manager/storage")
            
            expected_path = os.path.join(storagepath, identity_file)
            if os.path.exists(expected_path):
                print(f"✅ Trovato file identità: {expected_path}")
                
                def init_identity():
                    time.sleep(2)
                    print(f"🔄 Inizializzazione automatica con identità: {identity_file}")
                    from flask import request
                    with app.test_request_context():
                        with app.test_client() as client:
                            response = client.post('/api/identity/select', 
                                                  json={'identity': identity_file, 'name': ''})
                            if response.status_code == 200:
                                data = response.get_json()
                                if data.get('success'):
                                    print(f"✅ Identità {identity_file} caricata automaticamente")
                                    socketio.emit('identity_loaded', data)
                                else:
                                    print(f"❌ Errore caricamento identità: {data.get('error')}")
                            else:
                                print(f"❌ Errore HTTP: {response.status_code}")
                
                thread = threading.Thread(target=init_identity)
                thread.daemon = True
                thread.start()
            else:
                print(f"❌ Il file {identity_file} non si trova in {storagepath}")
                print(f"   Copia il file in {storagepath} o usa il selettore nel browser")
        else:
            print(f"❌ File non trovato: {args.identity}")
    
    port = args.port
    host = args.host
    
    print("="*50)
    print("🚀 LXMF WEB SERVER")
    print("="*50)
    print(f"📡 Server in ascolto su http://{host}:{port}")
    if args.identity:
        print(f"🔑 Identità specificata: {args.identity}")
    print(f"🆔 Identity attuale: {messenger.get_hash() if messenger else 'N/D'}")
    print(f"📛 Nome: {messenger.display_name if messenger else 'N/D'}")
    print(f"📁 Upload directory: {upload_dir}")
    print("="*50)
    print("Premi CTRL+C per fermare il server")
    print("="*50)
    
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)