# Плагин rangetest для Sideband

[English](README.md) | **Русский** | [Italiano](README.it.md)

Этот command plugin добавляет в Sideband команду LXMF:

```text
rangetest <interval> start
rangetest 0 stop
```

Плагин периодически запрашивает телеметрию у пира, отправившего команду.
Sideband сохраняет ответы в своей базе данных; при остановке плагин
экспортирует точки с координатами в GPX и KML.

## Установка

1. В Sideband откройте `Settings` → `Plugins`.
2. Включите **Enable Plugins**.
3. Включите **Enable Command Plugins**.
4. Выберите папку `sideband_plugins/` репозитория как **Plugins Directory**.

   Либо скопируйте `rangetest.py` в отдельную папку, например:

   ```bash
   mkdir -p ~/.config/sideband/plugins
   cp sideband_plugins/rangetest.py ~/.config/sideband/plugins/
   ```

   и выберите `~/.config/sideband/plugins`.

5. Перезапустите Sideband.
6. Проверьте логи:

   ```text
   Rangetest command plugin started
   Registered ... as handler for command "rangetest"
   ```

## Использование

В режиме Sideband **Send command or request**:

```text
rangetest 15 start
```

Остановка:

```text
rangetest 0 stop
```

Интервал ограничивается диапазоном от 5 до 3600 секунд.

Файлы создаются в:

```text
~/.config/sideband/app_storage/rangetest/
```

с именами:

```text
rangetest_<peer>_<timestamp>.gpx
rangetest_<peer>_<timestamp>.kml
```

## Разрешения

Чтобы Sideband принял команду `rangetest`, он должен разрешить запросы от
отправителя. Проверьте настройки контакта или trusted peer в Sideband.

Чтобы получать ответы телеметрии, удалённый пир должен разрешать запросы
телеметрии:

- `allow_telemetry_requests = true` в RNS Messenger, или
- `allow_telemetry` для этого пира.

## Ограничения

- Экспортируются только точки с корректными координатами.
- Sideband не хранит RSSI/SNR/Q в той же записи приёма; плагин экспортирует
  доступную телеметрию, а не радиометрики пакета.
- Плагин использует API Sideband `request_latest_telemetry()` и
  `peer_telemetry()`, доступные в Sideband 1.5.0.
