# core/telemeter.py
import RNS
from RNS.vendor import umsgpack
import time
import struct

class Commands:
    PLUGIN_COMMAND    = 0x00
    TELEMETRY_REQUEST = 0x01
    PING              = 0x02
    ECHO              = 0x03
    SIGNAL_REPORT     = 0x04

class Telemeter:
    @staticmethod
    def from_packed(packed):
        try:
            p = umsgpack.unpackb(packed)
            t = Telemeter(from_packed=True)
            for sid in p:
                if sid in t.sids:
                    s = t.sids[sid]()
                    s.data = s.unpack(p[sid])
                    s.synthesized = True
                    s.active = True
                    s._telemeter = t
                    for name, sid_val in t.available.items():
                        if sid_val == sid:
                            t.sensors[name] = s
                            break
            return t
        except Exception as e:
            RNS.log(f"Telemeter unpack error: {e}", RNS.LOG_ERROR)
            return None

    def __init__(self, from_packed=False):
        self.sids = {
            Sensor.SID_TIME: Time,
            Sensor.SID_RECEIVED: Received,
            Sensor.SID_INFORMATION: Information,
            Sensor.SID_BATTERY: Battery,
            Sensor.SID_PRESSURE: Pressure,
            Sensor.SID_LOCATION: Location,
            Sensor.SID_PHYSICAL_LINK: PhysicalLink,
            Sensor.SID_TEMPERATURE: Temperature,
            Sensor.SID_HUMIDITY: Humidity,
            Sensor.SID_MAGNETIC_FIELD: MagneticField,
        }
        self.available = {
            "time": Sensor.SID_TIME,
            "received": Sensor.SID_RECEIVED,
            "information": Sensor.SID_INFORMATION,
            "battery": Sensor.SID_BATTERY,
            "pressure": Sensor.SID_PRESSURE,
            "location": Sensor.SID_LOCATION,
            "physical_link": Sensor.SID_PHYSICAL_LINK,
            "temperature": Sensor.SID_TEMPERATURE,
            "humidity": Sensor.SID_HUMIDITY,
            "magnetic_field": Sensor.SID_MAGNETIC_FIELD,
        }
        self.names = {v: k for k, v in self.available.items()}
        self.from_packed = from_packed
        self.sensors = {}
        if not self.from_packed:
            self.enable("time")

    def get_name(self, sid):
        return self.names.get(sid, None)

    def enable(self, sensor):
        if not self.from_packed and sensor in self.available:
            if sensor not in self.sensors:
                self.sensors[sensor] = self.sids[self.available[sensor]]()
                self.sensors[sensor]._telemeter = self
            self.sensors[sensor].active = True

    def disable(self, sensor):
        if not self.from_packed and sensor in self.sensors:
            self.sensors[sensor].active = False
            del self.sensors[sensor]

    def read(self, sensor):
        if sensor in self.sensors and self.sensors[sensor].active:
            return self.sensors[sensor].data
        return None

    def read_all(self):
        readings = {}
        for name, sensor in self.sensors.items():
            if sensor.active:
                readings[name] = sensor.data
        return readings

    def packed(self):
        packed = {}
        packed[Sensor.SID_TIME] = int(time.time())
        for name, sensor in self.sensors.items():
            if sensor.active:
                packed[sensor.sid] = sensor.pack()
        return umsgpack.packb(packed)

class Sensor:
    SID_NONE              = 0x00
    SID_TIME              = 0x01
    SID_LOCATION          = 0x02
    SID_PRESSURE          = 0x03
    SID_BATTERY           = 0x04
    SID_PHYSICAL_LINK     = 0x05
    SID_TEMPERATURE       = 0x07
    SID_HUMIDITY          = 0x08
    SID_MAGNETIC_FIELD    = 0x09
    SID_INFORMATION       = 0x0F
    SID_RECEIVED          = 0x10

    def __init__(self, sid=None):
        self._telemeter = None
        self._sid = sid or Sensor.SID_NONE
        self._data = None
        self.active = False
        self.synthesized = False

    @property
    def sid(self):
        return self._sid

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, value):
        self._data = value

    def pack(self):
        return self.data

    def unpack(self, packed):
        return packed

    def name(self):
        return self._telemeter.get_name(self._sid) if self._telemeter else None

# Sensori specifici

class Time(Sensor):
    SID = Sensor.SID_TIME
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        return self.data["utc"] if self.data else None
    def unpack(self, packed):
        return {"utc": packed} if packed is not None else None

class Information(Sensor):
    SID = Sensor.SID_INFORMATION
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        return self.data["contents"] if self.data else None
    def unpack(self, packed):
        return {"contents": packed} if packed is not None else None

class Received(Sensor):
    SID = Sensor.SID_RECEIVED
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        if self.data:
            return [
                self.data["by"],
                self.data["via"],
                self.data["distance"]["geodesic"],
                self.data["distance"]["euclidian"]
            ]
        return None
    def unpack(self, packed):
        if packed:
            return {
                "by": packed[0],
                "via": packed[1],
                "distance": {
                    "geodesic": packed[2],
                    "euclidian": packed[3]
                }
            }
        return None

class Battery(Sensor):
    SID = Sensor.SID_BATTERY
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        if self.data:
            return [
                round(self.data["charge_percent"], 1),
                self.data["charging"],
                self.data.get("temperature")
            ]
        return None
    def unpack(self, packed):
        if packed:
            return {
                "charge_percent": round(packed[0], 1),
                "charging": packed[1],
                "temperature": packed[2] if len(packed) > 2 else None
            }
        return None

class Pressure(Sensor):
    SID = Sensor.SID_PRESSURE
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        return self.data["mbar"] if self.data else None
    def unpack(self, packed):
        return {"mbar": packed} if packed is not None else None

class Location(Sensor):
    SID = Sensor.SID_LOCATION
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        if self.data:
            try:
                return [
                    struct.pack("!i", int(round(self.data["latitude"], 6)*1e6)),
                    struct.pack("!i", int(round(self.data["longitude"], 6)*1e6)),
                    struct.pack("!i", int(round(self.data["altitude"], 2)*1e2)),
                    struct.pack("!I", int(round(self.data["speed"], 2)*1e2)),
                    struct.pack("!i", int(round(self.data["bearing"], 2)*1e2)),
                    struct.pack("!H", int(round(self.data["accuracy"], 2)*1e2)),
                    self.data["last_update"],
                ]
            except:
                return None
        return None
    def unpack(self, packed):
        if packed and len(packed) >= 7:
            try:
                return {
                    "latitude": struct.unpack("!i", packed[0])[0]/1e6,
                    "longitude": struct.unpack("!i", packed[1])[0]/1e6,
                    "altitude": struct.unpack("!i", packed[2])[0]/1e2,
                    "speed": struct.unpack("!I", packed[3])[0]/1e2,
                    "bearing": struct.unpack("!i", packed[4])[0]/1e2,
                    "accuracy": struct.unpack("!H", packed[5])[0]/1e2,
                    "last_update": packed[6],
                }
            except:
                return None
        return None

class PhysicalLink(Sensor):
    SID = Sensor.SID_PHYSICAL_LINK
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        if self.data:
            return [self.data["rssi"], self.data["snr"], self.data["q"]]
        return None
    def unpack(self, packed):
        if packed:
            return {"rssi": packed[0], "snr": packed[1], "q": packed[2]}
        return None

class Temperature(Sensor):
    SID = Sensor.SID_TEMPERATURE
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        return self.data["c"] if self.data else None
    def unpack(self, packed):
        return {"c": packed} if packed is not None else None

class Humidity(Sensor):
    SID = Sensor.SID_HUMIDITY
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        return self.data["percent_relative"] if self.data else None
    def unpack(self, packed):
        return {"percent_relative": packed} if packed is not None else None

class MagneticField(Sensor):
    SID = Sensor.SID_MAGNETIC_FIELD
    def __init__(self):
        super().__init__(type(self).SID)
    def pack(self):
        if self.data:
            return [self.data["x"], self.data["y"], self.data["z"]]
        return None
    def unpack(self, packed):
        if packed:
            return {"x": packed[0], "y": packed[1], "z": packed[2]}
        return None