# Sideband rangetest plugin

Questo command plugin aggiunge a Sideband il comando LXMF:

```text
rangetest <intervallo> start
rangetest 0 stop
```

Il plugin chiede periodicamente la telemetria al peer che ha inviato il
comando. Sideband salva le risposte nel proprio database; allo stop il plugin
esporta i punti con coordinate in GPX e KML.

## Installazione

1. In Sideband apri `Settings` → `Plugins`.
2. Attiva **Enable Plugins**.
3. Attiva **Enable Command Plugins**.
4. Seleziona come **Plugins Directory** la cartella `sideband_plugins/` del
   repository.

   In alternativa copia `rangetest.py` in una directory dedicata, ad esempio:

   ```bash
   mkdir -p ~/.config/sideband/plugins
   cp sideband_plugins/rangetest.py ~/.config/sideband/plugins/
   ```

   e seleziona `~/.config/sideband/plugins`.

5. Riavvia Sideband.
6. Verifica nei log che compaia:

   ```text
   Rangetest command plugin started
   Registered ... as handler for command "rangetest"
   ```

## Uso

Dalla modalità **Send command or request** di Sideband:

```text
rangetest 15 start
```

e per fermare:

```text
rangetest 0 stop
```

L’intervallo viene limitato tra 5 e 3600 secondi.

I file vengono creati in:

```text
~/.config/sideband/app_storage/rangetest/
```

con nomi:

```text
rangetest_<peer>_<timestamp>.gpx
rangetest_<peer>_<timestamp>.kml
```

## Permessi

Per ricevere il comando `rangetest`, Sideband deve autorizzare il peer che lo
invia. In Sideband verifica le impostazioni dei contatti/trusted peer.

Per ricevere le risposte telemetria, il peer remoto deve avere abilitate le
richieste telemetria:

- `allow_telemetry_requests = true` nel RNS Messenger, oppure
- `allow_telemetry` abilitato per il peer.

## Limiti

- Vengono esportati solo i punti che contengono una posizione valida.
- Sideband non memorizza nello stesso record RSSI/SNR/Q della ricezione; il
  plugin esporta la telemetria disponibile, non le metriche radio del pacchetto.
- Il plugin usa l’API Sideband `request_latest_telemetry()` e
  `peer_telemetry()`, disponibile in Sideband 1.5.0.
