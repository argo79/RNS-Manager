# RNS-Manager

Monorepo con due applicazioni Reticulum (RNS) indipendenti:

| Progetto | Descrizione | Porta |
|---|---|---|
| [**manager/**](manager/) | Identity Manager + Aspect Monitor: gestione identità RNS, cifratura/firma, monitor annunci in tempo reale con cache SQLite | `5010` |
| [**messenger/**](messenger/) | RNS Messenger: chat LXMF con allegati, audio Codec2/Opus, telemetria, mappa e comandi remoti | `5012` |

Le due applicazioni hanno **codice, dipendenze, build e README separati** e condividono soltanto la cartella dati `~/.rns_manager/` (identità, `config.json`, upload, cache annunci).

## 📁 Struttura del repository

```ini
RNS-Manager/
├── manager/          # Flask Identity Manager + Aspect Monitor
│   ├── rns_manager.py
│   ├── modules/rns_monitor.py
│   ├── templates/ static/ img/
│   ├── requirements.txt
│   └── README.md / README.en.md
├── messenger/        # Flask-SocketIO LXMF Messenger
│   ├── lxmf_chat.py
│   ├── messenger.py
│   ├── core/
│   ├── templates/ static/ img/
│   ├── requirements.txt
│   └── README.md / README.en.md
├── legacy/           # materiale storico non utilizzato (non incluso nelle build)
├── LICENSE
└── .gitignore
```

## 🚀 Avvio rapido

### Manager (Identity Manager + Monitor)

```bash
cd manager
pip install -r requirements.txt
python3 rns_manager.py     # oppure ./start.sh
```

Aprire <http://127.0.0.1:5010/>.

### Messenger (LXMF Chat)

```bash
cd messenger
pip install -r requirements.txt
python3 lxmf_chat.py -p 5012     # oppure ./start.sh
```

Aprire <http://127.0.0.1:5012/>.

## 📸 Screenshot

| Identity Manager | Aspect Monitor |
|---|---|
| ![Identity Manager](manager/img/rnidGUI.jpg) | ![Aspect Monitor](manager/img/rnsMonitor2.jpg) |

| LXMF Messenger | Monitor mobile |
|---|---|
| ![LXMF Chat](messenger/img/lxmf-chat.jpg) | ![Monitor mobile](manager/img/monitor-mobile-live.jpg) |

## 🔒 Dati e privacy

Il repository **non contiene** dati personali o di runtime: cache, database SQLite, download, upload, socket, identità e segreti sono esclusi da `.gitignore`.

I dati delle applicazioni restano nella home dell'utente:

```ini
~/.rns_manager/storage/     # identità RNS
~/.rns_manager/config.json  # configurazione del messenger
~/.rns_manager/uploads/     # allegati caricati
```

## 🧰 Requisiti comuni

- Python 3.8+ (manager) / 3.9+ (messenger)
- Reticulum installato (`pip install rns`)
- Comandi `rnid`, `rnstatus`, `rnpath`, `rnprobe` nel `PATH` per il manager

## 📝 Licenza

Distribuito sotto licenza MIT. Vedi [LICENSE](LICENSE).

## 📧 Contatto

- Email: <arg0netds@gmail.com>
- GitHub: <https://github.com/argo79/RNS-Manager>
- RNS Identity: `04511923b68ae34e0fda5721d82f596f`

---

English: see [manager/README.en.md](manager/README.en.md) and [messenger/README.en.md](messenger/README.en.md).
