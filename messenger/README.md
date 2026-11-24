# 💬 RNS Messenger

**English** | [Русский](README.ru.md)

Web messenger for **LXMF** (Lightweight Extensible Message Format) over Reticulum, with attachments, voice messages, telemetry, map and remote commands.

This is a separate project from the [Manager](../manager/README.md): the only shared resource is the `~/.rns_manager/` data directory (identities, configuration, uploads).

![RNS Messenger](img/lxmf-chat.jpg)

## ✨ Features

- 💬 One-to-one LXMF chat with persistent conversation history
- 👥 Peers with multiple groups/trust (favorite, home, work, family, friends, server, bot, monitor, research, public; a peer can belong to several groups and new peers start with no group)
- 📢 Manual identity announce button in the header
- 📎 Attachments: files, images and audio
- 🎙️ In-browser recording with browser playback of **Codec2 1200/3200** (decoded server-side to WAV) and **Opus** (Ogg/WebM)
- 📊 Telemetry: system, battery, pressure, temperature, humidity, magnetic field, location, physical link
- 🛰️ Telemetry request diagnostics and global request/command permission toggles
- 📇 Compact expandable contacts and click-to-expand raw packet HEX/ASCII details
- 🗺️ Leaflet map with peer positions and telemetry history
- 🎮 LXMF remote commands: telemetry, ping, echo, signal report, rangetest
- 📡 Propagation nodes: configuration and message synchronisation
- 📣 Announce sending and peer import from the monitor cache
- 🌐 Interface translations (Italian and English; easily extensible)
- ⚡ Real-time updates via **Socket.IO** (send progress, incoming messages)
- 🔔 In-app/browser message and call notifications, unread badges, ringtone and notification settings
- 🗃️ Browsable and downloadable raw message archive

- **UI translations**: `static/messenger_translations.json`; the default language follows the browser, can be changed from the header, and is saved in `localStorage`.

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
> To expose it on the LAN: `python3 lxmf_chat.py --host 0.0.0.0 -p 5012`
> (not recommended: the web UI has no authentication).

## 🔐 Security notes

- The web server binds to localhost by default; requests with non-local `Host` headers or
  cross-origin `Origin`/`Referer` on state-changing methods are rejected (DNS-rebinding/CSRF hardening).
- Security headers (`CSP`, `X-Frame-Options`, `X-Content-Type-Options`) are enabled.
- **Do not expose the UI on the LAN/Internet without authentication and TLS**: anyone who can
  reach the port can use the loaded identity.
- Remote LXMF commands (`ping`, `echo`, `signal`, telemetry requests, `rangetest`) are **denied by default**.
  Enable them per peer with `allow_commands` / `allow_telemetry` in `destinations`, or globally with
  `allow_remote_commands` / `allow_telemetry_requests` in `~/.rns_manager/config.json`.
- Uploads are limited to 64 MB and file names are sanitized. Images are served only for
  whitelisted formats (`jpg`, `png`, `gif`, `webp`); everything else is an attachment.
- Optional token auth: set `LXMF_AUTH_TOKEN` and open the UI with `?token=...` (cookie/header supported).
- `RNS_DEBUG_PACKETS=1` enables full packet hex/ASCII dumps (very verbose).
- Data retention (config.json, `0` disables): `message_retention_days` (default 90),
  `raw_retention_days` (30), `telemetry_retention_days` (90), `rangetest_retention_days` (90).
- Leaflet, Socket.IO and MDI assets are loaded from pinned CDNs: the browser needs internet access
  for the map/icon/realtime assets (the application backend works offline).

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
├── static/               # CSS, favicon, vendor/ (Leaflet, Socket.IO)
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
~/.rns_manager/cache/announces.db
```

Identities imported through the Manager are available in the messenger and vice versa.

## 🎮 Usage

1. Select an identity from the list (or start with `-i`).
2. Pick a peer and send text, files, images or audio.
3. Open the telemetry panel to request data or follow it on the map.
4. Use the command panel for ping/echo/signal report/rangetest.
5. Configure a propagation node if peers are not directly reachable.
6. Filter the peer list by announce aspect. Defaults are `lxmf.delivery`, `lxmf.propagation` and `rnstransport.discovery.interface`; add more in the **CONFIG** tab or with `announce_aspects` / `LXMF_ANNOUNCE_ASPECTS=a,b`.
7. In `lxmf.propagation` mode the list shows propagation nodes, with per-node actions to set or replace the current propagation node. The map can be opened in any aspect; in delivery mode it shows contacts with known positions together with discovery interfaces, and markers can be filtered by contact/interface type.
8. In `rnstransport.discovery.interface` mode the map opens over the central area automatically; interface markers show the interface name and hop count, with type represented by color/icon.
9. The map uses free, keyless providers (Esri World Street/Topographic/Imagery, OpenStreetMap, OpenTopoMap, CyclOSM) and Leaflet/Socket.IO are served locally from `static/vendor/`. A visible layer selector switches provider without changing the current center/zoom. **The map never follows incoming announcements:** only user pan/zoom, the explicit 🎯 “fit markers” and 📍 “center on me” actions change the view. Selecting a peer from the left list pans to its marker while preserving the current zoom; clicking a marker selects the same contact in the left list without panning and opens a popup with Chat, Telemetry and Call actions.
10. Map tools include distance (📏), circular area (⭕) and rectangular area (▭); the readout and preview update live while moving the mouse. Measurements stay on a separate layer and do not disappear when announcements refresh. Use 🧹 to clear them.
11. `rnstransport.discovery.interface` announcements are decoded safely: the UI shows interface type/name, host/port, coordinates and RNode radio parameters (frequency, bandwidth, SF, CR), but never IFAC net keys or generated config entries. No discovered interface is auto-connected. The interface marker popup can add supported local interface sections (RNode, Backbone Client, TCP Client, KISS, Weave, I2P) to `~/.reticulum/config`; missing local parameters must be entered because they are not published by discovery. Existing interfaces have a single ON/OFF toggle in the popup; missing interfaces can be added from the same form. Config changes require restarting Reticulum/rnsd to take effect.
12. Configure or remove the propagation node from **CONFIG → NODO PROPAGATION** using **IMPOSTA** / 🗑️. The setting is applied immediately and saved to `config.json`; changing it asks for confirmation when another node is already configured.
13. **📶 STATO RNS** opens the raw `rnstatus` output (ANSI stripped) in a modal without invoking a shell; the command is looked up in `PATH` and has an 8 second timeout.
14. **ESCI** stops the server completely; **CAMBIA IDENTITÀ** switches identity without stopping it.

### Publishing interface discovery

The Messenger only needs to listen to `rnstransport.discovery.interface`; no Reticulum configuration is required to **receive** discovery announcements. To make one of your own interfaces discoverable, configure it on the announcing node, for example:

```ini
[[My RNode]]
  type = RNodeInterface
  interface_enabled = yes
  # ... interface-specific parameters ...
  discoverable = yes
  discovery_name = My node
  latitude = 45.4642
  longitude = 9.1900
  height = 120
  # Optional: a command printing "lat,lon,alt"
  # location_cmd = /usr/local/bin/gps-location
```

When a discovered `BackboneInterface` or `TCPServerInterface` is added from the map popup, RNS Manager writes a **client** interface (`BackboneClientInterface` or `TCPClientInterface`) pointing to the announced `reachable_on:port`; it never opens a local listener.

Only interfaces supporting Reticulum discovery can be published (`BackboneInterface`, `TCPServerInterface`, `TCPClientInterface`, `RNodeInterface`, `WeaveInterface`, `I2PInterface`, `KISSInterface`). Coordinates are public and are displayed only when present in the announcement. Do not enable `discovery_encrypt` unless the receivers have the required network identity. Avoid `publish_ifac` unless you intentionally want to publish IFAC details.


## 📊 Main APIs

```ini
GET  /                            GET  /chat
GET  /api/status                  GET  /api/identities
GET  /api/aspects                 GET  /api/peers?aspect=<aspect>
GET  /api/map/peers
POST /api/identity/select         POST /api/exit
GET  /api/conversation/<hash>     POST /api/send
POST /api/command                 POST /api/telemetry/request
GET  /api/telemetry/config        POST /api/telemetry/config
GET  /api/telemetry/history/<h>   POST /api/telemetry/test
GET  /api/peer/<hash>/telemetry/last
GET  /api/raw/history             GET  /api/raw/history/<hash>
GET  /api/raw/<hash>
GET  /api/config                  POST /api/config
GET  /api/reticulum/interfaces    POST /api/reticulum/interface
GET  /api/rns/status
POST /api/propagation
POST /api/sync                    POST /api/announce
POST /api/peers/import            POST /api/cleanup_temp
GET  /api/attachment/<hash>       POST /api/shutdown
GET  /api/favorites               POST /api/favorites/add
POST /api/favorites/remove        POST /api/peer/update
POST /api/peer/<hash>/favorite    POST /api/peer/<hash>/groups
POST /api/groups/reset
GET  /api/announce/stats
GET  /api/telemetry/stats
```

Socket.IO events: `connect`, `disconnect`, `ping_server` (client → server); `connected`, `pong`, `peer_update`, `progress_update`, `new_message`, `delivery_update`, `sync_complete`, `identity_loaded` (server → client).

## 📦 Standalone build

```bash
cd RNS-Manager
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r messenger/requirements.txt pyinstaller

cd messenger
./build_rns_messenger.sh
# output: ../dist/rns_messenger_v<version>.run
```

The build runs in a clean virtualenv and includes `RNS`, `LXMF`, `LXST`, `numpy`
and `pycodec2` (with its bundled `libcodec2`). The messenger uses the
`~/.reticulum` configuration of the user. Expected size is roughly 35-45 MB.
Attach `.run` files to a GitHub **Release** (2 GB per asset limit); do not commit
them to the repository (100 MB per file limit).

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

