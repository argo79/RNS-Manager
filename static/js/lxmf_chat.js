// static/js/lxmf_chat.js
// ============================================
// STATO GLOBALE
// ============================================
let currentIdentity = null;
let currentPeer = null;
let peers = [];
let messages = [];
let pollInterval = null;
let peerInterval = null;
let progressInterval = null;
let lastMessagesJson = '';
let sortMode = 'time';
let timeOrder = 'desc';
let hopsOrder = 'asc';
let signalOrder = 'desc';
let selectedFiles = [];
let activeTransfers = new Map();

// RAW view - per i messaggi
let rawViewActive = false;

// Monitor - per pacchetti in tempo reale
let monitorActive = false;
let incomingPackets = {};

// Notifiche peer
let unreadPeers = new Set();

// Database telemetria per peer
let telemetryDatabase = {};

// Mappa Leaflet
let mapInstance = null;
let mapMarker = null;
let currentMapStyle = 'topo';

// Cache messaggi per debug
let messageCache = new Map();
let debugLogs = [];

// Variabile per il tab corrente
let currentPeerTab = 'all';

// Stato sincronizzazione
let isSyncing = false;
let syncInterval = null;

// Variabili configurazione
let currentConfig = {};

// ============================================
// FUNZIONI DI DEBUG
// ============================================
function debugLog(message, data = null) {
    const time = new Date().toLocaleTimeString();
    const logEntry = `[${time}] ${message}` + (data ? `: ${JSON.stringify(data).substring(0, 200)}` : '');
    
    console.log('🔍 DEBUG:', logEntry);
    
    debugLogs.unshift(logEntry);
    if (debugLogs.length > 50) debugLogs.pop();
    
    const debugContent = document.getElementById('debugContent');
    if (debugContent) {
        let html = '';
        debugLogs.slice(0, 20).forEach(log => {
            html += `<div style="border-bottom: 1px solid #333; padding: 2px 0; color: #0f0;">${escapeHtml(log)}</div>`;
        });
        debugContent.innerHTML = html;
    }
}

// ============================================
// PARSING MESSAGGI TELEMETRIA
// ============================================
function parseTelemetryMessage(text) {
    if (!text || typeof text !== 'string') return { type: 'text', content: text };
    
    debugLog('Parsing messaggio:', text.substring(0, 100));
    
    if (text.includes('Uptime:') && text.includes('CPU:') && text.includes('RAM:')) {
        debugLog('Rilevato messaggio TelemetryBot');
        
        const uptimeMatch = text.match(/Uptime:\s*([^ ]+\s+[^ ]+)/);
        const cpuMatch = text.match(/CPU:\s*([0-9.]+%)/);
        const ramMatch = text.match(/RAM:\s*([0-9.]+%)/);
        const diskMatch = text.match(/Disk:\s*([0-9.]+%)/);
        const netMatch = text.match(/Net sent:\s*([0-9]+) KB,\s*recv:\s*([0-9]+) KB/);
        
        const data = {
            uptime: uptimeMatch ? uptimeMatch[1] : null,
            cpu: cpuMatch ? cpuMatch[1] : null,
            ram: ramMatch ? ramMatch[1] : null,
            disk: diskMatch ? diskMatch[1] : null,
            net_sent: netMatch ? netMatch[1] : null,
            net_recv: netMatch ? netMatch[2] : null
        };
        
        debugLog('Dati TelemetryBot estratti:', data);
        return { type: 'telemetry', data, raw: text };
    }
    
    if (text.includes(':')) {
        const lines = text.split('\n');
        const data = {};
        let structured = false;
        
        lines.forEach(line => {
            const parts = line.split(':');
            if (parts.length >= 2) {
                const key = parts[0].trim();
                const value = parts.slice(1).join(':').trim();
                if (key && value && !key.includes(' ') && value.length < 50) {
                    data[key] = value;
                    structured = true;
                }
            }
        });
        
        if (structured && Object.keys(data).length > 1) {
            debugLog('Messaggio strutturato:', data);
            return { type: 'structured', data, raw: text };
        }
    }
    
    if (text.split(' ').length > 10 && text.length > 80) {
        const words = text.split(' ');
        const lines = [];
        let currentLine = '';
        
        words.forEach(word => {
            if ((currentLine + ' ' + word).length < 40) {
                currentLine += (currentLine ? ' ' : '') + word;
            } else {
                if (currentLine) lines.push(currentLine);
                currentLine = word;
            }
        });
        if (currentLine) lines.push(currentLine);
        
        return { type: 'wrapped', lines, raw: text };
    }
    
    return { type: 'text', content: text };
}

function formatMessageContent(msg) {
    if (!msg || !msg.content) return '';
    
    const parsed = parseTelemetryMessage(msg.content);
    
    if (parsed.type === 'telemetry' && parsed.data) {
        const d = parsed.data;
        let html = '<div class="message-telemetry">';
        html += '<div style="color: #88ff88; font-weight: bold; margin-bottom: 4px;">📊 TELEMETRY BOT</div>';
        html += '<div class="message-telemetry-row">';
        
        if (d.uptime) html += `<span><span style="color:#888;">⏱️</span> ${d.uptime}</span>`;
        if (d.cpu) html += `<span><span style="color:#888;"> -CPU:</span> ${d.cpu}</span>`;
        if (d.ram) html += `<span><span style="color:#888;"> -RAM:</span> ${d.ram}</span>`;
        if (d.disk) html += `<span><span style="color:#888;"> -DISK:</span> ${d.disk}</span>`;
        
        html += '</div><div class="message-telemetry-row" style="margin-top:4px;">';
        
        if (d.net_sent) html += `<span><span style="color:#888;"> 📤 OUT </span> ${d.net_sent} KB</span>`;
        if (d.net_recv) html += `<span><span style="color:#888;"> 📥 IN </span> ${d.net_recv} KB</span>`;
        
        html += '</div></div>';
        return html;
    }
    
    if (parsed.type === 'structured' && parsed.data) {
        const d = parsed.data;
        let html = '<div class="message-telemetry">';
        html += '<div class="message-telemetry-row">';
        
        for (let [key, value] of Object.entries(d)) {
            html += `<span><span style="color:#888;">${key}</span> ${value}</span>`;
        }
        
        html += '</div></div>';
        return html;
    }
    
    if (parsed.type === 'wrapped' && parsed.lines) {
        let html = '<div class="lxmf-message-content formatted">';
        parsed.lines.forEach(line => {
            html += escapeHtml(line) + '<br>';
        });
        html += '</div>';
        return html;
    }
    
    return `<div class="lxmf-message-content">${escapeHtml(parsed.content || msg.content)}</div>`;
}

// ============================================
// FUNZIONI DI UTILITY
// ============================================
function updateConnectionStatus(status) {
    const statusEl = document.getElementById('connectionStatus');
    statusEl.className = `status ${status}`;
}

function updateIdentityDisplay() {
    if (currentIdentity) {
        console.log("🆔 Aggiorno display identità:", currentIdentity);
        
        document.getElementById('identityStatus').innerHTML = '✅';
        document.getElementById('identityName').innerHTML = currentIdentity.name || 'Sconosciuto';
        
        if (currentIdentity.identity_hash) {
            let identityHash = currentIdentity.identity_hash;
            identityHash = identityHash.replace(/[<>:\s]/g, '');
            document.getElementById('identityHash').innerHTML = identityHash.substring(0, 32);
            console.log("📇 Header identity hash:", identityHash.substring(0, 32));
        } else {
            document.getElementById('identityHash').innerHTML = '';
        }
        
        document.getElementById('sidebarIdentityName').innerHTML = currentIdentity.name || '-';
        document.getElementById('sidebarAspect').innerHTML = '🔗 lxmf.delivery';

        if (currentIdentity.delivery_hash) {
            let deliveryHash = currentIdentity.delivery_hash;
            deliveryHash = deliveryHash.replace(/[<>:\s]/g, '');
            document.getElementById('sidebarDeliveryHash').innerHTML = deliveryHash.substring(0, 32);
            console.log("📦 Sidebar delivery hash:", deliveryHash.substring(0, 32));
        } else {
            document.getElementById('sidebarDeliveryHash').innerHTML = '';
            console.log("⚠️ Nessun delivery_hash in currentIdentity");
            
            if (currentIdentity.address) {
                let address = currentIdentity.address.replace(/[<>:\s]/g, '');
                document.getElementById('sidebarDeliveryHash').innerHTML = address.substring(0, 32);
                console.log("⚠️ Usato address come fallback per delivery");
            }
        }
        
        if (currentIdentity.identity_hash) {
            let identityHash = currentIdentity.identity_hash.replace(/[<>:\s]/g, '');
            document.getElementById('sidebarIdentityHash').innerHTML = identityHash.substring(0, 32);
        } else {
            document.getElementById('sidebarIdentityHash').innerHTML = '';
        }
    } else {
        console.log("⚠️ currentIdentity è null in updateIdentityDisplay");
    }
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0 || isNaN(bytes)) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

function formatSpeed(bytesPerSec) {
    if (!bytesPerSec || bytesPerSec < 0 || isNaN(bytesPerSec)) return '';
    const bitsPerSec = bytesPerSec * 8;
    if (bitsPerSec < 1000) return bitsPerSec.toFixed(1) + ' b/s';
    if (bitsPerSec < 1000000) return (bitsPerSec/1000).toFixed(1) + ' Kb/s';
    return (bitsPerSec/1000000).toFixed(2) + ' Mb/s';
}

function formatElapsed(seconds) {
    if (!seconds || seconds < 0 || isNaN(seconds)) return '';
    if (seconds < 60) return seconds.toFixed(1) + 's';
    if (seconds < 3600) {
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}m ${secs}s`;
    }
    return `${Math.floor(seconds/3600)}h ${Math.floor((seconds%3600)/60)}m`;
}

function getSignalClass(value, type) {
    if (value === undefined || value === null) return '';
    if (type === 'rssi') {
        if (value > -70) return 'good';
        if (value > -85) return 'fair';
        return 'poor';
    } else if (type === 'snr') {
        if (value > 15) return 'good';
        if (value > 8) return 'fair';
        return 'poor';
    }
    return '';
}

function cleanHash(hash) {
    if (!hash) return '';
    return hash.replace(/[:\s<>]/g, '').toLowerCase();
}

function cleanDisplayName(name) {
    if (!name) return 'Sconosciuto';
    let cleanName = String(name);
    cleanName = cleanName.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]/g, '');
    cleanName = cleanName.replace(/\s+/g, ' ').trim();
    if (!cleanName || cleanName.length === 0) return 'Sconosciuto';
    return cleanName;
}

function getTimeAgo(ts) {
    if (!ts) return 'sconosciuto';
    const seconds = Math.floor((Date.now() / 1000) - ts);
    if (seconds < 60) return 'ora';
    if (seconds < 3600) return `${Math.floor(seconds/60)}m fa`;
    if (seconds < 86400) return `${Math.floor(seconds/3600)}h fa`;
    return `${Math.floor(seconds/86400)}g fa`;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

// ============================================
// FUNZIONI MAPPA LEAFLET
// ============================================
function initMap() {
    const mapContainer = document.getElementById('mapContainer');
    const mapPlaceholder = document.getElementById('mapPlaceholder');
    
    if (!mapContainer) return;
    
    mapPlaceholder.style.display = 'none';
    mapContainer.style.display = 'block';
    
    if (!mapInstance) {
        mapInstance = L.map('mapContainer').setView([41.9028, 12.4964], 5);
        
        L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenTopoMap',
            maxZoom: 17
        }).addTo(mapInstance);
        
        mapMarker = L.marker([41.9028, 12.4964], {
            icon: L.divIcon({
                className: 'custom-marker',
                html: '<span class="mdi mdi-antenna" style="font-size:24px; color:#88ff88;"></span>',
                iconSize: [30, 30],
                popupAnchor: [0, -15]
            })
        }).addTo(mapInstance);
        
        mapMarker.setOpacity(0);
        debugLog('Mappa inizializzata con stile rilievi');
    }
}

function setMapStyle(style) {
    currentMapStyle = style;
    
    document.getElementById('mapStyleTopo').classList.toggle('active', style === 'topo');
    document.getElementById('mapStyleSatellite').classList.toggle('active', style === 'satellite');
    document.getElementById('mapStyleStreet').classList.toggle('active', style === 'street');
    
    if (!mapInstance) {
        initMap();
        return;
    }
    
    const center = mapInstance.getCenter();
    const zoom = mapInstance.getZoom();
    
    const layersToRemove = [];
    mapInstance.eachLayer(layer => {
        if (layer instanceof L.TileLayer) {
            layersToRemove.push(layer);
        }
    });
    
    layersToRemove.forEach(layer => mapInstance.removeLayer(layer));
    
    let tileLayer;
    switch(style) {
        case 'satellite':
            tileLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
                attribution: '&copy; Esri',
                maxZoom: 18
            });
            break;
        case 'street':
            tileLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
                attribution: '&copy; OpenStreetMap',
                maxZoom: 19
            });
            break;
        case 'topo':
        default:
            tileLayer = L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenTopoMap',
                maxZoom: 17
            });
            break;
    }
    
    tileLayer.addTo(mapInstance);
    mapInstance.setView(center, zoom);
    
    debugLog('Stile mappa cambiato:', style);
}

function updateMap(lat, lon) {
    const mapContainer = document.getElementById('mapContainer');
    const mapPlaceholder = document.getElementById('mapPlaceholder');
    
    if (!lat || !lon) {
        if (mapContainer) mapContainer.style.display = 'none';
        if (mapPlaceholder) {
            mapPlaceholder.style.display = 'flex';
            mapPlaceholder.innerHTML = '<span>🗺️ Posizione non disponibile</span>';
        }
        return;
    }
    
    mapContainer.style.display = 'block';
    mapPlaceholder.style.display = 'none';
    
    if (!mapInstance) {
        initMap();
    }
    
    if (mapInstance && mapMarker) {
        mapInstance.setView([lat, lon], 13);
        
        let iconName = 'antenna';
        let fgColor = '#88ff88';
        let bgColor = '#000000';
        
        if (currentPeer) {
            const peerHash = cleanHash(currentPeer.hash);
            const telemetry = getTelemetryFromDatabase(peerHash);
            
            if (telemetry?.appearance?.icon) {
                iconName = telemetry.appearance.icon;
            }
            
            if (telemetry?.appearance?.foreground) {
                const [r, g, b] = telemetry.appearance.foreground;
                fgColor = `rgb(${r}, ${g}, ${b})`;
            }
            
            if (telemetry?.appearance?.background) {
                const [r, g, b] = telemetry.appearance.background;
                bgColor = `rgb(${r}, ${g}, ${b})`;
            }
        }
        
        mapMarker.setLatLng([lat, lon]);
        mapMarker.setOpacity(1);
        
        const iconSize = 32;
        
        mapMarker.setIcon(L.divIcon({
            className: 'custom-marker',
            html: `
                <div style="
                    background-color: ${bgColor};
                    border-radius: 50%;
                    width: ${iconSize}px;
                    height: ${iconSize}px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.3);
                ">
                    <span class="mdi mdi-${iconName}" style="font-size:${iconSize-8}px; color:${fgColor};"></span>
                </div>
            `,
            iconSize: [iconSize, iconSize],
            popupAnchor: [0, -iconSize/2]
        }));
        
        if (mapMarker._popup) {
            mapMarker.unbindPopup();
        }
        
        debugLog('Mappa aggiornata', { 
            lat, lon, 
            icon: iconName, 
            fg: fgColor,
            bg: bgColor
        });
    }
}

// ============================================
// CONVERSIONE BATCH AUDIO
// ============================================
async function convertAllAudio() {
    if (!confirm('Convertire tutti i file audio Codec2 (.c2_*) in formati WAV/OGG?\nQuesta operazione potrebbe richiedere alcuni secondi.')) {
        return;
    }
    
    const progressDiv = document.getElementById('conversionProgress');
    progressDiv.innerHTML = '🔄 Scansione file in corso...';
    
    try {
        // Chiamata al backend per la conversione batch
        const response = await fetch('/api/audio/convert-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                delete_original: false,
                create_wav: true,
                create_ogg: true
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            let message = `✅ Conversione completata!\n`;
            message += `📁 File trovati: ${data.found}\n`;
            message += `✅ Convertiti: ${data.converted}`;
            if (data.failed > 0) message += `\n❌ Falliti: ${data.failed}`;
            
            progressDiv.innerHTML = message.replace(/\n/g, '<br>');
            
            // Mostra notifica
            showNotification(`✅ Convertiti ${data.converted} file audio`, 'success');
            
            // Ricarica la lista messaggi se necessario
            if (currentPeer) {
                lastMessagesJson = '';
                pollMessages();
            }
        } else {
            progressDiv.innerHTML = `❌ Errore: ${data.error}`;
            showNotification(`❌ Errore conversione: ${data.error}`, 'error');
        }
    } catch (e) {
        progressDiv.innerHTML = `❌ Errore: ${e.message}`;
        showNotification(`❌ Errore: ${e.message}`, 'error');
    }
}

async function cleanupConverted() {
    if (!confirm('Rimuovere i file originali .c2_* già convertiti?\nQuesta operazione libererà spazio ma non potrai più riconvertirli.')) {
        return;
    }
    
    const progressDiv = document.getElementById('conversionProgress');
    progressDiv.innerHTML = '🔄 Pulizia in corso...';
    
    try {
        const response = await fetch('/api/audio/cleanup', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        const data = await response.json();
        
        if (data.success) {
            progressDiv.innerHTML = `✅ Rimossi ${data.removed} file originali (liberati ${formatBytes(data.freed_space)})`;
            showNotification(`✅ Puliti ${data.removed} file`, 'success');
            
            if (currentPeer) {
                lastMessagesJson = '';
                pollMessages();
            }
        } else {
            progressDiv.innerHTML = `❌ Errore: ${data.error}`;
            showNotification(`❌ Errore: ${data.error}`, 'error');
        }
    } catch (e) {
        progressDiv.innerHTML = `❌ Errore: ${e.message}`;
        showNotification(`❌ Errore: ${e.message}`, 'error');
    }
}


// ============================================
// TELEMETRIA DASHBOARD
// ============================================
function saveTelemetryToDatabase(peerHash, telemetryData) {
    if (!peerHash || !telemetryData) return;
    
    if (!telemetryDatabase[peerHash]) {
        telemetryDatabase[peerHash] = {
            battery: null,
            location: null,
            appearance: null,
            information: null,
            pressure: null,
            temperature: null,
            humidity: null,
            lastUpdate: null,
            history: []
        };
    }
    
    if (telemetryData.battery) telemetryDatabase[peerHash].battery = telemetryData.battery;
    if (telemetryData.location) telemetryDatabase[peerHash].location = telemetryData.location;
    if (telemetryData.appearance) telemetryDatabase[peerHash].appearance = telemetryData.appearance;
    if (telemetryData.information) telemetryDatabase[peerHash].information = telemetryData.information;
    if (telemetryData.pressure) telemetryDatabase[peerHash].pressure = telemetryData.pressure;
    if (telemetryData.temperature) telemetryDatabase[peerHash].temperature = telemetryData.temperature;
    if (telemetryData.humidity) telemetryDatabase[peerHash].humidity = telemetryData.humidity;
    
    telemetryDatabase[peerHash].lastUpdate = telemetryData.timestamp || Math.floor(Date.now() / 1000);
    
    if (!telemetryDatabase[peerHash].history) {
        telemetryDatabase[peerHash].history = [];
    }
    
    telemetryDatabase[peerHash].history.unshift({
        timestamp: telemetryData.timestamp || Math.floor(Date.now() / 1000),
        battery: telemetryData.battery?.charge_percent,
        location: telemetryData.location
    });
    
    if (telemetryDatabase[peerHash].history.length > 100) {
        telemetryDatabase[peerHash].history.pop();
    }
    
    debugLog('Telemetria salvata per peer', { peer: peerHash.substring(0,8), data: telemetryData });
}

function getTelemetryFromDatabase(peerHash) {
    if (!peerHash || !telemetryDatabase[peerHash]) {
        return {
            battery: null,
            location: null,
            appearance: null,
            information: null,
            pressure: null,
            temperature: null,
            humidity: null,
            lastUpdate: null
        };
    }
    
    return telemetryDatabase[peerHash];
}

function renderTelemetryDashboard() {
    const dashboard = document.getElementById('telemetryDashboard');
    if (!dashboard) return;
    if (!currentPeer) {
        dashboard.innerHTML = '<div class="telemetry-empty">👈 Seleziona un peer</div>';
        return;
    }
    
    const peerHash = cleanHash(currentPeer.hash);
    const telemetry = getTelemetryFromDatabase(peerHash);
    
    if (!telemetry.battery && !telemetry.location && !telemetry.appearance && !telemetry.information) {
        dashboard.innerHTML = '<div class="telemetry-empty">📡 Nessun dato telemetria</div>';
        return;
    }
    
    let html = '<div class="telemetry-dashboard">';
    
    const iconName = telemetry.appearance?.icon || 'account';
    html += `<div class="telemetry-peer-mini">`;
    html += `<div class="peer-mini-icon"><span class="mdi mdi-${iconName}"></span></div>`;
    html += `<div class="peer-mini-name">${escapeHtml(cleanDisplayName(currentPeer.display_name))}</div>`;
    html += `<div class="peer-mini-hash">${peerHash.substring(0, 8)}</div>`;
    html += `</div>`;
    
    if (telemetry.battery) {
        const battery = telemetry.battery;
        const chargePercent = battery.charge_percent || battery.charge || 0;
        const isCharging = battery.charging || false;
        
        html += `<div class="telemetry-grid">`;
        html += `<div class="telemetry-cell battery-cell">`;
        html += `<div class="telemetry-cell-title">🔋 BATTERIA</div>`;
        html += `<div class="battery-compact">`;
        html += `<span class="battery-icon">${isCharging ? '⚡' : '🔋'}</span>`;
        html += `<div class="battery-level-compact"><div class="battery-fill-compact" style="width:${chargePercent}%"></div></div>`;
        html += `<span class="battery-percent">${chargePercent}%</span>`;
        if (battery.temperature) html += `<span style="color:#ffaa00; margin-left:4px;">${battery.temperature}°C</span>`;
        html += `</div></div></div>`;
    }
    
    if (telemetry.location) {
        const loc = telemetry.location;
        html += `<div class="position-movement-row">`;
        
        html += `<div class="position-movement-box">`;
        html += `<div class="position-movement-label">LAT</div>`;
        html += `<div class="position-movement-value">${loc.latitude ? loc.latitude.toFixed(6) : '?'}</div>`;
        html += `</div>`;
        
        html += `<div class="position-movement-box">`;
        html += `<div class="position-movement-label">LON</div>`;
        html += `<div class="position-movement-value">${loc.longitude ? loc.longitude.toFixed(6) : '?'}</div>`;
        html += `</div>`;
        
        html += `<div class="position-movement-box">`;
        html += `<div class="position-movement-label">ALT</div>`;
        html += `<div class="position-movement-value">${loc.altitude ? loc.altitude.toFixed(1) + 'm' : '?'}</div>`;
        html += `</div>`;
        
        html += `<div class="position-movement-box velocity">`;
        html += `<div class="position-movement-label">VEL</div>`;
        html += `<div class="position-movement-value">${(loc.speed !== undefined && !isNaN(loc.speed)) ? (loc.speed * 3.6).toFixed(1) + 'km/h' : '?'}</div>`;
        html += `</div>`;
        
        html += `<div class="position-movement-box direction">`;
        html += `<div class="position-movement-label">DIR</div>`;
        html += `<div class="position-movement-value">${(loc.bearing !== undefined && !isNaN(loc.bearing)) ? loc.bearing.toFixed(0) + '°' : '?'}</div>`;
        html += `</div>`;
        
        html += `</div>`;
        
        if (loc.latitude && loc.longitude) {
            updateMap(loc.latitude, loc.longitude);
        }
    }
    
    if (telemetry.temperature || telemetry.humidity || telemetry.pressure || 
        telemetry.information?.cpu || telemetry.information?.ram || telemetry.information?.disk) {
        
        html += `<div class="sensor-row">`;
        
        if (telemetry.temperature || telemetry.battery?.temperature) {
            const temp = telemetry.temperature || telemetry.battery?.temperature;
            html += `<div class="sensor-item">`;
            html += `<span class="sensor-icon">🌡️</span>`;
            html += `<span class="sensor-label">TEMP</span>`;
            html += `<span class="sensor-value">${temp}°C</span>`;
            html += `</div>`;
        } else {
            html += `<div class="sensor-item"><span class="sensor-label">-</span></div>`;
        }
        
        if (telemetry.humidity) {
            html += `<div class="sensor-item">`;
            html += `<span class="sensor-icon">💧</span>`;
            html += `<span class="sensor-label">HUM</span>`;
            html += `<span class="sensor-value">${telemetry.humidity}%</span>`;
            html += `</div>`;
        } else {
            html += `<div class="sensor-item"><span class="sensor-label">-</span></div>`;
        }
        
        if (telemetry.pressure) {
            html += `<div class="sensor-item">`;
            html += `<span class="sensor-icon">🔄</span>`;
            html += `<span class="sensor-label">PRESS</span>`;
            html += `<span class="sensor-value">${telemetry.pressure}hPa</span>`;
            html += `</div>`;
        } else {
            html += `<div class="sensor-item"><span class="sensor-label">-</span></div>`;
        }
        
        if (telemetry.information?.cpu) {
            html += `<div class="sensor-item">`;
            html += `<span class="sensor-icon">⚙️</span>`;
            html += `<span class="sensor-label">CPU</span>`;
            html += `<span class="sensor-value">${telemetry.information.cpu}</span>`;
            html += `</div>`;
        } else {
            html += `<div class="sensor-item"><span class="sensor-label">-</span></div>`;
        }
        
        if (telemetry.information?.ram) {
            html += `<div class="sensor-item">`;
            html += `<span class="sensor-icon">📊</span>`;
            html += `<span class="sensor-label">RAM</span>`;
            html += `<span class="sensor-value">${telemetry.information.ram}</span>`;
            html += `</div>`;
        } else {
            html += `<div class="sensor-item"><span class="sensor-label">-</span></div>`;
        }
        
        html += `</div>`;
        
        html += `<div class="sensor-row">`;
        
        if (telemetry.information?.disk) {
            html += `<div class="sensor-item">`;
            html += `<span class="sensor-icon">💾</span>`;
            html += `<span class="sensor-label">DISK</span>`;
            html += `<span class="sensor-value">${telemetry.information.disk}</span>`;
            html += `</div>`;
        } else {
            html += `<div class="sensor-item"><span class="sensor-label">-</span></div>`;
        }
        
        if (telemetry.information?.uptime) {
            html += `<div class="sensor-item" style="grid-column: span 4;">`;
            html += `<span class="sensor-icon">⏱️</span>`;
            html += `<span class="sensor-label">UPTIME</span>`;
            html += `<span class="sensor-value">${telemetry.information.uptime}</span>`;
            html += `</div>`;
        } else {
            html += `<div class="sensor-item" style="grid-column: span 4;"><span class="sensor-label">-</span></div>`;
        }
        
        html += `</div>`;
    }
    
    if (telemetry.lastUpdate) {
        const date = new Date(telemetry.lastUpdate * 1000);
        html += `<div class="timestamp-row">`;
        html += `<div class="timestamp-cell">`;
        html += `<span>🕒</span>`;
        html += `<span>${date.toLocaleDateString()} ${date.toLocaleTimeString()}</span>`;
        html += `</div>`;
        html += `</div>`;
    }
    
    html += `</div>`;
    dashboard.innerHTML = html;
}

// ============================================
// FUNZIONI MONITOR
// ============================================
function toggleRawView() {
    rawViewActive = !rawViewActive;
    const btn = document.getElementById('rawViewBtn');
    
    if (rawViewActive) {
        btn.classList.add('active');
        btn.innerHTML = '🔬 RAW ON';
    } else {
        btn.classList.remove('active');
        btn.innerHTML = '🔬 RAW';
    }
    renderMessages();
    debugLog('RAW view', rawViewActive);
}

function toggleMonitor() {
    monitorActive = !monitorActive;
    const monitor = document.getElementById('rawMonitor');
    const btn = document.getElementById('monitorBtn');
    const container = document.getElementById('chatContainer');
    const messageColumn = document.querySelector('.lxmf-message-column');
    
    if (monitorActive) {
        monitor.style.display = 'flex';
        container.classList.add('monitor-active');
        btn.classList.add('active');
        btn.innerHTML = '📊 MONITOR ON';
        messageColumn.style.flex = '1';
        renderMonitorPackets();
        renderTelemetryDashboard();
    } else {
        monitor.style.display = 'none';
        container.classList.remove('monitor-active');
        btn.classList.remove('active');
        btn.innerHTML = '📊 MONITOR';
        messageColumn.style.flex = '3';
    }
    debugLog('Monitor', monitorActive);
}

function switchRawTab(tab) {
    document.getElementById('tabPackets').classList.toggle('active', tab === 'packets');
    document.getElementById('tabHistory').classList.toggle('active', tab === 'history');
    document.getElementById('tabConfig').classList.toggle('active', tab === 'config');
    
    document.getElementById('rawContent').style.display = tab === 'packets' ? 'block' : 'none';
    document.getElementById('historyContent').style.display = tab === 'history' ? 'block' : 'none';
    document.getElementById('configContent').style.display = tab === 'config' ? 'block' : 'none';
    
    if (tab === 'history') {
        loadTelemetryHistory();
    } else if (tab === 'config') {
        loadConfig();
    }
}

function addMonitorPacket(packet) {
    if (!packet.peer) return;
    
    const packetPeer = cleanHash(packet.peer);
    const currentPeerHash = currentPeer ? cleanHash(currentPeer.hash) : null;
    
    if (packet.direction !== 'incoming') {
        return;
    }
    
    if (!incomingPackets[packetPeer]) {
        incomingPackets[packetPeer] = [];
    }
    
    const recentPackets = incomingPackets[packetPeer].slice(0, 5);
    const isDuplicate = recentPackets.some(p => 
        p.timestamp === packet.timestamp && 
        p.content === packet.content &&
        p.raw_hex === packet.raw_hex
    );
    
    if (isDuplicate) {
        return;
    }
    
    incomingPackets[packetPeer].unshift(packet);
    
    if (incomingPackets[packetPeer].length > 50) {
        incomingPackets[packetPeer].pop();
    }
    
    const telemetryData = {
        timestamp: packet.timestamp,
        battery: packet.battery,
        location: packet.location,
        appearance: packet.appearance,
        information: packet.information,
        pressure: packet.pressure,
        temperature: packet.temperature,
        humidity: packet.humidity
    };
    
    if (packet.telemetry) {
        if (packet.telemetry.battery) telemetryData.battery = packet.telemetry.battery;
        if (packet.telemetry.location) telemetryData.location = packet.telemetry.location;
        if (packet.telemetry.appearance) telemetryData.appearance = packet.telemetry.appearance;
        if (packet.telemetry.information) telemetryData.information = packet.telemetry.information;
    }
    
    saveTelemetryToDatabase(packetPeer, telemetryData);
    
    if (currentPeerHash && packetPeer === currentPeerHash) {
        if (monitorActive) {
            renderTelemetryDashboard();
            renderMonitorPackets();
        }
    }
    
    if (currentPeerHash && packetPeer !== currentPeerHash) {
        unreadPeers.add(packetPeer);
        renderPeerList();
    }
}

function clearMonitorLog() {
    if (!currentPeer) return;
    const peerHash = cleanHash(currentPeer.hash);
    incomingPackets[peerHash] = [];
    
    if (monitorActive) {
        renderMonitorPackets();
    }
    debugLog('Monitor log pulito');
}

function renderMonitorPackets() {
    const list = document.getElementById('rawContent');
    if (!list || !currentPeer) return;
    
    const currentPeerHash = cleanHash(currentPeer.hash);
    const packets = incomingPackets[currentPeerHash] || [];
    
    if (packets.length === 0) {
        list.innerHTML = '<div style="color: #888; text-align: center; margin-top: 20px;">Nessun pacchetto ricevuto</div>';
        return;
    }
    
    let html = '';
    packets.slice(0, 30).forEach(pkt => {
        const time = new Date(pkt.timestamp * 1000).toLocaleTimeString();
        const date = new Date(pkt.timestamp * 1000).toLocaleDateString();
        
        html += `<div style="border-bottom:1px solid #333; padding:8px; font-size:10px;">`;
        html += `<div style="color:#888; margin-bottom:4px;">${date} ${time} 📥</div>`;
        
        if (pkt.content) {
            const short = pkt.content.length > 50 ? pkt.content.substring(0,50) + '...' : pkt.content;
            html += `<div style="color:#88ff88; margin-bottom:4px;">${escapeHtml(short)}</div>`;
        }
        
        if (pkt.raw_hex) {
            const hexId = `hex-${pkt.timestamp}-${Math.random()}`;
            const hexPreview = pkt.raw_hex.substring(0, 128);
            
            html += `<div style="margin:4px 0;">`;
            html += `<div style="display:flex; align-items:center; gap:4px;">`;
            html += `<span style="color:#88f; font-size:9px;">🔹 HEX (${pkt.raw_size || '?'} bytes):</span>`;
            html += `<button onclick="toggleHex('${hexId}')" style="background:#1a1a1a; color:#88f; border:1px solid #88f; font-size:8px; padding:2px 5px; cursor:pointer;">mostra/nascondi</button>`;
            html += `</div>`;
            html += `<div id="${hexId}" style="display:none; color:#88f; font-size:8px; word-break:break-all; background:#1a1a1a; padding:4px; margin-top:2px; max-height:200px; overflow-y:auto;">${escapeHtml(pkt.raw_hex)}</div>`;
            html += `<div style="color:#88f; font-size:8px; word-break:break-all;">${escapeHtml(hexPreview)}${pkt.raw_hex.length > 128 ? '...' : ''}</div>`;
            html += `</div>`;
        }
        
        if (pkt.raw_ascii) {
            const asciiId = `ascii-${pkt.timestamp}-${Math.random()}`;
            const asciiPreview = pkt.raw_ascii.substring(0, 128);
            
            html += `<div style="margin:4px 0;">`;
            html += `<div style="display:flex; align-items:center; gap:4px;">`;
            html += `<span style="color:#ff8; font-size:9px;">🔸 ASCII:</span>`;
            html += `<button onclick="toggleAscii('${asciiId}')" style="background:#1a1a1a; color:#ff8; border:1px solid #ff8; font-size:8px; padding:2px 5px; cursor:pointer;">mostra/nascondi</button>`;
            html += `</div>`;
            html += `<div id="${asciiId}" style="display:none; color:#ff8; font-size:8px; word-break:break-all; background:#1a1a1a; padding:4px; margin-top:2px; max-height:200px; overflow-y:auto;">${escapeHtml(pkt.raw_ascii)}</div>`;
            html += `<div style="color:#ff8; font-size:8px; word-break:break-all;">${escapeHtml(asciiPreview)}${pkt.raw_ascii.length > 128 ? '...' : ''}</div>`;
            html += `</div>`;
        }
        
        if (pkt.rssi || pkt.snr) {
            html += `<div style="display:flex; gap:8px; margin-top:4px;">`;
            if (pkt.rssi) html += `<span>📶 ${pkt.rssi.toFixed(1)}</span>`;
            if (pkt.snr) html += `<span>📊 ${pkt.snr.toFixed(1)}</span>`;
            html += `</div>`;
        }
        
        if (pkt.battery || pkt.location) {
            html += `<div style="display:flex; gap:4px; margin-top:2px;">`;
            if (pkt.battery) html += `<span style="color:#7ec8e0;">🔋 ${pkt.battery.charge_percent || pkt.battery.charge || '?'}%</span>`;
            if (pkt.location) html += `<span style="color:#7ec8e0;">📍 pos</span>`;
            html += `</div>`;
        }
        
        html += `</div>`;
    });
    
    list.innerHTML = html;
}

function toggleHex(id) {
    const el = document.getElementById(id);
    if (el) {
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }
}

function toggleAscii(id) {
    const el = document.getElementById(id);
    if (el) {
        el.style.display = el.style.display === 'none' ? 'block' : 'none';
    }
}

// ============================================
// STORICO TELEMETRIA
// ============================================
let historyMap = null;
let historyMarkers = [];

function initHistoryMap() {
    const container = document.getElementById('historyMapContainer');
    if (!container) return;
    
    if (historyMap) {
        historyMap.remove();
        historyMarkers = [];
    }
    
    historyMap = L.map('historyMapContainer').setView([41.9028, 12.4964], 4);
    
    L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
        attribution: '&copy; OpenTopoMap',
        maxZoom: 17
    }).addTo(historyMap);
}

function getPointColor(entry) {
    const signal = entry.signal || {};
    
    if (signal.snr !== undefined && signal.snr !== null) {
        if (signal.snr > 15) return '#00ff00';
        if (signal.snr > 8) return '#ffff00';
        if (signal.snr > 3) return '#ff8800';
        return '#ff0000';
    }
    
    if (signal.rssi !== undefined && signal.rssi !== null) {
        if (signal.rssi > -70) return '#00ff00';
        if (signal.rssi > -85) return '#ffff00';
        if (signal.rssi > -100) return '#ff8800';
        return '#ff0000';
    }
    
    if (signal.quality !== undefined && signal.quality !== null) {
        const q = Number(signal.quality);
        if (q > 0.8) return '#00ff00';
        if (q > 0.5) return '#ffff00';
        if (q > 0.2) return '#ff8800';
        return '#ff0000';
    }
    
    if (signal.hops !== undefined && signal.hops !== null) {
        if (signal.hops <= 1) return '#00ff00';
        if (signal.hops <= 3) return '#ffff00';
        if (signal.hops <= 5) return '#ff8800';
        return '#ff0000';
    }
    
    return '#888888';
}

function addHistoryMarker(entry, index) {
    if (!entry.location || !historyMap) return;
    
    const lat = entry.location.latitude;
    const lon = entry.location.longitude;
    const color = getPointColor(entry);
    
    const date = new Date(entry.timestamp * 1000);
    const dateStr = date.toLocaleDateString();
    const timeStr = date.toLocaleTimeString();
    
    let popupHtml = `<b>${dateStr} ${timeStr}</b><br>`;
    
    if (entry.signal) {
        if (entry.signal.snr) popupHtml += `SNR: ${entry.signal.snr.toFixed(1)}<br>`;
        if (entry.signal.rssi) popupHtml += `RSSI: ${entry.signal.rssi.toFixed(1)}<br>`;
        if (entry.signal.quality) popupHtml += `Quality: ${entry.signal.quality.toFixed(2)}<br>`;
        if (entry.signal.hops !== undefined) popupHtml += `Hops: ${entry.signal.hops}<br>`;
    }
    
    if (entry.location.altitude) popupHtml += `Alt: ${entry.location.altitude.toFixed(1)}m<br>`;
    if (entry.location.speed) popupHtml += `Vel: ${(entry.location.speed * 3.6).toFixed(1)}km/h<br>`;
    if (entry.location.bearing) popupHtml += `Dir: ${entry.location.bearing.toFixed(0)}°<br>`;
    
    if (entry.battery) {
        popupHtml += `🔋 ${entry.battery.percent}%`;
        if (entry.battery.charging) popupHtml += ` (⚡)`;
        popupHtml += `<br>`;
    }
    
    if (entry.environment) {
        if (entry.environment.temperature) popupHtml += `🌡️ ${entry.environment.temperature}°C<br>`;
        if (entry.environment.humidity) popupHtml += `💧 ${entry.environment.humidity}%<br>`;
    }
    
    const marker = L.circleMarker([lat, lon], {
        radius: 8,
        color: color,
        fillColor: color,
        fillOpacity: 0.7,
        weight: 2,
        opacity: 0.9
    }).bindPopup(popupHtml);
    
    if (index > 0 && historyMarkers.length > 0) {
        const prevMarker = historyMarkers[index - 1];
        if (prevMarker && prevMarker.getLatLng) {
            const prevLatLng = prevMarker.getLatLng();
            
            const line = L.polyline([
                [prevLatLng.lat, prevLatLng.lng],
                [lat, lon]
            ], {
                color: '#888888',
                weight: 1,
                opacity: 0.3,
                dashArray: '5, 5'
            }).addTo(historyMap);
            
            historyMarkers.push(line);
        }
    }
    
    marker.addTo(historyMap);
    historyMarkers.push(marker);
    
    return marker;
}

function renderTimeline(history) {
    const timelineEl = document.getElementById('historyTimeline');
    if (!timelineEl) return;
    
    let html = '';
    
    history.forEach((entry, index) => {
        const date = new Date(entry.timestamp * 1000);
        const dateStr = date.toLocaleDateString();
        const timeStr = date.toLocaleTimeString();
        const color = getPointColor(entry);
        
        let entryHtml = `
            <div class="timeline-entry" onclick="focusHistoryMarker(${index})" style="cursor:pointer; padding:5px; border-bottom:1px solid #333; display:flex; align-items:center;">
                <div style="width:12px; height:12px; border-radius:50%; background-color:${color}; margin-right:8px;"></div>
                <div style="flex:1;">
                    <div style="color:#88ff88; font-size:10px;">${dateStr} ${timeStr}</div>
        `;
        
        if (entry.signal) {
            let signalParts = [];
            if (entry.signal.snr) signalParts.push(`SNR:${entry.signal.snr.toFixed(1)}`);
            if (entry.signal.rssi) signalParts.push(`RSSI:${entry.signal.rssi.toFixed(1)}`);
            if (entry.signal.hops !== undefined) signalParts.push(`H:${entry.signal.hops}`);
            if (signalParts.length > 0) {
                entryHtml += `<div style="color:#888; font-size:9px;">${signalParts.join(' ')}</div>`;
            }
        }
        
        if (entry.location) {
            entryHtml += `<div style="color:#aaa; font-size:8px;">${entry.location.latitude.toFixed(4)}, ${entry.location.longitude.toFixed(4)}</div>`;
        }
        
        entryHtml += `</div></div>`;
        html += entryHtml;
    });
    
    timelineEl.innerHTML = html || '<div style="color:#888; text-align:center; padding:10px;">Nessun dato storico</div>';
}

function focusHistoryMarker(index) {
    if (!historyMap || !historyMarkers[index]) return;
    
    const marker = historyMarkers[index];
    const latLng = marker.getLatLng ? marker.getLatLng() : marker;
    
    historyMap.setView(latLng, 13);
    
    if (marker.openPopup) {
        marker.openPopup();
    }
}

async function loadTelemetryHistory() {
    if (!currentPeer) return;
    
    const peerHash = cleanHash(currentPeer.hash);
    const range = document.getElementById('historyTimeRange')?.value || '24h';
    
    try {
        const response = await fetch(`/api/telemetry/history/${peerHash}?range=${range}`);
        const history = await response.json();
        
        if (!history || history.length === 0) {
            document.getElementById('historyMapContainer').innerHTML = '<div style="display:flex; align-items:center; justify-content:center; height:100%; color:#888;">📊 Nessun dato storico disponibile</div>';
            document.getElementById('historyTimeline').innerHTML = '<div style="color:#888; text-align:center; padding:10px;">Nessun dato</div>';
            return;
        }
        
        if (!historyMap) {
            initHistoryMap();
        } else {
            historyMarkers.forEach(m => historyMap.removeLayer(m));
            historyMarkers = [];
        }
        
        history.forEach((entry, index) => {
            if (entry.location) {
                addHistoryMarker(entry, index);
            }
        });
        
        if (history[0].location && historyMap) {
            historyMap.setView([
                history[0].location.latitude,
                history[0].location.longitude
            ], 10);
        }
        
        renderTimeline(history);
        
    } catch (e) {
        console.error('Errore caricamento storico:', e);
    }
}

// ============================================
// REGISTRAZIONE VOCALE (USA CONFIG)
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
    
    // 🔥 USA LA CONFIGURAZIONE INVECE DEL POPUP
    let audioCodec = currentConfig.preferred_audio || 'opus';
    let audioMode = 16;
    let bitrate = 24000;
    let mimeType = 'audio/ogg;codecs=opus';
    
    // Mappa codec preference a mode
    const codecMap = {
        'opus': { mode: 16, bitrate: 24000 },
        'c2_3200': { mode: 9, bitrate: 3200 },
        'c2_2400': { mode: 8, bitrate: 2400 },
        'c2_1200': { mode: 4, bitrate: 1200 },
        'c2_700': { mode: 3, bitrate: 700 }
    };
    
    if (codecMap[audioCodec]) {
        audioMode = codecMap[audioCodec].mode;
        bitrate = codecMap[audioCodec].bitrate;
        // Registriamo sempre in Opus, la conversione lato server deciderà
        mimeType = 'audio/ogg;codecs=opus';
    }
    
    debugLog('Avvio registrazione', { codec: audioCodec, mode: audioMode, bitrate });
    
    try {
        audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        window.MediaRecorder = window.OpusMediaRecorder;
        
        const workerOptions = {
            encoderWorkerFactory: function () {
                return new Worker('/static/opus-media-recorder/encoderWorker.umd.js');
            },
            OggOpusEncoderWasmPath: '/static/opus-media-recorder/OggOpusEncoder.wasm',
            WebMOpusEncoderWasmPath: '/static/opus-media-recorder/WebMOpusEncoder.wasm'
        };
        
        const options = { 
            mimeType: mimeType,
            audioBitsPerSecond: bitrate
        };
        
        mediaRecorder = new MediaRecorder(audioStream, options, workerOptions);
        
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
            
            const audioBlob = new Blob(audioChunks, { type: 'audio/ogg' });
            
            console.log(`🎵 Audio registrato: ${formatBytes(audioBlob.size)}`);
            
            document.getElementById('voiceRecordingIndicator').style.display = 'block';
            document.getElementById('voiceRecordingIndicator').innerHTML = '<span>🔄 PREPARAZIONE...</span>';
            
            // 🔥 IL FILE VIENE SEMPRE SALVATO COME .opus TEMPORANEO
            // Sarà convertito lato server in base alla configurazione
            const fileName = `voice_${Date.now()}.opus`;
            const file = new File([audioBlob], fileName, { type: 'audio/ogg' });
            
            file.audioCodec = audioCodec;
            file.audioMode = audioMode;
            
            selectedFiles = [file];
            
            document.getElementById('filePreview').innerHTML = `
                <div class="lxmf-file-tag">
                    <span>${audioCodec.includes('opus') ? '🎵' : '🎤'} ${fileName} (${formatBytes(audioBlob.size)})</span>
                    <span class="remove" onclick="removeFile(0)">✕</span>
                </div>
            `;
            
            // Invia automaticamente se configurato
            if (confirm("Inviare messaggio vocale ora?")) {
                await sendVoiceMessage(file, audioCodec);
            }
            
            resetVoiceUI(voiceBtn);
        };
        
        mediaRecorder.onerror = (event) => {
            console.error("Errore MediaRecorder:", event.error);
            alert("Errore registrazione: " + event.error);
            stopVoiceRecording();
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

async function sendVoiceMessage(file, codec) {
    if (!currentPeer) return;
    
    const formData = new FormData();
    formData.append('destination', currentPeer.hash);
    formData.append('file', file);
    formData.append('audio_codec', codec);
    formData.append('audio_mode', file.audioMode || 16);
    formData.append('description', `🎤 Messaggio vocale`);
    
    document.getElementById('sendButton').disabled = true;
    document.getElementById('voiceBtn').disabled = true;
    
    try {
        const response = await fetch('/api/chat/send-file', {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        
        if (data.error) {
            alert(`❌ Errore: ${data.error}`);
        } else if (data.success) {
            selectedFiles = [];
            document.getElementById('filePreview').innerHTML = '';
            document.getElementById('fileInput').value = '';
            lastMessagesJson = '';
            setTimeout(() => pollMessages(), 500);
            debugLog('Messaggio vocale inviato', { codec: codec, mode: file.audioMode });
        }
    } catch (e) {
        alert(`❌ Errore: ${e.message}`);
    } finally {
        document.getElementById('sendButton').disabled = false;
        document.getElementById('voiceBtn').disabled = false;
    }
}

// ============================================
// FUNZIONI FILE
// ============================================
function handleFileSelect() {
    const fileInput = document.getElementById('fileInput');
    if (!fileInput.files) return;
    
    selectedFiles = Array.from(fileInput.files);
    
    let previewHtml = '';
    selectedFiles.forEach((file, index) => {
        let icon = '📎';
        if (file.type.startsWith('image/')) icon = '🖼️';
        else if (file.type.startsWith('audio/')) icon = '🎵';
        else if (file.type.startsWith('video/')) icon = '🎬';
        
        let codecSelector = '';
        if (file.name.endsWith('.wav')) {
            codecSelector = `
                <select onchange="setAudioCodec(${index}, this.value)" style="background: #1a1a1a; color: #ffaa00; border: 1px solid #ffaa00; margin-left: 5px; font-size: 9px;">
                    <option value="opus">Opus</option>
                    <option value="c2_1200">Codec2 1200</option>
                    <option value="c2_2400">Codec2 2400</option>
                </select>
            `;
        }
        
        previewHtml += `
            <div class="lxmf-file-tag">
                <span>${icon} ${escapeHtml(file.name)} (${formatBytes(file.size)})</span>
                ${codecSelector}
                <span class="remove" onclick="removeFile(${index})">✕</span>
            </div>
        `;
    });
    
    document.getElementById('filePreview').innerHTML = previewHtml;
    debugLog('File selezionati', selectedFiles.length);
}

function setAudioCodec(index, codec) {
    if (selectedFiles[index]) {
        selectedFiles[index].audioCodec = codec;
        debugLog(`Audio codec set for file ${index}: ${codec}`);
    }
}

function removeFile(index) {
    selectedFiles.splice(index, 1);
    
    if (selectedFiles.length === 0) {
        document.getElementById('filePreview').innerHTML = '';
        document.getElementById('fileInput').value = '';
        return;
    }
    
    let previewHtml = '';
    selectedFiles.forEach((file, i) => {
        let icon = '📎';
        if (file.type.startsWith('image/')) icon = '🖼️';
        else if (file.type.startsWith('audio/')) icon = '🎵';
        else if (file.type.startsWith('video/')) icon = '🎬';
        
        previewHtml += `
            <div class="lxmf-file-tag">
                <span>${icon} ${escapeHtml(file.name)} (${formatBytes(file.size)})</span>
                <span class="remove" onclick="removeFile(${i})">✕</span>
            </div>
        `;
    });
    
    document.getElementById('filePreview').innerHTML = previewHtml;
}

// ============================================
// FUNZIONI RAW INDIVIDUALI
// ============================================
async function showMessageRaw(peerHash, filename, timestamp) {
    try {
        debugLog('Caricamento raw per', { peer: peerHash.substring(0,8), file: filename });
        
        const response = await fetch(`/api/chat/raw/${peerHash}/${filename}`);
        const data = await response.json();
        
        if (data.error) {
            alert(`❌ Errore: ${data.error}`);
            return;
        }
        
        const modalHtml = `
            <div id="rawModal-${timestamp}" class="raw-modal" onclick="closeRawModal(${timestamp})">
                <div class="raw-modal-content" onclick="event.stopPropagation()">
                    <div class="raw-modal-header">
                        <span>📦 RAW COMPLETO - ${new Date(timestamp*1000).toLocaleString()}</span>
                        <button onclick="closeRawModal(${timestamp})">✕</button>
                    </div>
                    <div class="raw-modal-body">
                        <div class="raw-section">
                            <div class="raw-label">📏 SIZE: ${data.raw_size} bytes</div>
                        </div>
                        <div class="raw-section">
                            <div class="raw-label">🔹 HEX (primi 2048 bytes):</div>
                            <div class="raw-hex">${escapeHtml(data.raw_hex.substring(0, 2048))}${data.raw_hex.length > 2048 ? '...' : ''}</div>
                            <button class="raw-copy-btn" onclick="copyToClipboard('${data.raw_hex}')">📋 COPIA TUTTO HEX</button>
                        </div>
                        <div class="raw-section">
                            <div class="raw-label">🔸 ASCII:</div>
                            <div class="raw-ascii">${escapeHtml(data.raw_ascii.substring(0, 2048))}${data.raw_ascii.length > 2048 ? '...' : ''}</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        const oldModal = document.getElementById(`rawModal-${timestamp}`);
        if (oldModal) oldModal.remove();
        
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        
    } catch (e) {
        console.error('Errore caricamento raw:', e);
        alert(`❌ Errore: ${e.message}`);
    }
}

function closeRawModal(timestamp) {
    const modal = document.getElementById(`rawModal-${timestamp}`);
    if (modal) modal.remove();
}

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        alert('✅ Copiato!');
    }).catch(() => {
        alert('❌ Errore copia');
    });
}

// ============================================
// RENDERING MESSAGGI (COMPLETO)
// ============================================
function renderMessages() {
    if (!currentPeer) {
        document.getElementById('messageList').innerHTML = '<div class="lxmf-no-peer">👈 Seleziona un peer</div>';
        return;
    }
    
    const peerHash = cleanHash(currentPeer.hash);
    const peerMessages = messages.filter(msg => {
        const msgFrom = cleanHash(msg.from || '');
        const msgTo = cleanHash(msg.to || '');
        return msgFrom === peerHash || msgTo === peerHash;
    });
    
    debugLog('Render messages', { total: messages.length, filtered: peerMessages.length, peer: peerHash.substring(0,8) });
    
    if (peerMessages.length === 0) {
        document.getElementById('messageList').innerHTML = '<div class="lxmf-no-messages">💬 Nessun messaggio per questo peer</div>';
        return;
    }
    
    const sortedMessages = [...peerMessages].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
    
    let html = '';
    sortedMessages.forEach(msg => {
        const time = new Date(msg.timestamp * 1000).toLocaleTimeString();
        const direction = msg.direction === 'incoming' ? 'incoming' : 'outgoing';
        
        let sender = '';
        if (msg.direction === 'incoming') {
            const peer = peers.find(p => cleanHash(p.hash) === cleanHash(msg.from));
            sender = peer ? cleanDisplayName(peer.display_name) : (msg.from ? msg.from.replace(/[<>]/g, '').substring(0,16) + '...' : 'Sconosciuto');
        } else {
            sender = currentIdentity ? cleanDisplayName(currentIdentity.name) : 'Tu';
        }
        
        let statusIcon = '';
        let retryButton = '';
        let rawButton = '';
        
        if (msg.direction === 'outgoing') {
            if (msg.status === 'delivered') {
                statusIcon = '<span class="lxmf-message-status delivered" title="Consegnato">✓✓</span>';
            } else if (msg.status === 'failed') {
                statusIcon = '<span class="lxmf-message-status failed" title="Fallito">✗</span>';
                retryButton = `<button class="retry-button" onclick="retryMessage(${msg.timestamp})" title="Riprova invio">↻</button>`;
            } else if (msg.status === 'sending') {
                statusIcon = '<span class="lxmf-message-status sending" title="Invio in corso">⋯</span>';
            }
        }
        
        if (msg.lxmf_file) {
            rawButton = `<button class="msg-raw-btn" onclick="showMessageRaw('${peerHash}', '${msg.lxmf_file}', ${msg.timestamp})" title="Mostra RAW completo">🔬</button>`;
        }
        
        let radioInfo = '';
        let radioParts = [];
        if (msg.rssi !== undefined && msg.rssi !== null && !isNaN(msg.rssi)) {
            const rssiClass = getSignalClass(msg.rssi, 'rssi');
            radioParts.push(`<span class="${rssiClass}">📶 RSSI: ${Number(msg.rssi).toFixed(1)}</span>`);
        }
        if (msg.snr !== undefined && msg.snr !== null && !isNaN(msg.snr)) {
            const snrClass = getSignalClass(msg.snr, 'snr');
            radioParts.push(`<span class="${snrClass}">📊 SNR: ${Number(msg.snr).toFixed(1)}</span>`);
        }
        if (msg.q !== undefined && msg.q !== null && !isNaN(msg.q)) {
            radioParts.push(`⚡ Q: ${Number(msg.q).toFixed(1)}`);
        }
        if (radioParts.length > 0) {
            radioInfo = `<div class="lxmf-radio-info">${radioParts.join(' ')}</div>`;
        }
        
        let transferInfo = '';
        const isTransfer = msg.total_size && msg.total_size > 0;
        
        if (isTransfer) {
            const sizeFormatted = formatBytes(msg.total_size);
            let speedFormatted = (msg.speed && !isNaN(msg.speed)) ? formatSpeed(msg.speed) : '';
            let elapsedFormatted = (msg.elapsed && !isNaN(msg.elapsed)) ? formatElapsed(msg.elapsed) : '';
            
            let statusText = '';
            let statusClass = '';
            const progress = (!isNaN(msg.progress)) ? Number(msg.progress) : 0;
            
            if (msg.direction === 'incoming') {
                if (msg.is_receiving) {
                    statusText = progress > 0 ? `📥 Ricezione ${progress.toFixed(1)}%` : '📥 In attesa...';
                } else {
                    statusText = '📥 Ricevuto';
                }
                statusClass = 'completed';
            } else {
                if (msg.status === 'delivered' || progress >= 100) {
                    statusText = '✅ Inviato';
                    statusClass = 'completed';
                } else if (msg.status === 'failed') {
                    statusText = '❌ Fallito';
                    statusClass = 'failed';
                } else if (progress > 0) {
                    statusText = `📤 Invio ${progress.toFixed(1)}%`;
                } else {
                    statusText = '📤 In attesa...';
                }
            }
            
            let infoParts = [];
            infoParts.push(`<span class="size">📦 ${sizeFormatted}</span>`);
            if (speedFormatted) infoParts.push(`<span class="speed">⚡ ${speedFormatted}</span>`);
            if (elapsedFormatted) infoParts.push(`<span class="time">⏱️ ${elapsedFormatted}</span>`);
            if (msg.parts) infoParts.push(`<span>📊 ${msg.parts}</span>`);
            
            transferInfo = `
                <div class="lxmf-transfer-info">
                    <span class="${statusClass}">${statusText}</span>
                    ${infoParts.join(' ')}
                </div>
            `;
        }
        
        let progressBar = '';
        if (isTransfer && msg.progress !== undefined && !isNaN(msg.progress) && msg.progress < 100 && msg.status !== 'delivered') {
            const progress = Number(msg.progress) || 0;
            progressBar = `
                <div class="lxmf-progress-laser" style="margin: 8px 0;">
                    <div class="lxmf-laser-fill" style="width: ${progress}%;"></div>
                    <div class="lxmf-laser-pulse" style="left: ${progress}%;"></div>
                </div>
            `;
        }
        
        let mediaPreview = '';
        if (msg.attachments && msg.attachments.length > 0) {
            msg.attachments.forEach(att => {
                const filename = att.saved_as ? att.saved_as.split('/').pop() : null;
                const fileUrl = filename ? `/downloads/${filename}` : null;
                
                if (att.type === 'image' && fileUrl) {
                    mediaPreview += `
                        <div class="lxmf-attachment">
                            <img class="lxmf-image-preview" src="${fileUrl}" 
                                 onclick="openModal('image', this.src)" 
                                 style="max-width: 200px; max-height: 200px; margin-top: 10px; border-radius: 4px; cursor: pointer;">
                        </div>
                    `;
                } else if (att.type && att.type.startsWith('audio') && fileUrl) {
                    const audioMode = att.audio_mode || 0;
                    
                    let icon = "🎵";
                    let codecLabel = "Opus";
                    
                    if (audioMode >= 1 && audioMode <= 9) {
                        icon = "🎤";
                        const bitrate = {
                            1: "450PWB", 2: "450", 3: "700", 4: "1200",
                            5: "1300", 6: "1400", 7: "1600", 8: "2400", 9: "3200"
                        }[audioMode] || audioMode;
                        codecLabel = `Codec2 ${bitrate}bps`;
                    } else if (audioMode >= 16) {
                        const opusQuality = {
                            16: "Ogg", 17: "LBW", 18: "MBW", 19: "PTT",
                            20: "RT HDX", 21: "RT FDX", 22: "Standard",
                            23: "HQ", 24: "Broadcast", 25: "Lossless"
                        }[audioMode] || "Opus";
                        codecLabel = `Opus ${opusQuality}`;
                    }
                    
                    mediaPreview += `
                        <div class="lxmf-attachment">
                            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                                <span style="font-size: 16px;">${icon}</span>
                                <span style="color: #888; font-size: 11px;">${codecLabel} (${formatBytes(att.size || 0)})</span>
                            </div>
                            <audio controls style="width: 100%; margin-top: 5px; margin-bottom: 10px;">
                                <source src="${fileUrl}" type="audio/ogg">
                                <source src="${fileUrl}" type="audio/webm">
                                Il tuo browser non supporta la riproduzione audio
                            </audio>
                        </div>
                    `;
                } else if (att.type === 'file' && fileUrl) {
                    mediaPreview += `
                        <div class="lxmf-attachment">
                            <div style="display: flex; align-items: center; gap: 10px; margin-top: 8px; padding: 6px; background: #1a1a1a; border-radius: 4px;">
                                <span style="font-size: 20px;">📎</span>
                                <span style="flex: 1; color: #88ff88;">${escapeHtml(att.name || 'file')}</span>
                                <span style="color: #888; font-size: 11px;">${formatBytes(att.size || 0)}</span>
                                <a href="${fileUrl}" download style="color: #88ff88; text-decoration: none; padding: 2px 8px; border: 1px solid #88ff88; border-radius: 4px;">💾</a>
                            </div>
                        </div>
                    `;
                }
            });
        }
        
        let rawHtml = '';
        if (rawViewActive) {
            if (msg.raw_hex) {
                // Usa i dati raw già presenti nel messaggio
                rawHtml = `
                    <div class="lxmf-message-raw">
                        <div class="lxmf-message-raw-hex">📦 HEX (${msg.raw_size || '?'} bytes):<br>${escapeHtml(msg.raw_hex.substring(0, 200))}${msg.raw_hex.length > 200 ? '...' : ''}</div>
                        <div class="lxmf-message-raw-ascii">📝 ASCII:<br>${escapeHtml(msg.raw_ascii ? msg.raw_ascii.substring(0, 200) : '')}${msg.raw_ascii && msg.raw_ascii.length > 200 ? '...' : ''}</div>
                        ${msg.lxmf_file ? `<button class="msg-raw-btn" onclick="showMessageRaw('${peerHash}', '${msg.lxmf_file}', ${msg.timestamp})" title="Mostra RAW completo">🔬 FULL</button>` : ''}
                    </div>
                `;
            } else if (msg.lxmf_file) {
                // Fallback: carica dal server
                const rawId = `raw-${msg.timestamp}-${Date.now()}`;
                rawHtml = `
                    <div class="lxmf-message-raw" id="${rawId}">
                        <div class="lxmf-message-raw-hex">📦 Caricamento dati raw...</div>
                    </div>
                `;
                setTimeout(() => loadRawData(msg, peerHash, rawId), 50);
            }
        }
        
        const contentHtml = formatMessageContent(msg);
        
        html += `
            <div class="lxmf-message ${direction}">
                <div class="lxmf-message-sender">${escapeHtml(sender)}</div>
                ${contentHtml}
                ${mediaPreview}
                ${transferInfo}
                ${progressBar}
                ${radioInfo}
                ${rawHtml}
                <div class="lxmf-message-time">
                    ${time}
                    ${statusIcon}
                    ${rawButton}
                    ${retryButton}
                </div>
            </div>
        `;
    });
    
    document.getElementById('messageList').innerHTML = html;
    document.getElementById('messageList').scrollTop = document.getElementById('messageList').scrollHeight;
}

async function loadRawData(msg, peerHash, rawId) {
    if (!msg.lxmf_file) return;
    
    try {
        const response = await fetch(`/api/chat/raw/${peerHash}/${msg.lxmf_file}`);
        const data = await response.json();
        
        if (data.raw_hex) {
            const rawElement = document.getElementById(rawId);
            if (rawElement) {
                rawElement.innerHTML = `
                    <div class="lxmf-message-raw-hex">📦 HEX (${data.raw_size || '?'} bytes):<br>${escapeHtml(data.raw_hex.substring(0, 200))}${data.raw_hex.length > 200 ? '...' : ''}</div>
                    <div class="lxmf-message-raw-ascii">📝 ASCII:<br>${escapeHtml(data.raw_ascii ? data.raw_ascii.substring(0, 200) : '')}${data.raw_ascii && data.raw_ascii.length > 200 ? '...' : ''}</div>
                `;
            }
        }
    } catch (e) {
        console.error("Errore caricamento raw:", e);
        const rawElement = document.getElementById(rawId);
        if (rawElement) {
            rawElement.innerHTML = '<div class="lxmf-message-raw-hex">❌ Errore caricamento raw</div>';
        }
    }
}

// ============================================
// STATISTICHE TRASFERIMENTO
// ============================================
function updateTransferStats() {
    if (activeTransfers.size === 0) {
        document.getElementById('transferStats').innerHTML = '';
        return;
    }
    
    let totalSpeed = 0;
    let activeCount = 0;
    let totalProgress = 0;
    
    activeTransfers.forEach(transfer => {
        if (transfer.speed && !isNaN(transfer.speed)) {
            totalSpeed += transfer.speed;
            activeCount++;
        }
        if (transfer.progress && !isNaN(transfer.progress)) {
            totalProgress += transfer.progress;
        }
    });
    
    if (activeCount > 0) {
        const avgSpeed = totalSpeed / activeCount;
        const avgProgress = totalProgress / activeTransfers.size;
        
        document.getElementById('transferStats').innerHTML = `
            <div class="lxmf-stats-bar">
                <span>📤 Trasferimenti attivi: ${activeTransfers.size}</span>
                <span>⚡ Velocità media: ${formatSpeed(avgSpeed)}</span>
                <span>📊 Progresso medio: ${avgProgress.toFixed(1)}%</span>
            </div>
        `;
    }
}

function clearMessages() {
    if (confirm('Cancellare tutti i messaggi di questa chat?')) {
        messages = [];
        renderMessages();
        debugLog('Messaggi cancellati');
    }
}

function exportConversation() {
    if (!currentPeer || messages.length === 0) return;
    
    const data = {
        peer: currentPeer,
        identity: currentIdentity,
        messages: messages,
        exported_at: new Date().toISOString()
    };
    
    const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chat_${cleanHash(currentPeer.hash).substring(0,8)}_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
    
    debugLog('Conversazione esportata');
}

// ============================================
// MODALE PER ANTEPRIME
// ============================================
function openModal(type, src) {
    const modal = document.getElementById('mediaModal');
    const modalImg = document.getElementById('modalImage');
    const modalVideo = document.getElementById('modalVideo');
    
    modalImg.style.display = 'none';
    modalVideo.style.display = 'none';
    modalVideo.pause();
    
    if (type === 'image') {
        modalImg.src = src;
        modalImg.style.display = 'block';
    } else if (type === 'video') {
        modalVideo.src = src;
        modalVideo.style.display = 'block';
        modalVideo.play();
    }
    
    modal.style.display = 'block';
}

function closeModal() {
    const modal = document.getElementById('mediaModal');
    const modalVideo = document.getElementById('modalVideo');
    modalVideo.pause();
    modal.style.display = 'none';
}

// ============================================
// FUNZIONI CONFIGURAZIONE
// ============================================
async function loadConfig() {
    try {
        const r = await fetch('/api/config');
        currentConfig = await r.json();
        
        document.getElementById('propagationNode').value = currentConfig.propagation_node || '';
        document.getElementById('deliveryMode').value = currentConfig.delivery_mode || 'direct';
        document.getElementById('maxRetries').value = currentConfig.max_retries || 3;
        document.getElementById('preferredAudio').value = currentConfig.preferred_audio || 'opus';
        document.getElementById('saveSent').checked = currentConfig.save_sent !== false;
        document.getElementById('autoRetry').checked = currentConfig.auto_retry !== false;
        document.getElementById('stampCost').value = currentConfig.stamp_cost === null ? '' : currentConfig.stamp_cost;
        document.getElementById('propagationStampCost').value = currentConfig.propagation_stamp_cost || 16;
        document.getElementById('acceptInvalidStamps').checked = currentConfig.accept_invalid_stamps || false;
        document.getElementById('maxStampRetries').value = currentConfig.max_stamp_retries || 3;
        
        toggleRetriesSection();
        
        showConfigStatus('✅ Configurazione caricata', 'success');
    } catch (e) {
        showConfigStatus('❌ Errore caricamento', 'error');
        debugLog('Errore loadConfig', e);
    }
}

async function saveConfig() {
    const config = {
        propagation_node: document.getElementById('propagationNode').value.trim() || null,
        delivery_mode: document.getElementById('deliveryMode').value,
        max_retries: parseInt(document.getElementById('maxRetries').value),
        preferred_audio: document.getElementById('preferredAudio').value,
        save_sent: document.getElementById('saveSent').checked,
        auto_retry: document.getElementById('autoRetry').checked,
        stamp_cost: document.getElementById('stampCost').value === '' ? null : parseInt(document.getElementById('stampCost').value),
        propagation_stamp_cost: parseInt(document.getElementById('propagationStampCost').value),
        accept_invalid_stamps: document.getElementById('acceptInvalidStamps').checked,
        max_stamp_retries: parseInt(document.getElementById('maxStampRetries').value)
    };
    
    if (config.propagation_node && config.propagation_node.length !== 64 && config.propagation_node.length !== 32) {
        showConfigStatus('⚠️ Hash propagation non valido (deve essere 32 o 64 caratteri)', 'warning');
        return;
    }
    
    try {
        const r = await fetch('/api/config', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(config)
        });
        const data = await r.json();
        
        if (data.success) {
            currentConfig = data.config;
            showConfigStatus('✅ Configurazione salvata!', 'success');
            toggleRetriesSection();
        } else {
            showConfigStatus('❌ Errore salvataggio', 'error');
        }
    } catch (e) {
        showConfigStatus('❌ Errore di connessione', 'error');
        debugLog('Errore saveConfig', e);
    }
}

function toggleRetriesSection() {
    const mode = document.getElementById('deliveryMode').value;
    const retriesSection = document.getElementById('retriesSection');
    
    if (mode === 'hybrid') {
        retriesSection.style.display = 'block';
    } else {
        retriesSection.style.display = 'none';
    }
}

function showConfigStatus(message, type) {
    const status = document.getElementById('configStatus');
    status.innerHTML = message;
    status.style.color = type === 'success' ? '#88ff88' : (type === 'error' ? '#ff8888' : '#ffaa00');
    setTimeout(() => {
        status.innerHTML = '';
    }, 3000);
}

// ============================================
// FUNZIONI PROPAGATION SYNC
// ============================================
async function syncPropagationNode() {
    if (isSyncing) {
        showNotification('Sincronizzazione già in corso...', 'info');
        return;
    }
    
    const btn = document.getElementById('propSyncBtn');
    btn.classList.add('syncing');
    btn.innerHTML = '🛰️ SYNC...';
    isSyncing = true;
    
    try {
        showNotification('📡 Richiesta sincronizzazione propagation node...', 'info');
        
        const r = await fetch('/api/lxmf/propagation/sync', {
            method: 'POST'
        });
        const data = await r.json();
        
        if (data.success) {
            showNotification('✅ Sincronizzazione avviata', 'success');
            startSyncProgressMonitoring();
        } else {
            showNotification(`❌ Errore: ${data.error || 'Sconosciuto'}`, 'error');
            resetSyncButton();
        }
    } catch (e) {
        showNotification('❌ Errore di connessione', 'error');
        console.error('Sync error:', e);
        resetSyncButton();
    }
}

function startSyncProgressMonitoring() {
    if (syncInterval) clearInterval(syncInterval);
    
    syncInterval = setInterval(async () => {
        try {
            const r = await fetch('/api/lxmf/propagation/status');
            const data = await r.json();
            
            if (data.status) {
                updateSyncProgress(data.status);
                
                if (data.status.state === 'complete' || 
                    data.status.state === 'failed' ||
                    data.status.state === 'no_path') {
                    
                    clearInterval(syncInterval);
                    syncInterval = null;
                    resetSyncButton();
                    
                    if (data.status.state === 'complete') {
                        showNotification(`✅ Ricevuti ${data.status.messages_received || 0} messaggi`, 'success');
                        if (currentPeer) {
                            lastMessagesJson = '';
                            pollMessages();
                        }
                    } else if (data.status.state === 'failed') {
                        showNotification('❌ Sincronizzazione fallita', 'error');
                    }
                }
            }
        } catch (e) {
            console.error('Status error:', e);
        }
    }, 1000);
}

function updateSyncProgress(status) {
    const btn = document.getElementById('propSyncBtn');
    
    const states = {
        'idle': '🛰️ SYNC',
        'path_requested': '🛰️ Cercando node...',
        'link_establishing': '🛰️ Connessione...',
        'link_established': '🛰️ Connesso',
        'request_sent': '🛰️ Richiesta...',
        'receiving': `🛰️ Ricevendo ${status.progress?.toFixed(0) || 0}%`,
        'complete': '🛰️ COMPLETATO',
        'failed': '🛰️ FALLITO'
    };
    
    btn.innerHTML = states[status.state] || '🛰️ SYNC';
    
    const syncProgress = document.getElementById('syncProgress');
    if (syncProgress) {
        syncProgress.innerHTML = `📡 Propagation: ${status.state} ${status.progress ? `(${status.progress.toFixed(0)}%)` : ''}`;
    }
}

function resetSyncButton() {
    const btn = document.getElementById('propSyncBtn');
    btn.classList.remove('syncing');
    btn.innerHTML = '🛰️ SYNC';
    isSyncing = false;
}

function showNotification(message, type = 'info') {
    let notification = document.getElementById('tempNotification');
    if (!notification) {
        notification = document.createElement('div');
        notification.id = 'tempNotification';
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 12px 24px;
            border-radius: 4px;
            font-weight: bold;
            z-index: 9999;
            animation: slideIn 0.3s ease;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        `;
        document.body.appendChild(notification);
    }
    
    const colors = {
        success: { bg: '#1e3a1e', border: '#88ff88', color: '#88ff88' },
        error: { bg: '#3a1e1e', border: '#ff8888', color: '#ff8888' },
        info: { bg: '#1e3a5f', border: '#88aaff', color: '#88aaff' }
    };
    
    const style = colors[type] || colors.info;
    notification.style.backgroundColor = style.bg;
    notification.style.border = `1px solid ${style.border}`;
    notification.style.color = style.color;
    notification.innerHTML = message;
    notification.style.display = 'block';
    
    setTimeout(() => {
        notification.style.display = 'none';
    }, 3000);
}

// ============================================
// FUNZIONI PEER (COMPLETE)
// ============================================
async function showPeerTab(tab) {
    currentPeerTab = tab;
    
    const tabs = ['all', 'favorites', 'home', 'mountain', 'world', 'work', 'bot-echo', 'lente'];
    tabs.forEach(t => {
        let idSuffix;
        if (t === 'bot-echo') {
            idSuffix = 'BotEcho';
        } else if (t === 'lente') {
            idSuffix = 'Lente';
        } else if (t === 'favorites') {
            idSuffix = 'Fav';
        } else {
            idSuffix = t.charAt(0).toUpperCase() + t.slice(1);
        }
        
        const el = document.getElementById(`tab${idSuffix}`);
        if (el) {
            if (t === tab) {
                el.classList.add('active');
            } else {
                el.classList.remove('active');
            }
        }
    });
    
    await loadPeers();
}

async function loadPeers() {
    try {
        let url;
        if (currentPeerTab === 'all') {
            url = '/api/chat/peers';
        } else if (currentPeerTab === 'favorites') {
            url = '/api/chat/peers/favorites';
        } else {
            url = `/api/chat/peers/group/${currentPeerTab}`;
        }
        
        const r = await fetch(url);
        const data = await r.json();
        peers = Array.isArray(data) ? data : (data.peers || []);
        
        debugLog('Peer caricati', { count: peers.length, tab: currentPeerTab });
        
        const online = peers.filter(p => p.online).length;
        let totalHops = 0;
        let validHopsCount = 0;
        
        peers.forEach(p => {
            if (p.hops !== undefined && p.hops !== null) {
                const hopsNum = Number(p.hops);
                if (!isNaN(hopsNum) && isFinite(hopsNum) && hopsNum >= 0 && hopsNum < 100) {
                    totalHops += hopsNum;
                    validHopsCount++;
                }
            }
        });
        
        const avgHops = validHopsCount > 0 ? totalHops / validHopsCount : 0;
        
        document.getElementById('onlineCount').textContent = online;
        document.getElementById('avgHops').textContent = avgHops.toFixed(1);
        
        sortPeers();
        renderPeerList();
        document.getElementById('peerCountDisplay').textContent = peers.length;
        document.getElementById('peerCount').textContent = peers.length;
        
        if (currentPeer) {
            const peerStillExists = peers.some(p => p.identity_hash === currentPeer.identity_hash);
            if (!peerStillExists) {
                currentPeer = null;
                document.getElementById('selectedPeerDisplay').innerHTML = 'Nessun peer selezionato';
                document.getElementById('selectedPeerSignal').innerHTML = '';
                document.getElementById('messageList').innerHTML = '<div class="lxmf-no-peer">👈 Seleziona un peer</div>';
                
                document.getElementById('messageInput').disabled = true;
                document.getElementById('sendButton').disabled = true;
                document.getElementById('fileBtn').disabled = true;
                document.getElementById('voiceBtn').disabled = true;
            }
        }
        
        if (!currentPeer && peers.length > 0) {
            await selectPeer(peers[0].hash);
        }
        
    } catch (e) {
        debugLog('Errore peers', e.message);
    }
}

function sortPeers() {
    if (sortMode === 'time') {
        peers.sort((a, b) => {
            const timeA = a.last_seen || 0;
            const timeB = b.last_seen || 0;
            return timeOrder === 'desc' ? timeB - timeA : timeA - timeB;
        });
    } else if (sortMode === 'hops') {
        peers.sort((a, b) => {
            const hopsA = a.hops !== undefined ? parseInt(a.hops) : 999;
            const hopsB = b.hops !== undefined ? parseInt(b.hops) : 999;
            return hopsOrder === 'asc' ? hopsA - hopsB : hopsB - hopsA;
        });
    } else if (sortMode === 'signal') {
        peers.sort((a, b) => {
            let scoreA = 0, scoreB = 0, countA = 0, countB = 0;
            
            if (a.rssi !== undefined && !isNaN(a.rssi)) {
                scoreA += (a.rssi + 100) * 0.6;
                countA++;
            }
            if (a.snr !== undefined && !isNaN(a.snr)) {
                scoreA += a.snr * 0.4;
                countA++;
            }
            if (a.quality !== undefined && !isNaN(a.quality)) {
                scoreA += parseFloat(a.quality);
                countA++;
            }
            
            if (b.rssi !== undefined && !isNaN(b.rssi)) {
                scoreB += (b.rssi + 100) * 0.6;
                countB++;
            }
            if (b.snr !== undefined && !isNaN(b.snr)) {
                scoreB += b.snr * 0.4;
                countB++;
            }
            if (b.quality !== undefined && !isNaN(b.quality)) {
                scoreB += parseFloat(b.quality);
                countB++;
            }
            
            if (countA > 0) scoreA = scoreA / countA;
            if (countB > 0) scoreB = scoreB / countB;
            
            if (countA === 0) scoreA = -999;
            if (countB === 0) scoreB = -999;
            
            return signalOrder === 'desc' ? scoreB - scoreA : scoreA - scoreB;
        });
    }
}

function setSortMode(mode) {
    if (mode === 'time') {
        if (sortMode === 'time') {
            timeOrder = timeOrder === 'desc' ? 'asc' : 'desc';
        } else {
            sortMode = 'time';
        }
        document.getElementById('timeIcon').textContent = timeOrder === 'desc' ? '⬇️' : '⬆️';
    } else if (mode === 'hops') {
        if (sortMode === 'hops') {
            hopsOrder = hopsOrder === 'asc' ? 'desc' : 'asc';
        } else {
            sortMode = 'hops';
        }
        document.getElementById('hopsIcon').textContent = hopsOrder === 'asc' ? '⬆️' : '⬇️';
    } else if (mode === 'signal') {
        if (sortMode === 'signal') {
            signalOrder = signalOrder === 'desc' ? 'asc' : 'desc';
        } else {
            sortMode = 'signal';
        }
        document.getElementById('signalIcon').textContent = signalOrder === 'desc' ? '⬇️' : '⬆️';
    }
    
    document.getElementById('sortTimeBtn').classList.toggle('active', sortMode === 'time');
    document.getElementById('sortHopsBtn').classList.toggle('active', sortMode === 'hops');
    document.getElementById('sortSignalBtn').classList.toggle('active', sortMode === 'signal');
    
    sortPeers();
    renderPeerList();
    debugLog('Ordinamento', { mode: sortMode, timeOrder, hopsOrder, signalOrder });
}

async function toggleFavorite(identityHash, event) {
    event.stopPropagation();
    
    try {
        const r = await fetch(`/api/identities/${identityHash}/favorite`, {
            method: 'POST'
        });
        const data = await r.json();
        
        if (data.success) {
            let wasFavorite = false;
            peers = peers.map(p => {
                if (p.identity_hash === identityHash || p.hash === identityHash) {
                    wasFavorite = p.favorite;
                    p.favorite = data.favorite;
                }
                return p;
            });
            
            if (currentPeer && (currentPeer.identity_hash === identityHash || currentPeer.hash === identityHash)) {
                currentPeer.favorite = data.favorite;
            }
            
            renderPeerList();
            
            if (currentPeerTab === 'favorites' && wasFavorite && !data.favorite) {
                setTimeout(() => loadPeers(), 100);
            }
            
            showNotification(
                data.favorite ? '⭐ Aggiunto ai preferiti' : '📋 Rimosso dai preferiti', 
                'success'
            );
        }
    } catch (e) {
        debugLog('Errore toggle favorite', e);
    }
}

async function toggleGroup(identityHash, group, event) {
    event.stopPropagation();
    
    try {
        const r = await fetch(`/api/identities/${identityHash}/group/${group}`, {
            method: 'POST'
        });
        const data = await r.json();
        
        if (data.success) {
            peers = peers.map(p => {
                if (p.identity_hash === identityHash || p.hash === identityHash) {
                    p.groups = data.groups;
                }
                return p;
            });
            
            if (currentPeer && (currentPeer.identity_hash === identityHash || currentPeer.hash === identityHash)) {
                currentPeer.groups = data.groups;
            }
            
            renderPeerList();
            
            if (currentPeerTab === group && !data.groups.includes(group)) {
                await loadPeers();
            } else if (currentPeerTab === group && data.groups.includes(group)) {
                await loadPeers();
            }
            
            const icon = {home:'🏠', mountain:'🏔️', world:'🌍', work:'🔧', 'bot-echo':'🤖', 'lente':'🔍'}[group] || group;
            showNotification(
                data.added ? `✅ Aggiunto a ${icon}` : `❌ Rimosso da ${icon}`, 
                'success'
            );
        }
    } catch (e) {
        debugLog('Errore toggle group', e);
    }
}

const GROUP_ICONS = {
    'home': '🏠',
    'mountain': '🏔️',
    'world': '🌍',
    'work': '🔧',
    'bot-echo': '🤖',
    'lente': '🔍'
};

const GROUP_COLORS = {
    'home': '#88ff88',
    'mountain': '#88aaff',
    'world': '#88ccff',
    'work': '#ffaa88',
    'bot-echo': '#ff9900',
    'lente': '#aa88ff'
};

function renderPeerList() {
    const search = document.getElementById('peerSearch').value.toLowerCase();
    let filtered = peers.filter(p => 
        (p.display_name && p.display_name.toLowerCase().includes(search)) ||
        (p.hash && p.hash.toLowerCase().includes(search))
    );
    
    document.getElementById('peerCount').textContent = filtered.length;
    
    if (filtered.length === 0) {
        document.getElementById('peerList').innerHTML = '<div class="lxmf-no-peer">Nessun peer trovato</div>';
        return;
    }
    
    let html = '';
    filtered.forEach(peer => {
        const peerHash = cleanHash(peer.hash);
        const isSelected = currentPeer && currentPeer.identity_hash && 
            cleanHash(currentPeer.identity_hash) === cleanHash(peer.identity_hash || peer.hash);
        const onlineClass = peer.online ? 'online' : 'offline';
        const timeAgo = getTimeAgo(peer.last_seen);
        const hasUnread = unreadPeers.has(peerHash);
        
        let displayName = cleanDisplayName(peer.display_name || 'Anonimo');
        if (displayName.length > 30) displayName = displayName.substring(0, 27) + '...';
        
        const hashDisplay = peer.hash ? peer.hash.substring(0, 24) + '...' : 'Hash non disponibile';
        
        const starClass = peer.favorite ? 'star-icon active' : 'star-icon';
        const starHtml = `<span class="${starClass}" onclick="toggleFavorite('${peer.identity_hash || peer.hash}', event)">⭐</span>`;
        
        const groupsActive = (peer.groups || []).map(g => 
            `<span class="group-indicator ${g}" title="${g}" style="color: ${GROUP_COLORS[g] || '#888'};">${GROUP_ICONS[g] || ''}</span>`
        ).join('');
        
        const isHomeActive = peer.groups?.includes('home') ? 'active-home' : '';
        const isMountainActive = peer.groups?.includes('mountain') ? 'active-mountain' : '';
        const isWorldActive = peer.groups?.includes('world') ? 'active-world' : '';
        const isWorkActive = peer.groups?.includes('work') ? 'active-work' : '';
        const isBotEchoActive = peer.groups?.includes('bot-echo') ? 'active-bot-echo' : '';
        const isLenteActive = peer.groups?.includes('lente') ? 'active-lente' : '';

        const homeStyle = peer.groups?.includes('home') ? `color: ${GROUP_COLORS.home}; text-shadow: 0 0 8px ${GROUP_COLORS.home}; background: rgba(136, 255, 136, 0.15);` : '';
        const mountainStyle = peer.groups?.includes('mountain') ? `color: ${GROUP_COLORS.mountain}; text-shadow: 0 0 8px ${GROUP_COLORS.mountain}; background: rgba(136, 170, 255, 0.15);` : '';
        const worldStyle = peer.groups?.includes('world') ? `color: ${GROUP_COLORS.world}; text-shadow: 0 0 8px ${GROUP_COLORS.world}; background: rgba(136, 204, 255, 0.15);` : '';
        const workStyle = peer.groups?.includes('work') ? `color: ${GROUP_COLORS.work}; text-shadow: 0 0 8px ${GROUP_COLORS.work}; background: rgba(255, 170, 136, 0.15);` : '';
        const botEchoStyle = peer.groups?.includes('bot-echo') ? `color: ${GROUP_COLORS['bot-echo']}; text-shadow: 0 0 8px ${GROUP_COLORS['bot-echo']}; background: rgba(255, 153, 0, 0.15);` : '';
        const lenteStyle = peer.groups?.includes('lente') ? `color: ${GROUP_COLORS.lente}; text-shadow: 0 0 8px ${GROUP_COLORS.lente}; background: rgba(170, 136, 255, 0.15);` : '';

        const groupsMenu = `
            <div class="peer-groups-menu" onclick="event.stopPropagation()">
                <button class="group-btn ${isHomeActive}" 
                    onclick="toggleGroup('${peer.identity_hash || peer.hash}', 'home', event)" 
                    title="Casa" style="${homeStyle}">🏠</button>
                <button class="group-btn ${isMountainActive}" 
                    onclick="toggleGroup('${peer.identity_hash || peer.hash}', 'mountain', event)" 
                    title="Montagna" style="${mountainStyle}">🏔️</button>
                <button class="group-btn ${isWorldActive}" 
                    onclick="toggleGroup('${peer.identity_hash || peer.hash}', 'world', event)" 
                    title="Mondo" style="${worldStyle}">🌍</button>
                <button class="group-btn ${isWorkActive}" 
                    onclick="toggleGroup('${peer.identity_hash || peer.hash}', 'work', event)" 
                    title="Lavoro" style="${workStyle}">🔧</button>
                <button class="group-btn ${isBotEchoActive}" 
                    onclick="toggleGroup('${peer.identity_hash || peer.hash}', 'bot-echo', event)" 
                    title="Bot Echo" style="${botEchoStyle}">🤖</button>
                <button class="group-btn ${isLenteActive}" 
                    onclick="toggleGroup('${peer.identity_hash || peer.hash}', 'lente', event)" 
                    title="Lente" style="${lenteStyle}">🔍</button>
            </div>
        `;
        
        const appearanceIcon = peer.telemetry?.appearance?.icon || 'account';
        
        let signalHtml = '';
        if (peer.rssi !== undefined || peer.snr !== undefined || peer.quality !== undefined) {
            let parts = [];
            if (peer.rssi !== undefined && !isNaN(peer.rssi)) {
                const rssiClass = getSignalClass(peer.rssi, 'rssi');
                parts.push(`<span class="${rssiClass}">📶 ${Number(peer.rssi).toFixed(1)}</span>`);
            }
            if (peer.snr !== undefined && !isNaN(peer.snr)) {
                const snrClass = getSignalClass(peer.snr, 'snr');
                parts.push(`<span class="${snrClass}">📊 ${Number(peer.snr).toFixed(1)}</span>`);
            }
            if (peer.quality !== undefined && !isNaN(peer.quality)) {
                parts.push(`⚡ ${Number(peer.quality).toFixed(1)}`);
            }
            signalHtml = `<div class="lxmf-peer-signal">${parts.join(' ')}</div>`;
        }
        
        const hopsHtml = peer.hops ? 
            `<span class="lxmf-peer-hops">🏓 ${peer.hops} hop</span>` : 
            '<span class="lxmf-peer-hops">🏓 ? hop</span>';
        
        html += `
            <div class="lxmf-peer-item ${onlineClass} ${isSelected ? 'selected' : ''} ${hasUnread ? 'unread' : ''}" 
                 onclick="selectPeer('${peer.hash || ''}')">
                <div class="lxmf-peer-header">
                    ${starHtml}
                    <span class="mdi mdi-${appearanceIcon}"></span>
                    <span class="lxmf-peer-name">${escapeHtml(displayName)}</span>
                    <span class="peer-groups-active">${groupsActive}</span>
                    ${hasUnread ? '<span class="lxmf-peer-unread">●</span>' : ''}
                </div>
                ${groupsMenu}
                <div class="lxmf-peer-hash">${hashDisplay}</div>
                ${signalHtml}
                <div class="lxmf-peer-footer">
                    <span class="lxmf-peer-time">${timeAgo}</span>
                    <span class="lxmf-peer-status">${peer.online ? 'ONLINE' : 'OFFLINE'}</span>
                    ${hopsHtml}
                </div>
            </div>
        `;
    });
    
    document.getElementById('peerList').innerHTML = html;
}

function clearSearch() {
    document.getElementById('peerSearch').value = '';
    renderPeerList();
}

async function selectPeer(hash) {
    if (!hash) return;
    
    const peerHash = cleanHash(hash);
    currentPeer = peers.find(p => p.identity_hash && cleanHash(p.identity_hash) === cleanHash(hash));
    
    if (currentPeer) {
        unreadPeers.delete(peerHash);
        
        debugLog('Peer selezionato', { name: currentPeer.display_name, hash: peerHash.substring(0,8) });
        
        let displayName = cleanDisplayName(currentPeer.display_name || 'Anonimo');
        if (displayName.length > 20) {
            displayName = displayName.substring(0, 17) + '...';
        }
        
        let hopsDisplay = '';
        if (currentPeer.hops !== undefined && currentPeer.hops !== null) {
            const hopsNum = Number(currentPeer.hops);
            if (!isNaN(hopsNum) && isFinite(hopsNum) && hopsNum >= 0 && hopsNum < 100) {
                hopsDisplay = ` <span style="color:#888; font-size:0.9em;">| 🏓 ${hopsNum} hop</span>`;
            }
        }
        
        document.getElementById('selectedPeerDisplay').innerHTML = `📨 ${escapeHtml(displayName)}${hopsDisplay}`;
        
        let signalHtml = '';
        if (currentPeer.rssi !== undefined || currentPeer.snr !== undefined || currentPeer.quality !== undefined) {
            let parts = [];
            if (currentPeer.rssi !== undefined && !isNaN(currentPeer.rssi)) {
                const rssiClass = getSignalClass(currentPeer.rssi, 'rssi');
                parts.push(`<span class="${rssiClass}">RSSI: ${Number(currentPeer.rssi).toFixed(1)}</span>`);
            }
            if (currentPeer.snr !== undefined && !isNaN(currentPeer.snr)) {
                const snrClass = getSignalClass(currentPeer.snr, 'snr');
                parts.push(`<span class="${snrClass}">SNR: ${Number(currentPeer.snr).toFixed(1)}</span>`);
            }
            if (currentPeer.quality !== undefined && !isNaN(currentPeer.quality)) {
                parts.push(`Q: ${Number(currentPeer.quality).toFixed(1)}`);
            }
            signalHtml = parts.join(' | ');
        }
        document.getElementById('selectedPeerSignal').innerHTML = signalHtml;
        
        document.getElementById('messageInput').disabled = false;
        document.getElementById('sendButton').disabled = false;
        document.getElementById('fileBtn').disabled = false;
        document.getElementById('voiceBtn').disabled = false;
        
        document.getElementById('messageInput').focus();
        
        lastMessagesJson = '';
        
        const mapContainer = document.getElementById('mapContainer');
        const mapPlaceholder = document.getElementById('mapPlaceholder');
        if (mapContainer) mapContainer.style.display = 'none';
        if (mapPlaceholder) {
            mapPlaceholder.style.display = 'flex';
            mapPlaceholder.innerHTML = '<span>🗺️ Seleziona un peer con posizione</span>';
        }
        
        if (mapMarker) {
            mapMarker.setOpacity(0);
        }
        
        const dashboard = document.getElementById('telemetryDashboard');
        if (dashboard) {
            dashboard.innerHTML = '<div class="telemetry-empty">👈 Seleziona un peer per vedere i dati</div>';
        }
        
        const rawContent = document.getElementById('rawContent');
        if (rawContent) {
            rawContent.innerHTML = '<div style="color: #888; text-align: center; margin-top: 20px;">Nessun pacchetto ricevuto</div>';
        }
        
        await pollMessages();
        
        renderPeerList();
    }
}

// ============================================
// FUNZIONI MESSAGGI (POLL)
// ============================================
async function pollMessages() {
    if (!currentPeer) return;
    
    try {
        const peerHash = cleanHash(currentPeer.hash);
        debugLog('Poll messaggi per', peerHash.substring(0,8));
        
        const r = await fetch(`/api/chat/messages?peer=${encodeURIComponent(peerHash)}`);
        const newMessages = await r.json();
        
        debugLog('Ricevuti messaggi', { count: newMessages?.length, primo: newMessages?.[0]?.content?.substring(0,30) });
        
        const newMessagesJson = JSON.stringify(newMessages);
        
        if (newMessagesJson !== lastMessagesJson) {
            lastMessagesJson = newMessagesJson;
            
            messages = newMessages;
            
            messageCache.set(peerHash, messages.length);
            
            renderMessages();
            
            if (messages.length > 0) {
                messages.forEach(msg => {
                    if (msg.direction === 'incoming') {
                        const packet = {
                            timestamp: msg.timestamp,
                            direction: msg.direction,
                            peer: msg.from || msg.to,
                            content: msg.content,
                            rssi: msg.rssi,
                            snr: msg.snr,
                            q: msg.q,
                            battery: msg.battery,
                            location: msg.location,
                            appearance: msg.appearance,
                            telemetry: msg.telemetry,
                            raw_hex: msg.raw_hex,
                            raw_ascii: msg.raw_ascii,
                            raw_size: msg.raw_size
                        };
                        
                        addMonitorPacket(packet);
                        
                        const telemetryData = {
                            timestamp: msg.timestamp,
                            battery: msg.battery,
                            location: msg.location,
                            appearance: msg.appearance,
                            information: msg.information
                        };
                        
                        if (msg.telemetry) {
                            if (msg.telemetry.battery) telemetryData.battery = msg.telemetry.battery;
                            if (msg.telemetry.location) telemetryData.location = msg.telemetry.location;
                            if (msg.telemetry.appearance) telemetryData.appearance = msg.telemetry.appearance;
                            if (msg.telemetry.information) telemetryData.information = msg.telemetry.information;
                        }
                        
                        saveTelemetryToDatabase(cleanHash(msg.from || msg.to), telemetryData);
                    }
                });
            }
            
            activeTransfers.clear();
            messages.forEach(msg => {
                if (msg.direction === 'outgoing' && msg.status === 'sending' && msg.progress < 100) {
                    activeTransfers.set(msg.id || msg.timestamp, msg);
                }
                if (msg.is_receiving) {
                    activeTransfers.set(msg.id || msg.timestamp, msg);
                }
            });
            
            if (monitorActive) {
                renderTelemetryDashboard();
            }
        }
    } catch (e) {
        debugLog('Errore poll', e.message);
    }
}

async function sendMessage() {
    if (!currentPeer) return;
    
    const input = document.getElementById('messageInput');
    const content = input.value.trim();
    
    if (!content && selectedFiles.length === 0) return;
    
    input.disabled = true;
    document.getElementById('sendButton').disabled = true;
    document.getElementById('voiceBtn').disabled = true;
    
    try {
        debugLog('Invio messaggio', { content: content.substring(0,30), files: selectedFiles.length });
        
        let response;
        
        if (selectedFiles.length > 0) {
            const file = selectedFiles[0];
            const formData = new FormData();
            formData.append('destination', currentPeer.hash);
            formData.append('file', file);
            if (content) formData.append('description', content);
            
            if (file.audioCodec) {
                formData.append('audio_codec', file.audioCodec);
            }
            
            response = await fetch('/api/chat/send-file', {
                method: 'POST',
                body: formData
            });
        } else {
            response = await fetch('/api/chat/send', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    destination: currentPeer.hash,
                    content: content
                })
            });
        }
        
        const data = await response.json();
        debugLog('Risposta invio', data);
        
        if (data.error) {
            alert(`❌ Errore: ${data.error}`);
        } else if (data.success) {
            input.value = '';
            selectedFiles = [];
            document.getElementById('filePreview').innerHTML = '';
            document.getElementById('fileInput').value = '';
            
            lastMessagesJson = '';
            setTimeout(() => pollMessages(), 500);
        }
    } catch (e) {
        debugLog('Errore invio', e.message);
        alert(`❌ Errore: ${e.message}`);
    } finally {
        input.disabled = false;
        document.getElementById('sendButton').disabled = false;
        document.getElementById('voiceBtn').disabled = false;
        input.focus();
    }
}

async function retryMessage(timestamp) {
    if (!currentPeer) return;
    
    const failedMsg = messages.find(m => 
        m.timestamp === timestamp && 
        m.direction === 'outgoing' && 
        m.status === 'failed'
    );
    
    if (!failedMsg || !failedMsg.content) {
        debugLog('Messaggio fallito non trovato per timestamp', timestamp);
        return;
    }
    
    debugLog('Riprova messaggio', { timestamp, content: failedMsg.content.substring(0,30) });
    
    const input = document.getElementById('messageInput');
    const sendBtn = document.getElementById('sendButton');
    
    input.disabled = true;
    sendBtn.disabled = true;
    
    try {
        const response = await fetch('/api/chat/send', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                destination: currentPeer.hash,
                content: failedMsg.content
            })
        });
        
        const data = await response.json();
        
        if (data.error) {
            alert(`❌ Errore: ${data.error}`);
        } else if (data.success) {
            showNotification('✅ Messaggio reinviato!', 'success');
            lastMessagesJson = '';
            setTimeout(() => pollMessages(), 500);
        }
    } catch (e) {
        debugLog('Errore riprova', e.message);
        alert(`❌ Errore: ${e.message}`);
    } finally {
        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
    }
}

// ============================================
// FUNZIONI INIZIALI
// ============================================
async function loadIdentities() {
    try {
        debugLog('Caricamento identità...');
        const r = await fetch('/api/chat/identities');
        const identities = await r.json();
        
        debugLog('Identità ricevute', identities);
        
        const select = document.getElementById('identitySelect');
        
        if (!identities || identities.length === 0) {
            select.innerHTML = '<option value="">❌ Nessuna identità trovata</option>';
            return;
        }
        
        let html = '<option value="">📁 Scegli identità...</option>';
        identities.forEach(id => {
            let shortHash = id.hash ? id.hash.replace(/[<>]/g, '').substring(0, 8) : '';
            html += `<option value="${id.path}">${id.name} (${shortHash})</option>`;
        });
        select.innerHTML = html;
        
    } catch (e) {
        debugLog('Errore caricamento identità', e.message);
        document.getElementById('identitySelect').innerHTML = '<option value="">❌ Errore caricamento</option>';
    }
}

async function selectIdentity() {
    const path = document.getElementById('identitySelect').value;
    if (!path) return;
    
    const btn = document.getElementById('initButton');
    const status = document.getElementById('initStatus');
    
    btn.disabled = true;
    status.innerHTML = '⏳ Inizializzazione...';
    
    try {
        debugLog('Selezione identità', path);
        
        const r = await fetch('/api/chat/identities/select', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path})
        });
        const data = await r.json();
        
        debugLog('Risposta select', data);
        
        if (data.error) throw new Error(data.error);
        
        currentIdentity = {
            name: data.identity.name,
            path: path,
            identity_hash: data.identity.identity_hash,
            delivery_hash: data.identity.delivery_hash,
            address: data.identity.address
        };
        
        if (!currentIdentity.delivery_hash && currentIdentity.identity_hash) {
            console.log("⚠️ Delivery hash mancante, uso identity_hash come fallback");
            currentIdentity.delivery_hash = currentIdentity.identity_hash;
        }
        
        console.log("✅ Identità caricata:", {
            name: currentIdentity.name,
            identity: currentIdentity.identity_hash?.substring(0, 16) + '...',
            delivery: currentIdentity.delivery_hash?.substring(0, 16) + '...'
        });
        
        updateIdentityDisplay();
        
        document.getElementById('connectionStatus').className = 'status online';
        document.getElementById('initPanel').style.display = 'none';
        document.getElementById('chatContainer').style.display = 'flex';
        
        await loadPeers();
        peerInterval = setInterval(loadPeers, 10000);
        pollInterval = setInterval(pollMessages, 2000);
        progressInterval = setInterval(updateTransferStats, 1000);
        
        debugLog('Client avviato', currentIdentity.name);
        
    } catch (e) {
        status.innerHTML = `❌ Errore: ${e.message}`;
        btn.disabled = false;
        debugLog('Errore selectIdentity', e.message);
    }
}

// ============================================
// INIZIALIZZAZIONE
// ============================================
document.addEventListener('DOMContentLoaded', () => {
    debugLog('App avviata');
    loadIdentities();
    
    if (Notification.permission !== 'denied') {
        Notification.requestPermission();
    }
    
    document.addEventListener('keydown', (e) => {
        if (e.ctrlKey && e.key === 'r') {
            e.preventDefault();
            refreshPeers();
        }
        if (e.key === 'Escape') {
            clearSearch();
        }
    });
    
    document.getElementById('peerSearch').addEventListener('input', renderPeerList);
    document.getElementById('messageInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    
    setInterval(() => {
        debugLog('STATO', { 
            messages: messages.length,
            currentPeer: currentPeer?.display_name,
            peerHash: currentPeer ? cleanHash(currentPeer.hash).substring(0,8) : null,
            cache: Object.fromEntries(messageCache)
        });
    }, 30000);
});

window.addEventListener('beforeunload', () => {
    if (peerInterval) clearInterval(peerInterval);
    if (pollInterval) clearInterval(pollInterval);
    if (progressInterval) clearInterval(progressInterval);
});

function refreshPeers() {
    loadPeers();
}

// Esponi funzioni globali (TUTTE!)
window.selectPeer = selectPeer;
window.sendMessage = sendMessage;
window.toggleRawView = toggleRawView;
window.toggleMonitor = toggleMonitor;
window.clearMonitorLog = clearMonitorLog;
window.handleFileSelect = handleFileSelect;
window.removeFile = removeFile;
window.setSortMode = setSortMode;
window.clearSearch = clearSearch;
window.clearMessages = clearMessages;
window.exportConversation = exportConversation;
window.openModal = openModal;
window.closeModal = closeModal;
window.retryMessage = retryMessage;
window.setMapStyle = setMapStyle;
window.showMessageRaw = showMessageRaw;
window.closeRawModal = closeRawModal;
window.copyToClipboard = copyToClipboard;
window.toggleVoiceRecording = toggleVoiceRecording;
window.stopVoiceRecording = stopVoiceRecording;
window.switchRawTab = switchRawTab;
window.toggleHex = toggleHex;
window.toggleAscii = toggleAscii;
window.showPeerTab = showPeerTab;
window.toggleFavorite = toggleFavorite;
window.toggleGroup = toggleGroup;
window.syncPropagationNode = syncPropagationNode;
window.loadConfig = loadConfig;
window.saveConfig = saveConfig;
window.toggleRetriesSection = toggleRetriesSection;
window.loadTelemetryHistory = loadTelemetryHistory;
window.focusHistoryMarker = focusHistoryMarker;
window.debugLog = debugLog;