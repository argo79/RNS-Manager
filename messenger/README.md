# 💬 RNS Messenger

**English** | [Русский](README.ru.md)

Web messenger for **LXMF** (Lightweight Extensible Message Format) over Reticulum, with attachments, voice messages, telemetry, map and remote commands.

This is a separate project from the [Manager](../manager/README.md): the only shared resource is the `~/.rns_manager/` data directory (identities, configuration, uploads).

![LXMF Chat](img/lxmf-chat.jpg)

## ✨ Features

- 💬 One-to-one LXMF chat with persistent conversation history
- 👥 Peers with groups/trust (favorite, home, work, family, friends, server, bot, monitor, research, public)
- 📎 Attachments: files, images and audio
- 🎙️ In-browser Opus recording and **Codec2** support (450–3200 bps) via `pycodec2`
- 📊 Telemetry: system, battery, pressure, temperature, humidity, magnetic field, location, physical link
- 🗺️ Leaflet map with peer positions and telemetry history
- 🎮 LXMF remote commands: telemetry, ping, echo, signal report, rangetest
- 📡 Propagation nodes: configuration and message synchronisation
- 📣 Announce sending and peer import from the monitor cache
- ⚡ Real-time updates via **Socket.IO** (send progress, incoming messages)
- 🗃️ Browsable and downloadable raw message archive

## 🚀 Installation

### Requirements
- Python 3.9+
- Reticulum (`rns`), `lxmf`, `lxst`
- An existing RNS identity (created/imported with the Manager in `~/.rns_manager/storage/`)

### Dependencies

```bash
cd RNS-Manager/messenger
pip install -r requirements.txt
```

Optional:
- `ffmpeg` in `PATH` for OGG/Opus audio conversion
- `pycodec2` + libcodec2 for Codec2 messages
- `gps` / `gpsd` for location telemetry from GPSD

### Run

```bash
./start.sh
# or
python3 lxmf_chat.py -p 5012
```

With a specific identity:

```bash
python3 lxmf_chat.py -i ~/.rns_manager/storage/<identity_name> -p 5012
```

Then open <http://127.0.0.1:5012/> (alias: `/chat`).

> The server listens on `127.0.0.1` by default.
> To expose it on the LAN: `python3 lxmf_chat.py --host 0.0.0.0 -p 5012`.

## 📁 Structure

```ini
messenger/
├── lxmf_chat.py          # Flask + Socket.IO web server (entry point)
├── messenger.py          # LXMF core: identity, send/receive, telemetry, commands
├── audio_codec.py        # Codec2 / Opus (pycodec2 + LXST)
├── telemetry_provider.py # System and location sensors
├── core/
│   └── telemeter.py      # FIELD_TELEMETRY fields (LXMF sensors)
├── templates/
│   └── lxmf_chat.html    # Web interface
├── static/               # CSS, favicon, Opus/WASM encoder
├── version.py
├── requirements.txt
├── rns_messenger.spec    # PyInstaller spec
└── build_rns_messenger.sh
```

## 🗂️ Data shared with the Manager

```ini
~/.rns_manager/storage/          # identities and peers/conversations databases
~/.rns_manager/config.json       # configuration (propagation node, GPSD, etc.)
~/.rns_manager/uploads/          # uploaded files
~/.rns_manager/Cache/announces.db
```

Identities imported through the Manager are available in the messenger and vice versa.

## 🎮 Usage

1. Select an identity from the list (or start with `-i`).
2. Pick a peer and send text, files, images or audio.
3. Open the telemetry panel to request data or follow it on the map.
4. Use the command panel for ping/echo/signal report/rangetest.
5. Configure a propagation node if peers are not directly reachable.

## 📊 Main APIs

```ini
GET  /                            GET  /chat
GET  /api/status                  GET  /api/identities
POST /api/identity/select         GET  /api/peers
GET  /api/conversation/<hash>     POST /api/send
POST /api/command                 POST /api/telemetry/request
GET  /api/telemetry/config        POST /api/telemetry/config
GET  /api/telemetry/history/<h>   POST /api/telemetry/test
GET  /api/peer/<hash>/telemetry/last
GET  /api/raw/history             GET  /api/raw/history/<hash>
GET  /api/raw/<hash>              GET  /download/<filename>
GET  /api/config                  POST /api/config
POST /api/sync                    POST /api/announce
POST /api/peers/import            POST /api/cleanup_temp
GET  /api/attachment/<hash>       POST /api/shutdown
GET  /api/favorites               POST /api/favorites/add
POST /api/favorites/remove        POST /api/peer/update
```

Socket.IO events: `connect`, `disconnect`, `ping_server` (client → server); `progress_update`, `message`, `identity_loaded` (server → client).

## 📦 Standalone build

```bash
cd messenger
./build_rns_messenger.sh
# output: dist/rns_messenger_v<version>.run
```

## 🔧 Troubleshooting

- **`No module named 'flask_socketio'`** → `pip install -r requirements.txt`
- **`No module named 'gpsdclient'`** → `pip install gpsdclient`
- **No identities available** → import/create one with the [Manager](../manager/README.md) or copy it into `~/.rns_manager/storage/`
- **Audio not converted** → install `ffmpeg`; for Codec2 install `pycodec2` with libcodec2
- **Empty map** → request telemetry from a peer that exposes its location

## 🔗 Related projects

- **RNS Manager & Monitor** ([`../manager/`](../manager/)): identity management and announce monitor on port **5010**.

## 📝 License

Distributed under the MIT License. See [`../LICENSE`](../LICENSE).

## 📧 Contact

- Email: <arg0netds@gmail.com>
- GitHub: <https://github.com/argo79/RNS-Manager>
- RNS Identity: `04511923b68ae34e0fda5721d82f596f`

