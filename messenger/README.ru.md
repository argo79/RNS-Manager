# 💬 RNS Messenger

[English](README.md) | **Русский**

Веб-мессенджер для **LXMF** (Lightweight Extensible Message Format) поверх Reticulum, с вложениями, голосовыми сообщениями, телеметрией, картой и удалёнными командами.

Это отдельный проект от [Manager](../manager/README.ru.md): единственный общий ресурс — каталог данных `~/.rns_manager/` (идентичности, конфигурация, загрузки).

![LXMF Chat](img/lxmf-chat.jpg)

## ✨ Возможности

- 💬 Личные чаты LXMF с сохраняемой историей переписки
- 👥 Пиры с группами/доверием (избранное, дом, работа, семья, друзья, сервер, бот, мониторинг, исследование, публичный)
- 📎 Вложения: файлы, изображения и аудио
- 🎙️ Запись Opus в браузере и поддержка **Codec2** (450–3200 бит/с) через `pycodec2`
- 📊 Телеметрия: система, батарея, давление, температура, влажность, магнитное поле, местоположение, физическое соединение
- 🗺️ Карта Leaflet с позициями пиров и историей телеметрии
- 🎮 Удалённые команды LXMF: телеметрия, ping, echo, signal report, rangetest
- 📡 Узлы распространения: настройка и синхронизация сообщений
- 📣 Отправка анонсов и импорт пиров из кэша монитора
- ⚡ Обновления в реальном времени через **Socket.IO** (прогресс отправки, входящие сообщения)
- 🗃️ Просмотр и скачивание архива исходных сообщений

## 🚀 Установка

### Требования
- Python 3.9+
- Reticulum (`rns`), `lxmf`, `lxst`
- Существующая идентичность RNS (созданная/импортированная в Manager в `~/.rns_manager/storage/`)

### Зависимости

```bash
cd RNS-Manager/messenger
pip install -r requirements.txt
```

Дополнительно:
- `ffmpeg` в `PATH` для конвертации аудио OGG/Opus
- `pycodec2` + libcodec2 для сообщений Codec2
- `gps` / `gpsd` для телеметрии местоположения из GPSD

### Запуск

```bash
./start.sh
# или
python3 lxmf_chat.py -p 5012
```

С определённой идентичностью:

```bash
python3 lxmf_chat.py -i ~/.rns_manager/storage/<имя_идентичности> -p 5012
```

Затем откройте <http://127.0.0.1:5012/> (алиас: `/chat`).

> По умолчанию сервер слушает `127.0.0.1`.
> Чтобы открыть доступ в локальной сети: `python3 lxmf_chat.py --host 0.0.0.0 -p 5012`.

## 📁 Структура

```ini
messenger/
├── lxmf_chat.py          # Веб-сервер Flask + Socket.IO (точка входа)
├── messenger.py          # Ядро LXMF: идентичность, отправка/приём, телеметрия, команды
├── audio_codec.py        # Codec2 / Opus (pycodec2 + LXST)
├── telemetry_provider.py # Системные датчики и геолокация
├── core/
│   └── telemeter.py      # Поля FIELD_TELEMETRY (датчики LXMF)
├── templates/
│   └── lxmf_chat.html    # Веб-интерфейс
├── static/               # CSS, favicon, кодировщик Opus/WASM
├── version.py
├── requirements.txt
├── rns_messenger.spec    # Спецификация PyInstaller
└── build_rns_messenger.sh
```

## 🗂️ Данные, общие с Manager

```ini
~/.rns_manager/storage/          # идентичности и базы пиров/переписок
~/.rns_manager/config.json       # конфигурация (узел распространения, GPSD и т.д.)
~/.rns_manager/uploads/          # загруженные файлы
~/.rns_manager/Cache/announces.db
```

Идентичности, импортированные через Manager, доступны в мессенджере, и наоборот.

## 🎮 Использование

1. Выберите идентичность из списка (или запустите с `-i`).
2. Выберите пира и отправьте текст, файлы, изображения или аудио.
3. Откройте панель телеметрии, чтобы запросить данные или отслеживать их на карте.
4. Используйте панель команд для ping/echo/signal report/rangetest.
5. Настройте узел распространения, если пиры недоступны напрямую.

## 📊 Основные API

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

События Socket.IO: `connect`, `disconnect`, `ping_server` (клиент → сервер); `progress_update`, `message`, `identity_loaded` (сервер → клиент).

## 📦 Сборка автономного файла

```bash
cd messenger
./build_rns_messenger.sh
# результат: dist/rns_messenger_v<версия>.run
```

## 🔧 Устранение неполадок

- **`No module named 'flask_socketio'`** → `pip install -r requirements.txt`
- **`No module named 'gpsdclient'`** → `pip install gpsdclient`
- **Нет доступных идентичностей** → импортируйте/создайте идентичность в [Manager](../manager/README.ru.md) или скопируйте её в `~/.rns_manager/storage/`
- **Аудио не конвертируется** → установите `ffmpeg`; для Codec2 установите `pycodec2` с libcodec2
- **Карта пуста** → запросите телеметрию у пира, который передаёт местоположение

## 🔗 Связанные проекты

- **RNS Manager & Monitor** ([`../manager/`](../manager/)): управление идентичностями и монитор анонсов на порту **5010**.

## 📝 Лицензия

Распространяется по лицензии MIT. См. [`../LICENSE`](../LICENSE).

## 📧 Контакты

- Email: <arg0netds@gmail.com>
- GitHub: <https://github.com/argo79/RNS-Manager>
- RNS Identity: `04511923b68ae34e0fda5721d82f596f`

