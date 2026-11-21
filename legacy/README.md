# legacy/

Materiale storico **non utilizzato** dalle due applicazioni e **non incluso nelle build**.
Resta qui solo come riferimento; può essere eliminato senza impatti.

| File | Note |
|---|---|
| `rns_lxmf.py` | Vecchio monolite CLI del messenger (nessun server web). Importa `telemeter` dalla root: non funzionante nello stato attuale. |
| `lxmf_message_sender.py` | Versione precedente del layer di invio messaggi, sostituita da `messenger/messenger.py`. |
| `lxmf_message_sender_modules.py` | Variante del file precedente, già nella vecchia cartella `modules/`. |
| `propagation_manager.py` | Gestione manuale dei nodi di propagazione LXMF; oggi il router LXMF la gestisce internamente. |
| `announce_handler.py` | Handler di announce generico della vecchia cartella `backend/`. |
| `lxmf_identities_db.py` | Helper identità mai collegato all'app. |
| `monitor.old.html` / `monitor.new.html` | Versioni precedenti del template del monitor. |
| `debug.html` | Vecchia pagina di debug (usa il percorso `/chat`). |
| `static_js/lxmf_chat.js` | Vecchio frontend JS del messenger, non referenziato dal template attuale. |
| `static_js/lxmf_client.js` | Vecchio client JS del messenger, non referenziato. |
