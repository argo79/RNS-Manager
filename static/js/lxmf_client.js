// LXMF Web Client - JavaScript

// Stato globale
let socket = null;
let currentIdentity = null;
let currentDeliveryHash = null;  // AGGIUNTO!
let currentPeer = null;
let currentTab = 'all';
let sortMode = 'time';
let sortDirection = { time: 'desc', hops: 'asc', signal: 'desc' };
let rawView = false;
let map = null;
let mapMarkers = {};
let selectedFile = null;
let mediaRecorder = null;
let audioChunks = [];
let recordingStartTime = null;
let recordingTimer = null;
let config = {};

// Inizializzazione al caricamento della pagina
document.addEventListener('DOMContentLoaded', function() {
    loadIdentities();
    setupSocket();
    setupEventListeners();
});

// Configura Socket.IO
function setupSocket() {
    socket = io();
    
    socket.on('connect', function() {
        console.log('✅ Connesso al server');
        document.getElementById('connectionStatus').className = 'status online';
        
        // Richiedi lo stato corrente per avere currentDeliveryHash
        fetch('/api/status')
            .then(res => res.json())
            .then(data => {
                if (data.status === 'ok') {
                    currentDeliveryHash = data.aspect_hash;
                    console.log('Delivery hash aggiornato:', currentDeliveryHash);
                }
            })
            .catch(err => console.error('Errore recupero stato:', err));
    });
    
    socket.on('disconnect', function() {
        console.log('❌ Disconnesso dal server');
        document.getElementById('connectionStatus').className = 'status offline';
    });
    
    socket.on('new_message', function(msg) {
        console.log('📨 Nuovo messaggio ricevuto:', msg);
        console.log('Current delivery hash:', currentDeliveryHash);
        console.log('Msg from:', msg.from);
        
        if (currentPeer && (msg.from === currentPeer.dest_hash)) {
            addMessageToChat(msg);
        } else {
            console.log('Messaggio non per il peer corrente o peer non selezionato');
        }
        updateRawMonitor(msg);
        updateTelemetry(msg);
    });
    
    socket.on('delivery_update', function(info) {
        console.log('✅ Delivery update:', info);
        
        // Cerca il messaggio nella lista e aggiorna lo stato
        const messages = document.querySelectorAll('.lxmf-message');
        messages.forEach(msgDiv => {
            if (msgDiv.dataset.hash === info.hash) {
                const footer = msgDiv.querySelector('.message-footer');
                if (footer) {
                    // Rimuovi vecchi status
                    const oldStatus = footer.querySelector('.message-status');
                    if (oldStatus) oldStatus.remove();
                    
                    // Aggiungi nuovo status
                    const statusSpan = document.createElement('span');
                    statusSpan.className = `message-status ${info.status}`;
                    statusSpan.innerHTML = info.status === 'delivered' ? '✅ Consegnato' : '❌ Fallito';
                    footer.appendChild(statusSpan);
                }
            }
        });
    });
}

async function importPeersFromCache() {
    try {
        const response = await fetch('/api/peers/import', {
            method: 'POST'
        });
        const data = await response.json();
        if (data.success) {
            alert(`✅ Importati ${data.count} peer da announces.db`);
            refreshPeers();  // Ricarica la lista
        }
    } catch (error) {
        console.error('Errore importazione:', error);
    }
}

// Carica la lista delle identità disponibili
async function loadIdentities() {
    try {
        const response = await fetch('/api/identities');
        const identities = await response.json();
        
        const select = document.getElementById('identitySelect');
        select.innerHTML = '';
        
        if (identities.length === 0) {
            select.innerHTML = '<option value="">Nessuna identità trovata</option>';
            return;
        }
        
        identities.forEach((id, index) => {
            const option = document.createElement('option');
            option.value = id.file;
            option.textContent = `${id.name || id.hash} (${id.hash.substring(0,8)}...)`;
            select.appendChild(option);
        });
        
        // Seleziona la prima identità di default
        select.selectedIndex = 0;
    } catch (error) {
        console.error('Errore caricamento identità:', error);
        document.getElementById('initStatus').textContent = '❌ Errore: ' + error.message;
    }
}

// Seleziona un'identità e avvia la chat
async function selectIdentity() {
    const select = document.getElementById('identitySelect');
    const identityFile = select.value;
    const displayName = document.getElementById('displayNameInput').value.trim();
    
    if (!identityFile) {
        alert('Seleziona un\'identità');
        return;
    }
    
    document.getElementById('initButton').disabled = true;
    document.getElementById('initStatus').textContent = '⏳ Inizializzazione...';
    
    try {
        const response = await fetch('/api/identity/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ identity: identityFile, name: displayName })
        });
        
        const data = await response.json();
        
        if (data.success) {
            currentIdentity = data.identity;
            currentDeliveryHash = data.aspect_hash;  // SALVA QUESTO!
            
            // Aggiorna UI
            document.getElementById('initPanel').style.display = 'none';
            document.getElementById('chatContainer').style.display = 'flex';
            document.getElementById('identityName').textContent = displayName || 'Utente';
            document.getElementById('identityHash').textContent = data.identity.substring(0,16) + '...';
            document.getElementById('sidebarIdentityName').textContent = displayName || 'Utente';
            document.getElementById('sidebarIdentityHash').textContent = data.identity;
            document.getElementById('sidebarAspect').textContent = 'lxmf.delivery';
            document.getElementById('sidebarDeliveryHash').textContent = data.aspect_hash;  // QUESTO È L'HASH GIUSTO!
            document.getElementById('identityStatus').innerHTML = '🟢';
            document.getElementById('connectionStatus').className = 'status online';
            
            // Abilita input
            document.getElementById('messageInput').disabled = false;
            document.getElementById('fileBtn').disabled = false;
            document.getElementById('voiceBtn').disabled = false;
            document.getElementById('sendButton').disabled = false;
            
            // Carica peer e messaggi
            refreshPeers();
            loadConfig();
        } else {
            alert('Errore inizializzazione: ' + (data.error || 'errore sconosciuto'));
        }
    } catch (error) {
        console.error('Errore:', error);
        document.getElementById('initStatus').textContent = '❌ Errore: ' + error.message;
    } finally {
        document.getElementById('initButton').disabled = false;
    }
}

// Carica la lista dei peer
async function refreshPeers() {
    try {
        const response = await fetch('/api/peers');
        const peers = await response.json();
        
        window.allPeers = peers; // Salva per filtro
        
        filterAndDisplayPeers();
    } catch (error) {
        console.error('Errore caricamento peer:', error);
    }
}

// Filtra e visualizza i peer secondo tab e ricerca
function filterAndDisplayPeers() {
    if (!window.allPeers) return;
    
    const searchTerm = document.getElementById('peerSearch').value.toLowerCase();
    const list = document.getElementById('peerList');
    
    let filtered = window.allPeers.filter(peer => {
        // Filtro per tab
        if (currentTab === 'favorites' && !peer.favorite) return false;
        if (currentTab === 'home' && peer.group !== 'home') return false;
        if (currentTab === 'mountain' && peer.group !== 'mountain') return false;
        if (currentTab === 'world' && peer.group !== 'world') return false;
        if (currentTab === 'work' && peer.group !== 'work') return false;
        if (currentTab === 'bot-echo' && !peer.name?.toLowerCase().includes('bot')) return false;
        
        // Filtro per ricerca
        if (searchTerm) {
            return (peer.name?.toLowerCase().includes(searchTerm) || 
                   peer.hash?.toLowerCase().includes(searchTerm));
        }
        return true;
    });
    
    // Ordina
    filtered.sort((a, b) => {
        if (sortMode === 'time') {
            return (b.last_seen || 0) - (a.last_seen || 0);
        } else if (sortMode === 'hops') {
            return (a.hops || 999) - (b.hops || 999);
        } else if (sortMode === 'signal') {
            return (b.snr || -999) - (a.snr || -999);
        }
        return 0;
    });
    
    // Aggiorna conteggio
    document.getElementById('peerCount').textContent = filtered.length;
    document.getElementById('peerCountDisplay').textContent = filtered.length;
    
    // Nella funzione filterAndDisplayPeers, aggiorna la parte che mostra i peer
    list.innerHTML = filtered.map(peer => `
        <div class="lxmf-peer-item ${currentPeer && (peer.dest_hash === currentPeer.dest_hash || peer.hash === currentPeer.hash) ? 'selected' : ''}" 
             onclick="selectPeer('${peer.dest_hash || peer.hash}')">
            <div class="peer-header">
                <span class="peer-name">${peer.name || peer.hash.substring(0,8)}</span>
                <span class="star-icon ${peer.favorite ? 'active' : ''}" onclick="toggleFavorite('${peer.hash}', event)">⭐</span>
            </div>
            <div class="peer-hash">${peer.hash.substring(0,16)}...</div>
            ${peer.dest_hash ? `<div class="peer-dest">📨 ${peer.dest_hash.substring(0,16)}...</div>` : ''}
            <div class="peer-meta">
                <span class="peer-hops">🔄 ${peer.hops || '?'} hop</span>
                <span class="peer-rssi">📶 ${peer.rssi ? peer.rssi + ' dBm' : '?'}</span>
                <span class="peer-snr">📡 ${peer.snr ? peer.snr + ' dB' : '?'}</span>
                <span class="peer-time">${peer.last_seen ? new Date(peer.last_seen*1000).toLocaleTimeString() : ''}</span>
            </div>
            ${peer.q ? `<div class="peer-quality">📊 Qualità: ${peer.q}%</div>` : ''}
        </div>
    `).join('');
}

async function selectPeer(destHash) {
    // PULISCI LA LISTA PRIMA DI CARICARE I NUOVI MESSAGGI
    document.getElementById('messageList').innerHTML = '';
    
    currentPeer = { dest_hash: destHash };
    
    document.getElementById('selectedPeerDisplay').textContent = 
        window.allPeers?.find(p => p.dest_hash === destHash || p.hash === destHash)?.name || destHash.substring(0,8);
    
    // Carica messaggi
    await loadMessages(destHash);
    
    // Aggiorna stile lista
    document.querySelectorAll('.peer-item').forEach(el => el.classList.remove('selected'));
    if (event && event.currentTarget) {
        event.currentTarget.classList.add('selected');
    }
}

async function loadMessages(destHash) {
    try {
        console.log(`📥 Caricamento messaggi per ${destHash.substring(0,8)}...`);
        
        const response = await fetch(`/api/conversation/${destHash}`);
        const messages = await response.json();
        
        console.log(`✅ Ricevuti ${messages.length} messaggi dal server`);
        
        const list = document.getElementById('messageList');
        if (!list) {
            console.error('❌ Elemento messageList non trovato nel DOM');
            return;
        }
        
        // SVUOTA LA LISTA PRIMA DI RICARICARE
        list.innerHTML = '';
        
        // AGGIUNGI TUTTI I MESSAGGI
        messages.forEach(msg => {
            addMessageToChat(msg);
        });
        
        console.log(`✅ Visualizzati ${list.children.length} messaggi nella chat`);
        
        // SCORRI IN BASSO
        list.scrollTop = list.scrollHeight;
        
    } catch (error) {
        console.error('❌ Errore caricamento messaggi:', error);
    }
}

// Funzione helper per ordinare i messaggi per tempo
function sortMessagesByTime() {
    const list = document.getElementById('messageList');
    const items = Array.from(list.children);
    
    items.sort((a, b) => {
        const timeA = parseFloat(a.dataset.time || 0);
        const timeB = parseFloat(b.dataset.time || 0);
        return timeA - timeB;
    });
    
    // Riordina senza cancellare
    items.forEach(item => list.appendChild(item));
}

function addMessageToChat(msg) {
    const list = document.getElementById('messageList');
    
    const isIncoming = msg.incoming !== undefined ? msg.incoming : (msg.from !== currentDeliveryHash);
    
    const messageDiv = document.createElement('div');
    messageDiv.className = `lxmf-message ${isIncoming ? 'incoming' : 'outgoing'}`;
    messageDiv.dataset.hash = msg.hash;
    messageDiv.dataset.time = msg.time || msg.timestamp;
    
    const msgDate = new Date((msg.time || msg.timestamp) * 1000);
    const timeStr = msgDate.toLocaleTimeString();
    const dateStr = msgDate.toLocaleDateString();
    
    // Determina il tipo di messaggio
    let messageType = 'text';
    let typeIcon = '💬';

    if (msg.attachments && Array.isArray(msg.attachments) && msg.attachments.length > 0) {
        const firstAttachment = msg.attachments[0];
        
        let firstType = null;
        if (Array.isArray(firstAttachment)) {
            firstType = firstAttachment[0];
        } else if (firstAttachment && typeof firstAttachment === 'object') {
            firstType = firstAttachment.type;
        }
        
        if (firstType === 'image') {
            messageType = 'image';
            typeIcon = '🖼️';
        } else if (firstType === 'audio' || firstType === 'audio_raw') {
            messageType = 'audio';
            typeIcon = '🎵';
        } else if (firstType === 'file') {
            messageType = 'file';
            typeIcon = '📎';
        }
    } else if (msg.command) {
        messageType = 'command';
        typeIcon = '⚙️';
    } else if (msg.telemetry || msg.telemetry_data) {
        messageType = 'telemetry';
        typeIcon = '📊';
    }
    
    // Info tecniche
    let techInfo = [];
    if (msg.hops) techInfo.push(`🔄 Hops: ${msg.hops}`);
    if (msg.rssi) {
        let rssiClass = msg.rssi > -80 ? 'good' : (msg.rssi > -100 ? 'fair' : 'poor');
        techInfo.push(`📶 RSSI: <span class="${rssiClass}">${msg.rssi} dBm</span>`);
    }
    if (msg.snr) {
        let snrClass = msg.snr > 8 ? 'good' : (msg.snr > 3 ? 'fair' : 'poor');
        techInfo.push(`📡 SNR: <span class="${snrClass}">${msg.snr} dB</span>`);
    }
    if (msg.q) techInfo.push(`📊 Qualità: ${msg.q}%`);
    if (msg.packed_size) {
        techInfo.push(`📦 Dimensione: ${msg.packed_size} byte`);
    }
    
    // Metodo di consegna
    let methodText = '';
    if (msg.method === 'direct') methodText = '🔗 Diretto';
    else if (msg.method === 'opportunistic') methodText = '📨 Opportunistico';
    else if (msg.method === 'propagated') methodText = '📦 Propagato';
    else methodText = msg.method || '';
    
    // Stato
    let statusText = '';
    if (msg.status === 'delivered') {
        statusText = '<span class="message-status delivered">✅ Consegnato</span>';
    } else if (msg.status === 'failed') {
        statusText = '<span class="message-status failed">❌ Fallito</span>';
    } else if (msg.status === 'sending') {
        statusText = '<span class="message-status sending">⏳ Invio...</span>';
    }
    
    // Mittente/destinatario
    const peerDisplay = isIncoming 
        ? `Da: ${msg.from.substring(0,8)}...` 
        : `A: ${msg.to ? msg.to.substring(0,8) : '???'}...`;
    
    // GESTIONE ALLEGATI - VERSIONE CORRETTA
    let attachmentsHtml = '';
    if (msg.attachments && Array.isArray(msg.attachments) && msg.attachments.length > 0) {
        attachmentsHtml = '<div class="message-attachments">';
        
        msg.attachments.forEach(att => {
            let type, filename, fileUrl;
            
            if (Array.isArray(att)) {
                type = att[0];
                filename = att[1];
                fileUrl = `/download/${filename}`;
            } else if (att && typeof att === 'object') {
                type = att.type;
                filename = att.filename;
                fileUrl = att.url || `/download/${filename}`;
            } else {
                console.log('⚠️ Attachment non valido:', att);
                return;
            }
            
            if (!filename || filename.startsWith('data:')) {
                console.log('⚠️ IGNORATO: data URI o filename mancante');
                return;
            }
            
            const ext = filename.split('.').pop().toLowerCase();
            
            console.log('🎯 DEBUG allegato:', {type, filename, fileUrl, ext});
            
            // IMMAGINI
            if (type === 'image' || ext.match(/^(jpg|jpeg|png|gif|webp|bmp)$/)) {
                attachmentsHtml += `
                    <div class="attachment-image" onclick="showImage('${filename}')">
                        <img src="${fileUrl}" 
                             class="attachment-thumb" 
                             loading="lazy"
                             onerror="console.error('❌ ERRORE CARICAMENTO:', this.src); this.style.display='none'; this.parentElement.innerHTML='❌ Immagine non trovata: ${filename}';">
                        <div class="attachment-filename">${filename}</div>
                    </div>
                `;
            }
            // AUDIO - WAV
            else if (type === 'audio' && ext === 'wav') {
                attachmentsHtml += `
                    <div class="attachment-audio">
                        <audio controls preload="metadata" style="width: 100%;" 
                               onerror="console.error('❌ ERRORE AUDIO:', this.src); this.parentElement.innerHTML='❌ Audio non trovato: ${filename}';">
                            <source src="${fileUrl}" type="audio/wav">
                        </audio>
                        <div class="audio-filename">${filename}</div>
                    </div>
                `;
            }
            // AUDIO - OPUS (supportato dal browser)
            else if (type === 'audio' && ext === 'opus') {
                attachmentsHtml += `
                    <div class="attachment-audio">
                        <audio controls preload="metadata" style="width: 100%;"
                               onerror="console.error('❌ ERRORE AUDIO:', this.src); this.parentElement.innerHTML='❌ Audio non trovato: ${filename}';">
                            <source src="${fileUrl}" type="audio/opus">
                        </audio>
                        <div class="audio-filename">${filename}</div>
                    </div>
                `;
            }
            // IGNORA ALTRI AUDIO (raw codec2)
            else if (ext.match(/^(c2_|aud)$/)) {
                console.log('⚠️ Ignorato audio raw non supportato:', filename);
            }
            // FILE
            else {
                attachmentsHtml += `
                    <div class="attachment-file" onclick="downloadFile('${filename}')">
                        📎 ${filename}
                    </div>
                `;
            }
        });
        attachmentsHtml += '</div>';
    }
    
    // Campi LXMF
    let fieldsHtml = '';
    if (msg.fields && Object.keys(msg.fields).length > 0) {
        fieldsHtml = '<div class="message-fields">';
        fieldsHtml += '<div class="fields-title">📋 Campi LXMF:</div>';
        fieldsHtml += '<div class="fields-grid">';
        
        for (const [key, value] of Object.entries(msg.fields)) {
            let displayValue = value;
            if (typeof value === 'object') {
                displayValue = JSON.stringify(value).substring(0, 50);
                if (JSON.stringify(value).length > 50) displayValue += '...';
            }
            fieldsHtml += `<div class="field-item"><span class="field-key">${key}:</span> <span class="field-value">${displayValue}</span></div>`;
        }
        fieldsHtml += '</div></div>';
    }
    
    // HTML COMPLETO
    messageDiv.innerHTML = `
        <div class="message-header">
            <span>${typeIcon} ${isIncoming ? '📨' : '📤'} ${peerDisplay}</span>
            <span class="message-time">${dateStr} ${timeStr}</span>
        </div>
        <div class="message-content">${msg.content || ''}</div>
        ${attachmentsHtml}
        ${fieldsHtml}
        <div class="message-footer">
            <span class="message-method">${methodText}</span>
            ${statusText}
        </div>
        ${techInfo.length > 0 ? `<div class="message-tech">${techInfo.join(' | ')}</div>` : ''}
        ${rawView && msg.raw_hex ? `
        <div class="message-raw">
            <div class="raw-hex">HEX: ${msg.raw_hex.substring(0,64)}${msg.raw_hex.length > 64 ? '...' : ''}</div>
            <div class="raw-ascii">ASCII: ${msg.raw_ascii?.substring(0,64) || ''}</div>
        </div>
        ` : ''}
    `;
    
    list.appendChild(messageDiv);
    list.scrollTop = list.scrollHeight;
}

async function sendMessage() {
    if (!currentPeer) {
        alert('Seleziona un peer prima di inviare');
        return;
    }
    
    const input = document.getElementById('messageInput');
    const content = input.value.trim();
    
    if (!content && !selectedFile) return;
    
    const tempId = 'temp_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
    
    const tempMsg = {
        hash: tempId,
        from: currentDeliveryHash,
        to: currentPeer.dest_hash,
        content: content,
        time: Date.now() / 1000,
        method: 'opportunistic',
        status: 'sending',
        incoming: false
    };
    
    if (selectedFile) {
        tempMsg.attachments = [[
            selectedFile.type.startsWith('image/') ? 'image' : 
            selectedFile.type.startsWith('audio/') ? 'audio' : 'file',
            selectedFile.name
        ]];
        tempMsg.packed_size = selectedFile.size;
        
        if (selectedFile.type.startsWith('image/')) {
            const reader = new FileReader();
            reader.onload = function(e) {
                setTimeout(() => {
                    const tempDiv = document.querySelector(`.lxmf-message[data-hash="${tempId}"]`);
                    if (tempDiv) {
                        const img = tempDiv.querySelector('img');
                        if (img) {
                            img.src = e.target.result;
                        }
                    }
                }, 100);
            };
            reader.readAsDataURL(selectedFile);
        }
    }
    
    addMessageToChat(tempMsg);
    
    const formData = new FormData();
    formData.append('destination', currentPeer.dest_hash);
    formData.append('content', content);
    
    if (selectedFile) {
        formData.append('file', selectedFile);
        
        if (selectedFile.type.startsWith('image/')) {
            formData.append('type', 'image');
        } else if (selectedFile.type.startsWith('audio/')) {
            formData.append('type', 'audio');
            
            // Usa la preferenza dal config
            let audioMode = 8; // Default Codec2 2400bps
            const pref = currentConfig?.preferred_audio || 'c2_2400';
            
            const modeMap = {
                'c2_3200': 9,
                'c2_2400': 8,
                'c2_1200': 4,
                'c2_700': 3,
                'opus': 16
            };
            
            audioMode = modeMap[pref] || 8;
            formData.append('audio_mode', audioMode.toString());
            
            console.log(`🎵 Invio audio con mode=${audioMode} (${pref})`);
        } else {
            formData.append('type', 'file');
        }
    }
    
    try {
        const response = await fetch('/api/send', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.success) {
            input.value = '';
            selectedFile = null;
            document.getElementById('filePreview').innerHTML = '';
            document.getElementById('filePreview').style.display = 'none';
            
            const tempDiv = document.querySelector(`.lxmf-message[data-hash="${tempId}"]`);
            if (tempDiv) {
                console.log('🔍 Elementi trovati nel messaggio temporaneo:', {
                    img: tempDiv.querySelector('img'),
                    filenameDiv: tempDiv.querySelector('.attachment-filename, .audio-filename'),
                    tempDiv: tempDiv,
                    hash: tempDiv.dataset.hash
                });
                
                tempDiv.dataset.hash = data.hash;
                
                if (data.filename) {
                    const img = tempDiv.querySelector('img');
                    if (img) {
                        console.log('🖼️ Aggiorno img.src da', img.src, 'a', `/download/${data.filename}`);
                        img.src = `/download/${data.filename}`;
                    }
                    
                    const filenameDiv = tempDiv.querySelector('.attachment-filename, .audio-filename');
                    if (filenameDiv) {
                        console.log('📝 Aggiorno filename da', filenameDiv.textContent, 'a', data.filename);
                        filenameDiv.textContent = data.filename;
                    }
                }
                
                const footer = tempDiv.querySelector('.message-footer');
                if (footer && data.method) {
                    const methodSpan = footer.querySelector('.message-method');
                    if (methodSpan) {
                        let methodText = '';
                        if (data.method === 'direct') methodText = '🔗 Diretto';
                        else if (data.method === 'opportunistic') methodText = '📨 Opportunistico';
                        else if (data.method === 'propagated') methodText = '📦 Propagato';
                        methodSpan.innerHTML = methodText;
                    }
                }
            }
        } else {
            const tempDiv = document.querySelector(`.lxmf-message[data-hash="${tempId}"]`);
            if (tempDiv) {
                const footer = tempDiv.querySelector('.message-footer');
                if (footer) {
                    const statusSpan = document.createElement('span');
                    statusSpan.className = 'message-status failed';
                    statusSpan.innerHTML = '❌ Fallito';
                    footer.appendChild(statusSpan);
                }
            }
            alert('❌ Errore: ' + data.error);
        }
    } catch (error) {
        console.error('Errore invio:', error);
        
        const tempDiv = document.querySelector(`.lxmf-message[data-hash="${tempId}"]`);
        if (tempDiv) {
            const footer = tempDiv.querySelector('.message-footer');
            if (footer) {
                const statusSpan = document.createElement('span');
                statusSpan.className = 'message-status failed';
                statusSpan.innerHTML = '❌ Errore di connessione';
                footer.appendChild(statusSpan);
            }
        }
        
        alert('❌ Errore di connessione');
    }
}

// Gestione file
function handleFileSelect() {
    const input = document.getElementById('fileInput');
    selectedFile = input.files[0];
    
    if (selectedFile) {
        const preview = document.getElementById('filePreview');
        preview.innerHTML = `
            <div class="file-preview-item">
                <span>📎 ${selectedFile.name}</span>
                <span>${(selectedFile.size/1024).toFixed(1)} KB</span>
                <button onclick="clearFile()">✕</button>
            </div>
        `;
        preview.style.display = 'block';
    }
}

function clearFile() {
    selectedFile = null;
    document.getElementById('fileInput').value = '';
    document.getElementById('filePreview').innerHTML = '';
    document.getElementById('filePreview').style.display = 'none';
}

// ============================================
// REGISTRAZIONE VOCALE - VERSIONE COMPLETA
// ============================================
let mediaRecorder = null;
let audioChunks = [];
let recordingStartTime = null;
let recordingTimer = null;
let audioStream = null;

async function toggleVoiceRecording() {
    if (!currentPeer) {
        alert("Seleziona un peer prima");
        return;
    }
    
    const voiceBtn = document.getElementById('voiceBtn');
    
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        stopVoiceRecording();
        return;
    }
    
    try {
        audioStream = await navigator.mediaDevices.getUserMedia({ 
            audio: {
                channelCount: 1,
                sampleRate: 48000,
                sampleSize: 16,
                echoCancellation: true,
                noiseSuppression: true
            } 
        });
        
        // Registra in formato WAV (PCM)
        const options = { 
            mimeType: 'audio/wav',
            audioBitsPerSecond: 128000
        };
        
        // Verifica se il browser supporta audio/wav
        if (!MediaRecorder.isTypeSupported('audio/wav')) {
            console.warn('audio/wav non supportato, uso default');
            mediaRecorder = new MediaRecorder(audioStream);
        } else {
            mediaRecorder = new MediaRecorder(audioStream, options);
        }
        
        audioChunks = [];
        
        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) {
                audioChunks.push(event.data);
            }
        };
        
        mediaRecorder.onstop = async () => {
            if (audioStream) {
                audioStream.getTracks().forEach(track => track.stop());
            }
            
            if (audioChunks.length === 0) {
                console.error("Nessun dato audio");
                resetVoiceUI(voiceBtn);
                return;
            }
            
            // Crea blob WAV
            const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
            
            console.log(`🎵 Audio registrato: ${formatBytes(audioBlob.size)}`);
            
            // Genera nome file temporaneo
            const tempHash = Array.from(crypto.getRandomValues(new Uint8Array(16)))
                .map(b => b.toString(16).padStart(2, '0'))
                .join('');
            
            const fileName = `${tempHash}_audio.wav`;
            const file = new File([audioBlob], fileName, { type: 'audio/wav' });
            
            selectedFiles = [file];
            
            document.getElementById('filePreview').innerHTML = `
                <div class="lxmf-file-tag">
                    <span>🎤 ${fileName} (${formatBytes(audioBlob.size)})</span>
                    <span class="remove" onclick="removeFile(0)">✕</span>
                </div>
            `;
            
            resetVoiceUI(voiceBtn);
        };
        
        mediaRecorder.start(1000);
        recordingStartTime = Date.now();
        voiceBtn.classList.add('recording');
        voiceBtn.innerHTML = '⏹️';
        
        document.getElementById('voiceRecordingIndicator').style.display = 'block';
        document.getElementById('voiceRecordingIndicator').innerHTML = '<span>🔴 REGISTRAZIONE... <span id="voiceTimer">0s</span></span>';
        
        if (recordingTimer) clearInterval(recordingTimer);
        recordingTimer = setInterval(() => {
            const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
            document.getElementById('voiceTimer').textContent = `${elapsed}s`;
        }, 100);
        
    } catch (e) {
        console.error("Errore:", e);
        alert("Errore microfono: " + e.message);
        resetVoiceUI(voiceBtn);
    }
}

function resetVoiceUI(voiceBtn) {
    document.getElementById('voiceRecordingIndicator').style.display = 'none';
    if (voiceBtn) {
        voiceBtn.classList.remove('recording');
        voiceBtn.innerHTML = '🎤';
    }
    if (recordingTimer) {
        clearInterval(recordingTimer);
        recordingTimer = null;
    }
}

function stopVoiceRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }
}

function toggleRawView() {
    rawView = !rawView;
    document.getElementById('rawViewBtn').style.background = rawView ? '#88ff88' : '#2a2a2a';
    document.getElementById('rawViewBtn').style.color = rawView ? '#0a0a0a' : '#e0e0e0';
    
    // Ricarica i messaggi dal server (non solo dalla memoria)
    if (currentPeer) {
        loadMessages(currentPeer.dest_hash);
    }
}

// Aggiorna monitor raw
function updateRawMonitor(msg) {
    const rawContent = document.getElementById('rawContent');
    
    const packetDiv = document.createElement('div');
    packetDiv.className = 'raw-packet';
    
    const time = new Date((msg.time || msg.timestamp) * 1000).toLocaleTimeString();
    const fromShort = msg.from ? msg.from.substring(0,8) : '???';
    
    packetDiv.innerHTML = `
        <div class="raw-header">
            <span>📦 ${fromShort}...</span>
            <span>${time}</span>
        </div>
        <div class="raw-hex">${msg.raw_hex?.substring(0,128) || 'N/D'}${msg.raw_hex?.length > 128 ? '...' : ''}</div>
        <div class="raw-ascii">${msg.raw_ascii?.substring(0,128) || ''}</div>
    `;
    
    rawContent.insertBefore(packetDiv, rawContent.firstChild);
    
    // Mantieni solo gli ultimi 50 pacchetti
    while (rawContent.children.length > 50) {
        rawContent.removeChild(rawContent.lastChild);
    }
}

// Aggiorna telemetria
function updateTelemetry(msg) {
    // Implementa se necessario
}

// Aggiorna stato messaggio
function updateMessageStatus(info) {
    console.log('📊 Stato messaggio aggiornato:', info);
    const messageElements = document.querySelectorAll('.message');
    messageElements.forEach(el => {
        if (el.dataset.hash === info.hash) {
            let statusIndicator = el.querySelector('.message-status');
            if (!statusIndicator) {
                statusIndicator = document.createElement('div');
                statusIndicator.className = 'message-status';
                el.querySelector('.message-footer').appendChild(statusIndicator);
            }
            
            if (info.status === 'delivered') {
                statusIndicator.innerHTML = ' ✅ Consegnato';
                statusIndicator.style.color = '#88ff88';
            } else if (info.status === 'failed') {
                statusIndicator.innerHTML = ' ❌ Fallito';
                statusIndicator.style.color = '#ff8888';
            }
        }
    });
}

// Cambia tab peer
function showPeerTab(tab) {
    currentTab = tab;
    
    // Aggiorna active tabs
    document.querySelectorAll('.lxmf-tab').forEach(t => t.classList.remove('active'));
    const tabMap = {
        'all': 'tabAll',
        'favorites': 'tabFav',
        'home': 'tabHome',
        'mountain': 'tabMountain',
        'world': 'tabWorld',
        'work': 'tabWork',
        'bot-echo': 'tabBotEcho'
    };
    if (tabMap[tab]) {
        document.getElementById(tabMap[tab]).classList.add('active');
    }
    
    filterAndDisplayPeers();
}

// Cambia modalità ordinamento
function setSortMode(mode) {
    sortMode = mode;
    
    // Aggiorna active buttons
    document.querySelectorAll('.lxmf-sort-btn').forEach(btn => btn.classList.remove('active'));
    const btnMap = {
        'time': 'sortTimeBtn',
        'hops': 'sortHopsBtn',
        'signal': 'sortSignalBtn'
    };
    if (btnMap[mode]) {
        document.getElementById(btnMap[mode]).classList.add('active');
    }
    
    filterAndDisplayPeers();
}

// Cancella ricerca
function clearSearch() {
    document.getElementById('peerSearch').value = '';
    filterAndDisplayPeers();
}

// Gestione preferiti
function toggleFavorite(peerHash, event) {
    event.stopPropagation();
    const star = event.target;
    star.classList.toggle('active');
}

// Pulisci messaggi
function clearMessages() {
    document.getElementById('messageList').innerHTML = '<div class="lxmf-no-peer">👈 Seleziona un peer per iniziare</div>';
}

// Esporta conversazione
function exportConversation() {
    if (!currentPeer) return;
    
    const messages = document.querySelectorAll('.message');
    let text = '';
    
    messages.forEach(msg => {
        const isIncoming = msg.classList.contains('incoming');
        const content = msg.querySelector('.message-content')?.textContent || '';
        const time = msg.querySelector('.message-header span:last-child')?.textContent || '';
        text += `${time} ${isIncoming ? '<' : '>'}: ${content}\n`;
    });
    
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `conversation_${currentPeer.dest_hash.substring(0,8)}.txt`;
    a.click();
}

// Cambia tab raw
function switchRawTab(tab) {
    document.querySelectorAll('.lxmf-raw-tab').forEach(t => t.classList.remove('active'));
    const tabMap = {
        'packets': 'tabPackets',
        'history': 'tabHistory',
        'config': 'tabConfig'
    };
    if (tabMap[tab]) {
        document.getElementById(tabMap[tab]).classList.add('active');
    }
    
    document.getElementById('rawContent').style.display = tab === 'packets' ? 'block' : 'none';
    document.getElementById('historyContent').style.display = tab === 'history' ? 'block' : 'none';
    document.getElementById('configContent').style.display = tab === 'config' ? 'block' : 'none';
}

// Pulisci monitor log
function clearMonitorLog() {
    document.getElementById('rawContent').innerHTML = '<div style="color: #888; text-align: center; margin-top: 20px;">Nessun pacchetto ricevuto</div>';
}

// Carica configurazione
async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        const data = await response.json();
        
        document.getElementById('propagationNode').value = data.propagation_node || '';
        document.getElementById('deliveryMode').value = data.delivery_mode || 'opportunistic';
        document.getElementById('stampCost').value = data.stamp_cost || 16;
        document.getElementById('tryPropagationOnFail').checked = data.try_propagation_on_fail !== false;
        
        config = data;
    } catch (error) {
        console.error('Errore caricamento config:', error);
    }
}

function loadAudioConfig() {
    const preferredAudio = document.getElementById('preferredAudio');
    const audioMode = document.getElementById('audioMode');
    const codec2Options = document.getElementById('codec2Options');
    
    // Mostra/nascondi opzioni Codec2 in base alla scelta
    preferredAudio.addEventListener('change', function() {
        if (this.value === 'codec2') {
            codec2Options.style.display = 'block';
        } else {
            codec2Options.style.display = 'none';
        }
    });
    
    // Carica valori dal config
    if (config.preferred_audio) {
        if (config.preferred_audio.startsWith('c2_')) {
            preferredAudio.value = 'codec2';
            const mode = config.preferred_audio.replace('c2_', '');
            audioMode.value = mode;
            codec2Options.style.display = 'block';
        } else {
            preferredAudio.value = config.preferred_audio;
            codec2Options.style.display = 'none';
        }
    }
    
    // Mostra disponibilità Codec2
    document.getElementById('codec2Status').textContent = 
        config.codec2_available ? '✅ Disponibile' : '❌ Non disponibile';
}

// Modifica saveConfig per includere audio preferences
async function saveConfig() {
    const preferredAudio = document.getElementById('preferredAudio').value;
    let audioPref = preferredAudio;
    
    if (preferredAudio === 'codec2') {
        const mode = document.getElementById('audioMode').value;
        audioPref = `c2_${mode}`;
    }
    
    const newConfig = {
        propagation_node: document.getElementById('propagationNode').value || null,
        delivery_mode: document.getElementById('deliveryMode').value,
        stamp_cost: parseInt(document.getElementById('stampCost').value) || 16,
        try_propagation_on_fail: document.getElementById('tryPropagationOnFail').checked,
        preferred_audio: audioPref  // <-- SALVA LA PREFERENZA!
    };
    
    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newConfig)
        });
        
        const data = await response.json();
        
        if (data.success) {
            document.getElementById('configStatus').textContent = '✅ Configurazione salvata';
            config = {...config, ...newConfig};
        } else {
            document.getElementById('configStatus').textContent = '❌ Errore: ' + data.error;
        }
    } catch (error) {
        document.getElementById('configStatus').textContent = '❌ Errore: ' + error.message;
    }
}

// Salva configurazione
async function saveConfig() {
    const newConfig = {
        propagation_node: document.getElementById('propagationNode').value || null,
        delivery_mode: document.getElementById('deliveryMode').value,
        stamp_cost: parseInt(document.getElementById('stampCost').value) || 16,
        try_propagation_on_fail: document.getElementById('tryPropagationOnFail').checked
    };
    
    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(newConfig)
        });
        
        const data = await response.json();
        
        if (data.success) {
            document.getElementById('configStatus').textContent = '✅ Configurazione salvata';
            config = newConfig;
        } else {
            document.getElementById('configStatus').textContent = '❌ Errore: ' + data.error;
        }
    } catch (error) {
        document.getElementById('configStatus').textContent = '❌ Errore: ' + error.message;
    }
}

// Mostra immagine in modal
function showImage(path) {
    const modal = document.getElementById('mediaModal');
    const img = document.getElementById('modalImage');
    const video = document.getElementById('modalVideo');
    
    img.style.display = 'block';
    video.style.display = 'none';
    img.src = `/download/${path.split('/').pop()}`;
    
    modal.style.display = 'block';
}

// Chiudi modal
function closeModal() {
    document.getElementById('mediaModal').style.display = 'none';
}

// Event listeners
function setupEventListeners() {
    // Invio con Enter
    document.getElementById('messageInput').addEventListener('keypress', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    
    // Ricerca con debounce
    let searchTimeout;
    document.getElementById('peerSearch').addEventListener('input', function() {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(filterAndDisplayPeers, 300);
    });
}