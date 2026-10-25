"""Recordings → MCAP, the log format Foxglove and ROS 2 (rosbag2) open directly.

    allsense mcap allsense_phone_20261017-191502.zip              # → .mcap next to it, for Foxglove
    allsense mcap allsense_phone_20261017-191502.zip --ros2       # ROS 2 messages: ros2 bag play / info

    allsense.export_mcap(rec_or_path, "out.mcap", profile="foxglove" | "ros2")

Topics are the ROS bridge's: ``/allsense/<device>/<category>/<name>``. Messages are stamped with the
sample's time (the phone's boot clock moved to Unix time with the recording's ``boot_to_unix_ns``).

- **foxglove** (JSON): one message per sample with its fields by name (``x``, ``latitude``…; spaces
  become ``_``), so Foxglove's Plot panel reads them directly. GNSS position also goes to
  ``…/position/fix`` as ``foxglove.LocationFix``, for the Map panel.
- **ros2** (CDR): the bridge's standard messages (``sensor_msgs/Imu``, ``NavSatFix``, ``BatteryState``…;
  docs in otterbridge-allsense-ros2), anything else ``std_msgs/Float64MultiArray``.

Needs ``pip install allsense[mcap]`` (the ``mcap`` and ``mcap-ros2-support`` packages).
"""

from __future__ import annotations

import json
import math
import os
from typing import Callable, Dict, Optional, Sequence, Tuple, Union

from .recording import RecordedStream, Recording, load_recording

PROFILES = ("foxglove", "ros2")


def export_mcap(recording: Union[Recording, str], out: Optional[str] = None, profile: str = "foxglove") -> str:
    """Writes a recording (or the path of one) as MCAP; returns the file written. ``out`` defaults
    to the recording's path with ``.mcap`` (``_ros2.mcap`` for the ros2 profile)."""
    if profile not in PROFILES:
        raise ValueError(f"profile is one of {PROFILES}, not {profile!r}")
    if isinstance(recording, str):
        if out is None:
            out = os.path.splitext(recording.rstrip("/\\"))[0] + ("_ros2.mcap" if profile == "ros2" else ".mcap")
        recording = load_recording(recording)
    if out is None:
        raise ValueError("Say where to write the .mcap (out=…)")
    messages = _ros2_messages if profile == "ros2" else _foxglove_messages
    with open(out, "wb") as f:
        messages(recording, f)
    return out


# ---- shared ---------------------------------------------------------------------------------------

def _boot_to_unix_ns(rec: Recording) -> int:
    return int(rec.meta.get("boot_to_unix_ns") or 0)


def _topic(rec: Recording, s: RecordedStream) -> str:
    return f"/allsense/{rec.device or 'phone'}/{s.meta.get('category') or 'sensors'}/{s.name}"


def _kind(s: RecordedStream) -> str:
    """``accelerometer``, ``gnss.position``…: the stream type without its ``android.sensor.`` / ``allsense.`` prefix."""
    t = s.meta.get("stream_type") or ""
    for prefix in ("android.sensor.", "allsense."):
        if t.startswith(prefix):
            return t[len(prefix):]
    return t


def _samples(rec: Recording):
    """Every sample of every stream in time order: (unix_ns, stream, values)."""
    offset = _boot_to_unix_ns(rec)
    rows = [(t + offset, i, s, v) for i, s in enumerate(rec) for t, v in zip(s.t_ns, s.values)]
    rows.sort(key=lambda r: (r[0], r[1]))
    for unix_ns, _, s, values in rows:
        yield unix_ns, s, values


def _get(values: Sequence[Optional[float]], i: int) -> Optional[float]:
    v = values[i] if i < len(values) else None
    return v if v is not None and math.isfinite(v) else None


def _stamp(unix_ns: int) -> Tuple[int, int]:
    return unix_ns // 1_000_000_000, unix_ns % 1_000_000_000


# ---- Foxglove (JSON) ------------------------------------------------------------------------------

def _key(field: str) -> str:
    return "_".join(field.split()) or "value"


def _foxglove_messages(rec: Recording, f) -> None:
    from mcap.writer import Writer

    w = Writer(f)
    w.start(profile="", library="allsense")
    channels: Dict[str, int] = {}
    fixes: Dict[str, int] = {}
    for s in rec:
        keys = [_key(x) for x in s.fields]
        props = {"timestamp": _TIME_SCHEMA, "t_ns": {"type": "integer", "description": "phone boot clock, ns"}}
        for k, u in zip(keys, s.units or [""] * len(keys)):
            props[k] = {"type": ["number", "null"], "description": u}
        schema = w.register_schema(f"allsense.{_kind(s) or 'stream'}", "jsonschema",
                                   json.dumps({"type": "object", "title": s.meta.get("type") or s.name, "properties": props}).encode())
        channels[s.name] = w.register_channel(_topic(rec, s), "json", schema)
        if _kind(s) == "gnss.position":
            fix_schema = w.register_schema("foxglove.LocationFix", "jsonschema", json.dumps(_LOCATION_FIX_SCHEMA).encode())
            fixes[s.name] = w.register_channel(_topic(rec, s) + "/fix", "json", fix_schema)
    offset = _boot_to_unix_ns(rec)
    for unix_ns, s, values in _samples(rec):
        sec, nsec = _stamp(unix_ns)
        msg = {"timestamp": {"sec": sec, "nsec": nsec}, "t_ns": unix_ns - offset}
        for i, field in enumerate(s.fields):
            msg[_key(field)] = _get(values, i)
        w.add_message(channels[s.name], log_time=unix_ns, data=json.dumps(msg).encode(), publish_time=unix_ns)
        if s.name in fixes and _get(values, 0) is not None and _get(values, 1) is not None:
            fix = {"timestamp": {"sec": sec, "nsec": nsec}, "frame_id": f"{rec.device}_gnss",
                   "latitude": values[0], "longitude": values[1], "altitude": _get(values, 2) or 0.0}
            acc, vacc = _get(values, 3), _get(values, 6)
            if acc is not None:
                v = vacc if vacc is not None else acc * 1.5
                fix["position_covariance"] = [acc ** 2, 0, 0, 0, acc ** 2, 0, 0, 0, v ** 2]
                fix["position_covariance_type"] = 1  # approximated
            else:
                fix["position_covariance"] = [0.0] * 9
                fix["position_covariance_type"] = 0
            w.add_message(fixes[s.name], log_time=unix_ns, data=json.dumps(fix).encode(), publish_time=unix_ns)
    w.finish()


_TIME_SCHEMA = {"type": "object", "properties": {"sec": {"type": "integer"}, "nsec": {"type": "integer"}}}
_LOCATION_FIX_SCHEMA = {
    "title": "foxglove.LocationFix", "type": "object",
    "properties": {
        "timestamp": _TIME_SCHEMA, "frame_id": {"type": "string"},
        "latitude": {"type": "number"}, "longitude": {"type": "number"}, "altitude": {"type": "number"},
        "position_covariance": {"type": "array", "items": {"type": "number"}, "minItems": 9, "maxItems": 9},
        "position_covariance_type": {"type": "integer"},
    },
}


# ---- ROS 2 (CDR) ----------------------------------------------------------------------------------
# The same conversions as the ROS 2 bridge's convert.py (otterbridge-allsense-ros2), as plain dicts (no ROS needed).

_NOT_PROVIDED = [-1.0] + [0.0] * 8
_NAN = float("nan")


def _f(v: Optional[float]) -> float:
    return _NAN if v is None else float(v)


def _header(unix_ns: int, frame_id: str) -> dict:
    sec, nsec = _stamp(unix_ns)
    return {"stamp": {"sec": sec, "nanosec": nsec}, "frame_id": frame_id}


def _vec(values, scale: float = 1.0) -> dict:
    x, y, z = (_f(values[i] if i < len(values) else None) * scale for i in range(3))
    return {"x": x, "y": y, "z": z}


def _imu(h: dict, **parts) -> dict:
    msg = {"header": h, "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0}, "orientation_covariance": list(_NOT_PROVIDED),
           "angular_velocity": {"x": 0.0, "y": 0.0, "z": 0.0}, "angular_velocity_covariance": list(_NOT_PROVIDED),
           "linear_acceleration": {"x": 0.0, "y": 0.0, "z": 0.0}, "linear_acceleration_covariance": list(_NOT_PROVIDED)}
    for k, v in parts.items():
        msg[k] = v
        msg[k + "_covariance"] = [0.0] * 9  # provided, covariance unknown
    return msg


def _quaternion(values) -> dict:
    x, y, z = (_f(values[i] if i < len(values) else None) for i in range(3))
    w = values[3] if len(values) > 3 and values[3] is not None else math.sqrt(max(0.0, 1.0 - x * x - y * y - z * z))
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    return {"x": x / n, "y": y / n, "z": z / n, "w": w / n}


def _rotation(h, values, kind):
    msg = _imu(h, orientation=_quaternion(values))
    heading = _get(values, 4)
    if kind == "rotation_vector" and heading is not None and heading >= 0:
        msg["orientation_covariance"][8] = heading ** 2
    return msg


def _proximity(h, values, s):
    max_range = (s.meta.get("max_range") or 5.0) / 100.0
    return {"header": h, "radiation_type": 1, "field_of_view": 0.5, "min_range": 0.0, "max_range": max_range,
            "range": _f(_get(values, 0)) / 100.0}


def _nav_sat_fix(h, values, s):
    lat, lon, alt, acc, vacc = (_get(values, i) for i in (0, 1, 2, 3, 6))
    msg = {"header": h, "status": {"status": 0 if lat is not None and lon is not None else -1, "service": 1},
           "latitude": _f(lat), "longitude": _f(lon), "altitude": _f(alt),
           "position_covariance": [0.0] * 9, "position_covariance_type": 0}
    if acc is not None:
        v = vacc if vacc is not None else acc * 1.5
        msg["position_covariance"] = [acc ** 2, 0.0, 0.0, 0.0, acc ** 2, 0.0, 0.0, 0.0, v ** 2]
        msg["position_covariance_type"] = 1
    return msg


def _battery(h, values, s):
    level, volts, temp, ma, charging = (_get(values, i) for i in range(5))
    return {"header": h, "voltage": _f(volts), "temperature": _f(temp), "current": _f(ma) / 1000.0,
            "charge": _NAN, "capacity": _NAN, "design_capacity": _NAN, "percentage": _f(level) / 100.0,
            "power_supply_status": 1 if charging == 1.0 else 2 if charging == 0.0 else 0,
            "power_supply_health": 0, "power_supply_technology": 2, "present": True,
            "cell_voltage": [], "cell_temperature": [], "location": "", "serial_number": ""}


def _array(h, values, s):
    return {"layout": {"dim": [{"label": ",".join(s.fields) or "values", "size": len(values), "stride": len(values)}], "data_offset": 0},
            "data": [_f(v) for v in values]}


_ACCEL = {"accelerometer", "gravity", "linear_acceleration", "accelerometer_uncalibrated"}
_GYRO = {"gyroscope", "gyroscope_uncalibrated"}
_ROTATION = {"rotation_vector", "game_rotation_vector", "geomagnetic_rotation_vector"}
_MAGNETIC = {"magnetic_field", "magnetic_field_uncalibrated"}

#: stream kind → (ROS type, converter(header, values, stream))
_ROS2: Dict[str, Tuple[str, Callable]] = {
    **{k: ("sensor_msgs/msg/Imu", lambda h, v, s: _imu(h, linear_acceleration=_vec(v))) for k in _ACCEL},
    **{k: ("sensor_msgs/msg/Imu", lambda h, v, s: _imu(h, angular_velocity=_vec(v))) for k in _GYRO},
    **{k: ("sensor_msgs/msg/Imu", lambda h, v, s, k=k: _rotation(h, v, k)) for k in _ROTATION},
    **{k: ("sensor_msgs/msg/MagneticField", lambda h, v, s: {"header": h, "magnetic_field": _vec(v, 1e-6),
                                                             "magnetic_field_covariance": [0.0] * 9}) for k in _MAGNETIC},
    "pressure": ("sensor_msgs/msg/FluidPressure", lambda h, v, s: {"header": h, "fluid_pressure": _f(_get(v, 0)) * 100.0, "variance": 0.0}),
    "light": ("sensor_msgs/msg/Illuminance", lambda h, v, s: {"header": h, "illuminance": _f(_get(v, 0)), "variance": 0.0}),
    "ambient_temperature": ("sensor_msgs/msg/Temperature", lambda h, v, s: {"header": h, "temperature": _f(_get(v, 0)), "variance": 0.0}),
    "relative_humidity": ("sensor_msgs/msg/RelativeHumidity",
                          lambda h, v, s: {"header": h, "relative_humidity": _f(_get(v, 0)) / 100.0, "variance": 0.0}),
    "proximity": ("sensor_msgs/msg/Range", _proximity),
    "gnss.position": ("sensor_msgs/msg/NavSatFix", _nav_sat_fix),
    "battery": ("sensor_msgs/msg/BatteryState", _battery),
}
_FALLBACK = ("std_msgs/msg/Float64MultiArray", _array)


def ros2_type(s: RecordedStream) -> str:
    """The ROS 2 message type a recorded stream is exported as."""
    return _ROS2.get(_kind(s), _FALLBACK)[0]


def _ros2_messages(rec: Recording, f) -> None:
    from mcap_ros2.writer import Writer

    from ._ros2_msgdefs import MSGDEFS

    w = Writer(f)
    schemas: Dict[str, object] = {}
    plan = {}
    for s in rec:
        ros_type, convert = _ROS2.get(_kind(s), _FALLBACK)
        if ros_type not in schemas:
            schemas[ros_type] = w.register_msgdef(ros_type, MSGDEFS[ros_type])
        category = s.meta.get("category")
        frame = "base_link" if category == "robot" else f"{rec.device or 'phone'}_{'gnss' if _kind(s) == 'gnss.position' else 'sensors'}"
        plan[s.name] = (_topic(rec, s), schemas[ros_type], convert, frame)
    for unix_ns, s, values in _samples(rec):
        topic, schema, convert, frame = plan[s.name]
        w.write_message(topic, schema, convert(_header(unix_ns, frame), values, s), log_time=unix_ns, publish_time=unix_ns)
    w.finish()
