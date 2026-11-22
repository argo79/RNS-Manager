#!/usr/bin/env python3
"""Sideband command plugin: LXMF rangetest.

Command syntax:
    rangetest <interval> start
    rangetest 0 stop

The plugin periodically requests telemetry from the peer that sent the
command. Sideband stores incoming telemetry in its own database. On stop,
the collected points are exported as GPX and KML next to sideband.db.

Installation:
1. Select this directory as Sideband's "Plugins Directory".
2. Enable "Enable Plugins" and "Enable Command Plugins" in Sideband.
3. Restart Sideband.
"""

import os
import threading
import time
import xml.etree.ElementTree as ET

import RNS


class RangetestCommandPlugin(SidebandCommandPlugin):
    command_name = "rangetest"

    def __init__(self, sideband_core):
        super().__init__(sideband_core)
        self._lock = threading.Lock()
        self._tests = {}
        self._stopping = False

    def start(self):
        self._stopping = False
        RNS.log("Rangetest command plugin started", RNS.LOG_NOTICE)
        super().start()

    def stop(self):
        with self._lock:
            self._stopping = True
            peers = list(self._tests.keys())
        for peer_hash in peers:
            try:
                self._stop(peer_hash)
            except Exception as e:
                RNS.log(f"Rangetest: errore stop {peer_hash.hex()[:16]}: {e}", RNS.LOG_ERROR)
        super().stop()

    def handle_command(self, arguments, lxm):
        try:
            if not arguments or len(arguments) < 2:
                self._reply(lxm, "Uso: rangetest <intervallo> start|stop")
                return

            try:
                interval = int(str(arguments[0]).strip())
            except (TypeError, ValueError):
                self._reply(lxm, "❌ Intervallo rangetest non valido")
                return

            action = str(arguments[1]).strip().lower()
            peer_hash = lxm.source_hash

            if action == "start":
                interval = max(5, min(interval, 3600))
                self._start(peer_hash, interval)
                self._reply(lxm, f"✅ Rangetest avviato ogni {interval} secondi")
            elif action == "stop":
                exported = self._stop(peer_hash)
                if exported:
                    self._reply(lxm, f"✅ Rangetest fermato; esportati {exported} punti")
                else:
                    self._reply(lxm, "✅ Rangetest fermato; nessun punto da esportare")
            else:
                self._reply(lxm, "❌ Azione rangetest non valida")
        except Exception as e:
            RNS.log(f"Rangetest: errore comando: {e}", RNS.LOG_ERROR)
            RNS.trace_exception(e)
            try:
                self._reply(lxm, "❌ Errore interno del rangetest")
            except Exception:
                pass

    def _reply(self, lxm, text):
        try:
            self.get_sideband().send_message(
                text,
                lxm.source_hash,
                False,
                skip_fields=True,
                no_display=True,
                attachment=None,
                image=None,
                audio=None,
            )
        except Exception as e:
            RNS.log(f"Rangetest: impossibile inviare risposta: {e}", RNS.LOG_ERROR)

    def _start(self, peer_hash, interval):
        with self._lock:
            old = self._tests.pop(peer_hash, None)
            if old and old.get("timer"):
                old["timer"].cancel()

            state = {
                "interval": interval,
                "start_time": time.time(),
                "last_request": None,
                "timer": None,
            }
            self._tests[peer_hash] = state
            timer = threading.Timer(interval, self._run_request, args=(peer_hash,))
            timer.daemon = True
            state["timer"] = timer
            timer.start()

        RNS.log(
            f"Rangetest: avviato verso {peer_hash.hex()[:16]} ogni {interval}s",
            RNS.LOG_NOTICE,
        )

    def _run_request(self, peer_hash):
        with self._lock:
            if self._stopping:
                return
            state = self._tests.get(peer_hash)
            if not state:
                return
            state["last_request"] = time.time()

        try:
            result = self.get_sideband().request_latest_telemetry(
                from_addr=peer_hash,
                is_livetrack=True,
            )
            RNS.log(
                f"Rangetest: richiesta telemetria a {peer_hash.hex()[:16]}: {result}",
                RNS.LOG_DEBUG,
            )
        except Exception as e:
            RNS.log(f"Rangetest: errore richiesta telemetria: {e}", RNS.LOG_ERROR)

        with self._lock:
            if self._stopping:
                return
            state = self._tests.get(peer_hash)
            if not state:
                return
            timer = threading.Timer(state["interval"], self._run_request, args=(peer_hash,))
            timer.daemon = True
            state["timer"] = timer
            timer.start()

    def _stop(self, peer_hash):
        with self._lock:
            state = self._tests.pop(peer_hash, None)
            if state and state.get("timer"):
                state["timer"].cancel()

        if not state:
            return 0

        try:
            return self._export(peer_hash, state["start_time"])
        except Exception as e:
            RNS.log(f"Rangetest: errore esportazione: {e}", RNS.LOG_ERROR)
            RNS.trace_exception(e)
            return 0


    def _export(self, peer_hash, after):
        try:
            from sbapp.sideband.sense import Telemeter
        except Exception:
            try:
                from sideband.sense import Telemeter
            except Exception as e:
                RNS.log(f"Rangetest: Telemeter non disponibile: {e}", RNS.LOG_ERROR)
                return 0

        sideband = self.get_sideband()
        raw_points = sideband.peer_telemetry(peer_hash, after=after) or []
        points = []

        for timestamp, packed in reversed(raw_points):
            try:
                telemeter = Telemeter.from_packed(packed)
                data = telemeter.read_all() if telemeter else None
            except Exception:
                continue
            if not data:
                continue

            location = data.get("location") or {}
            latitude = location.get("latitude")
            longitude = location.get("longitude")
            if latitude is None or longitude is None:
                continue

            points.append({
                "timestamp": timestamp,
                "latitude": float(latitude),
                "longitude": float(longitude),
                "altitude": float(location.get("altitude") or 0.0),
                "speed": float(location.get("speed") or 0.0),
                "bearing": float(location.get("bearing") or 0.0),
                "battery": data.get("battery"),
                "temperature": data.get("temperature"),
                "humidity": data.get("humidity"),
                "pressure": data.get("pressure"),
            })

        if not points:
            return 0

        base_dir = os.path.dirname(getattr(sideband, "db_path", "") or "")
        if not base_dir:
            base_dir = os.path.expanduser("~/.config/sideband/app_storage")
        output_dir = os.path.join(base_dir, "rangetest")
        os.makedirs(output_dir, exist_ok=True)

        peer_short = peer_hash.hex()[:8]
        stamp = int(time.time())
        gpx_path = os.path.join(output_dir, f"rangetest_{peer_short}_{stamp}.gpx")
        kml_path = os.path.join(output_dir, f"rangetest_{peer_short}_{stamp}.kml")

        self._write_gpx(points, gpx_path, peer_short)
        self._write_kml(points, kml_path, peer_short)
        RNS.log(
            f"Rangetest: esportati {len(points)} punti in {output_dir}",
            RNS.LOG_NOTICE,
        )
        return len(points)

    @staticmethod
    def _timestamp(ts):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


    def _write_gpx(self, points, path, peer_short):
        gpx = ET.Element("gpx", {
            "version": "1.1",
            "creator": "Sideband rangetest plugin",
            "xmlns": "http://www.topografix.com/GPX/1/1",
        })
        trk = ET.SubElement(gpx, "trk")
        ET.SubElement(trk, "name").text = f"Rangetest {peer_short}"
        seg = ET.SubElement(trk, "trkseg")
        for point in points:
            trkpt = ET.SubElement(seg, "trkpt", {
                "lat": f"{point['latitude']:.8f}",
                "lon": f"{point['longitude']:.8f}",
            })
            ET.SubElement(trkpt, "ele").text = f"{point['altitude']:.2f}"
            ET.SubElement(trkpt, "time").text = self._timestamp(point["timestamp"])
        tree = ET.ElementTree(gpx)
        tree.write(path, encoding="utf-8", xml_declaration=True)

    def _write_kml(self, points, path, peer_short):
        kml = ET.Element("kml", {"xmlns": "http://www.opengis.net/kml/2.2"})
        document = ET.SubElement(kml, "Document")
        ET.SubElement(document, "name").text = f"Rangetest {peer_short}"
        placemark = ET.SubElement(document, "Placemark")
        ET.SubElement(placemark, "name").text = f"Rangetest {peer_short}"
        line = ET.SubElement(placemark, "LineString")
        ET.SubElement(line, "tessellate").text = "1"
        coordinates = " ".join(
            f"{point['longitude']:.8f},{point['latitude']:.8f},{point['altitude']:.2f}"
            for point in points
        )
        ET.SubElement(line, "coordinates").text = coordinates
        tree = ET.ElementTree(kml)
        tree.write(path, encoding="utf-8", xml_declaration=True)


plugin_class = RangetestCommandPlugin

