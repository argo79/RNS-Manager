#!/usr/bin/env python3
"""
Database SQLite per identità LXMF
Sostituisce il sistema JSON
"""

import os
import sqlite3
import time
import json
import threading
from datetime import datetime

class LXMFIdentitiesDB:
    """Database SQLite per identità LXMF"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()
        print(f"📦 Identities DB: {db_path}")
    
    def _init_db(self):
        """Inizializza database identità"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Tabella principale identità
        c.execute('''
            CREATE TABLE IF NOT EXISTS identities (
                identity_hash TEXT PRIMARY KEY,
                delivery_hash TEXT UNIQUE,
                display_name TEXT,
                first_seen REAL,
                last_seen REAL,
                favorite INTEGER DEFAULT 0,
                notes TEXT,
                groups TEXT,  -- JSON array
                telemetry TEXT, -- JSON object
                appearance TEXT, -- JSON object
                metadata TEXT   -- JSON extra
            )
        ''')
        
        # Indici
        c.execute('CREATE INDEX IF NOT EXISTS idx_delivery ON identities(delivery_hash)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_last_seen ON identities(last_seen)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_favorite ON identities(favorite)')
        
        # Tabella per history degli annunci
        c.execute('''
            CREATE TABLE IF NOT EXISTS announce_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_hash TEXT,
                timestamp REAL,
                hops INTEGER,
                rssi REAL,
                snr REAL,
                quality REAL,
                via TEXT,
                data TEXT,
                FOREIGN KEY(delivery_hash) REFERENCES identities(delivery_hash)
            )
        ''')
        
        c.execute('CREATE INDEX IF NOT EXISTS idx_announce_delivery ON announce_history(delivery_hash)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_announce_time ON announce_history(timestamp)')
        
        conn.commit()
        conn.close()
        print("✅ Database identità inizializzato")
    
    def update_from_announce(self, announce):
        """Aggiorna identità da annuncio lxmf.delivery"""
        if announce.get('aspect') != 'lxmf.delivery':
            return None
        
        delivery_hash = announce.get('dest_hash', '')
        if not delivery_hash:
            return None
        
        delivery_clean = delivery_hash.lower()
        now = time.time()
        
        # Estrai display name da app_data
        display_name = None
        app_data = announce.get('data')
        if app_data:
            try:
                import LXMF
                if isinstance(app_data, str):
                    app_data = app_data.encode()
                dn = LXMF.display_name_from_app_data(app_data)
                if dn:
                    display_name = dn
            except:
                pass
        
        if not display_name:
            display_name = None
        
        # Cerca identità esistente per delivery_hash
        existing = self.get_by_delivery(delivery_clean)
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            if existing:
                # Aggiorna esistente
                identity_hash = existing['identity_hash']
                c.execute('''
                    UPDATE identities SET
                        last_seen = ?,
                        display_name = COALESCE(?, display_name)
                    WHERE identity_hash = ?
                ''', (now, display_name if display_name.startswith('LXMF_') else display_name, identity_hash))
            else:
                # Nuova identità
                identity_hash = delivery_clean  # Usa delivery_hash come identity_hash
                c.execute('''
                    INSERT INTO identities 
                    (identity_hash, delivery_hash, display_name, first_seen, last_seen, favorite, groups, telemetry, metadata)
                    VALUES (?, ?, ?, ?, ?, 0, '[]', '{}', '{}')
                ''', (identity_hash, delivery_clean, display_name, now, now))
            
            # Inserisci history
            c.execute('''
                INSERT INTO announce_history
                (delivery_hash, timestamp, hops, rssi, snr, quality, via, data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                delivery_clean,
                now,
                announce.get('hops', 0),
                announce.get('rssi'),
                announce.get('snr'),
                announce.get('q'),
                announce.get('via'),
                app_data
            ))
            
            # Pulisci history vecchia (mantieni ultimi 20)
            c.execute('''
                DELETE FROM announce_history
                WHERE delivery_hash = ? AND id NOT IN (
                    SELECT id FROM announce_history
                    WHERE delivery_hash = ?
                    ORDER BY timestamp DESC
                    LIMIT 20
                )
            ''', (delivery_clean, delivery_clean))
            
            conn.commit()
            conn.close()
            
            return {
                'identity_hash': identity_hash,
                'delivery_hash': delivery_clean,
                'display_name': display_name
            }
    
    def get_by_delivery(self, delivery_hash):
        """Trova identità per delivery hash"""
        delivery_clean = delivery_hash.lower()
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            SELECT identity_hash, delivery_hash, display_name, first_seen, last_seen,
                   favorite, notes, groups, telemetry, appearance, metadata
            FROM identities WHERE delivery_hash = ?
        ''', (delivery_clean,))
        
        row = c.fetchone()
        conn.close()
        
        if row:
            return {
                'identity_hash': row[0],
                'delivery_hash': row[1],
                'display_name': row[2],
                'first_seen': row[3],
                'last_seen': row[4],
                'favorite': bool(row[5]),
                'notes': row[6],
                'groups': json.loads(row[7]) if row[7] else [],
                'telemetry': json.loads(row[8]) if row[8] else {},
                'appearance': json.loads(row[9]) if row[9] else {},
                'metadata': json.loads(row[10]) if row[10] else {}
            }
        return None
    
    def get_by_identity(self, identity_hash):
        """Trova identità per identity hash"""
        identity_clean = identity_hash.lower()
        
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('''
            SELECT identity_hash, delivery_hash, display_name, first_seen, last_seen,
                   favorite, notes, groups, telemetry, appearance, metadata
            FROM identities WHERE identity_hash = ?
        ''', (identity_clean,))
        
        row = c.fetchone()
        conn.close()
        
        if row:
            return {
                'identity_hash': row[0],
                'delivery_hash': row[1],
                'display_name': row[2],
                'first_seen': row[3],
                'last_seen': row[4],
                'favorite': bool(row[5]),
                'notes': row[6],
                'groups': json.loads(row[7]) if row[7] else [],
                'telemetry': json.loads(row[8]) if row[8] else {},
                'appearance': json.loads(row[9]) if row[9] else {},
                'metadata': json.loads(row[10]) if row[10] else {}
            }
        return None
    
    def toggle_favorite(self, identity_hash):
        """Toggle preferito"""
        identity_clean = identity_hash.lower()
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # Leggi stato attuale
            c.execute('SELECT favorite FROM identities WHERE identity_hash = ?', (identity_clean,))
            row = c.fetchone()
            
            if not row:
                conn.close()
                return False
            
            new_status = 0 if row[0] else 1
            
            c.execute('UPDATE identities SET favorite = ? WHERE identity_hash = ?', 
                     (new_status, identity_clean))
            
            conn.commit()
            conn.close()
            
            return bool(new_status)
    
    def toggle_group(self, identity_hash, group):
        """Aggiunge/rimuove gruppo"""
        identity_clean = identity_hash.lower()
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            c.execute('SELECT groups FROM identities WHERE identity_hash = ?', (identity_clean,))
            row = c.fetchone()
            
            if not row:
                conn.close()
                return None
            
            groups = json.loads(row[0]) if row[0] else []
            
            if group in groups:
                groups.remove(group)
                added = False
            else:
                groups.append(group)
                added = True
            
            c.execute('UPDATE identities SET groups = ? WHERE identity_hash = ?',
                     (json.dumps(groups), identity_clean))
            
            conn.commit()
            conn.close()
            
            return {'added': added, 'groups': groups}
    
    def set_display_name(self, identity_hash, name):
        """Imposta nome visualizzato"""
        identity_clean = identity_hash.lower()
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            c.execute('UPDATE identities SET display_name = ? WHERE identity_hash = ?',
                     (name, identity_clean))
            
            conn.commit()
            conn.close()
            
            return True
    
    def update_telemetry(self, delivery_hash, telemetry_data):
        """Aggiorna telemetria"""
        delivery_clean = delivery_hash.lower()
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            c.execute('SELECT telemetry FROM identities WHERE delivery_hash = ?', (delivery_clean,))
            row = c.fetchone()
            
            if row:
                current = json.loads(row[0]) if row[0] else {}
                current.update(telemetry_data)
                
                c.execute('UPDATE identities SET telemetry = ? WHERE delivery_hash = ?',
                         (json.dumps(current), delivery_clean))
                
                conn.commit()
            
            conn.close()
    
    def update_appearance(self, delivery_hash, appearance_data):
        """Aggiorna icona profilo"""
        delivery_clean = delivery_hash.lower()
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            c.execute('UPDATE identities SET appearance = ? WHERE delivery_hash = ?',
                     (json.dumps(appearance_data), delivery_clean))
            
            conn.commit()
            conn.close()
    
    def get_all_peers(self, include_inactive=True, limit_days=7):
        """Ottieni tutti i peer con ultimi dati"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        cutoff = time.time() - (limit_days * 86400) if not include_inactive else 0
        
        c.execute('''
            SELECT i.identity_hash, i.delivery_hash, i.display_name, 
                   i.last_seen, i.favorite, i.groups, i.telemetry, i.appearance,
                   MAX(a.timestamp) as last_announce,
                   AVG(a.hops) as avg_hops,
                   AVG(a.rssi) as avg_rssi,
                   AVG(a.snr) as avg_snr,
                   COUNT(a.id) as announce_count
            FROM identities i
            LEFT JOIN announce_history a ON i.delivery_hash = a.delivery_hash
            WHERE i.last_seen >= ?
            GROUP BY i.identity_hash
            ORDER BY i.favorite DESC, i.last_seen DESC
        ''', (cutoff,))
        
        rows = c.fetchall()
        conn.close()
        
        peers = []
        now = time.time()
        
        for row in rows:
            is_online = (now - row[3]) < 7200  # 2 ore
            
            peer = {
                'hash': row[1],  # delivery_hash
                'identity_hash': row[0],
                'display_name': row[2],
                'last_seen': row[3],
                'online': is_online,
                'favorite': bool(row[4]),
                'groups': json.loads(row[5]) if row[5] else [],
                'telemetry': json.loads(row[6]) if row[6] else {},
                'appearance': json.loads(row[7]) if row[7] else {},
                'hops': row[9] or 0,
                'rssi': row[10],
                'snr': row[11],
                'quality': None,
                'announce_count': row[12] or 0
            }
            peers.append(peer)
        
        return peers
    

    def import_from_sqlite(self, sqlite_path):
        """Importa identità dal database SQLite del monitor"""
        try:
            if not os.path.exists(sqlite_path):
                return 0
            
            conn = sqlite3.connect(sqlite_path)
            c = conn.cursor()
            c.execute("SELECT timestamp, dest_hash, data, hops, rssi, snr, q, via FROM announces WHERE aspect='lxmf.delivery'")
            rows = c.fetchall()
            conn.close()
            
            print(f"📊 Trovati {len(rows)} annunci lxmf.delivery")
            
            imported = 0
            errors = 0
            for i, row in enumerate(rows):
                try:
                    announce = {
                        'aspect': 'lxmf.delivery',
                        'timestamp': row[0],
                        'dest_hash': row[1],
                        'data': row[2],
                        'hops': row[3],
                        'rssi': row[4],
                        'snr': row[5],
                        'q': row[6],
                        'via': row[7]
                    }
                    if self.update_from_announce(announce):
                        imported += 1
                    else:
                        errors += 1
                    
                    if (i+1) % 100 == 0:
                        print(f"   → {i+1}/{len(rows)} processati, {imported} importate")
                        
                except Exception as e:
                    print(f"❌ Errore riga {i}: {e}")
                    errors += 1
            
            print(f"✅ Importate {imported} identità dal monitor ({errors} errori)")
            return imported
        except Exception as e:
            print(f"❌ Errore import SQLite: {e}")
            import traceback
            traceback.print_exc()
            return 0


    def get_stats(self):
        """Statistiche database"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        c.execute('SELECT COUNT(*) FROM identities')
        total = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM identities WHERE favorite = 1')
        favorites = c.fetchone()[0]
        
        c.execute('SELECT COUNT(*) FROM announce_history')
        history = c.fetchone()[0]
        
        c.execute('SELECT MIN(last_seen), MAX(last_seen) FROM identities')
        time_range = c.fetchone()
        
        conn.close()
        
        return {
            'total_identities': total,
            'favorites': favorites,
            'total_announces': history,
            'first_seen': time_range[0],
            'last_seen': time_range[1]
        }