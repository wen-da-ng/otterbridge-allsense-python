import json
import math

import pytest

from allsense import export_mcap, load_recording
from allsense.cli import main
from allsense.mcap_export import ros2_type
from allsense.recording import _write_example

mcap_reader = pytest.importorskip("mcap.reader")
pytest.importorskip("mcap_ros2")

T0 = 1_760_700_000_000_000_000  # boot_to_unix_ns is 0 in the example, so t_ns is Unix time

STREAMS = [
    {"name": "accelerometer", "fields": ["x", "y", "z"], "units": ["m/s²"] * 3,
     "rows": [(T0, (0.1, 0.2, 9.8)), (T0 + 20_000_000, (0.0, None, 9.81))]},
    {"name": "position", "fields": ["latitude", "longitude", "altitude", "accuracy", "speed", "bearing", "vertical accuracy"],
     "units": ["°", "°", "m", "m", "m/s", "°", "m"],
     "rows": [(T0 + 10_000_000, (51.477928123456, -0.001545987654, 61.5, 4.0, 0.0, None, 6.0)),
              (T0 + 30_000_000, (None, None, None, None, None, None, None))]},
    {"name": "battery", "fields": ["level", "voltage", "temperature", "current", "charging"], "units": ["%", "V", "°C", "mA", ""],
     "rows": [(T0 + 5_000_000, (87.0, 4.012, 31.5, -412.0, 0.0))]},
    {"name": "mic_level", "fields": ["rms", "peak"], "units": ["dBFS", "dBFS"], "rows": [(T0 + 1, (-40.0, -20.0))]},
]
TYPES = {"accelerometer": ("android.sensor.accelerometer", "sensors"), "position": ("allsense.gnss.position", "gnss"),
         "battery": ("allsense.battery", "battery"), "mic_level": ("allsense.mic.level", "mic")}


@pytest.fixture
def recording(tmp_path):
    folder = tmp_path / "allsense_phone_20261017-191502"
    _write_example(str(folder), STREAMS)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    for s in meta["streams"]:  # the example writer leaves these out; the phone writes them
        s["stream_type"], s["category"] = TYPES[s["name"]]
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return str(folder)


def test_foxglove_profile_is_json_by_field_name(recording, tmp_path):
    out = export_mcap(recording, str(tmp_path / "out.mcap"))
    with open(out, "rb") as f:
        reader = mcap_reader.make_reader(f)
        msgs = [(c.topic, s.name, m.log_time, json.loads(m.data)) for s, c, m in reader.iter_messages(log_time_order=True)]
    topics = {t for t, *_ in msgs}
    assert topics == {"/allsense/phone/sensors/accelerometer", "/allsense/phone/gnss/position", "/allsense/phone/gnss/position/fix",
                      "/allsense/phone/battery/battery", "/allsense/phone/mic/mic_level"}
    acc = [m for t, _, _, m in msgs if t.endswith("accelerometer")]
    assert acc[0]["z"] == 9.8 and acc[1]["y"] is None and acc[1]["timestamp"] == {"sec": 1_760_700_000, "nsec": 20_000_000}
    pos = next(m for t, _, _, m in msgs if t.endswith("/position"))
    assert pos["vertical_accuracy"] == 6.0 and pos["latitude"] == 51.477928123456
    fixes = [(s, m) for t, s, _, m in msgs if t.endswith("/fix")]
    assert len(fixes) == 1  # the row without a fix isn't on the map
    assert fixes[0][0] == "foxglove.LocationFix" and fixes[0][1]["position_covariance"][8] == 36.0
    assert [lt for *_, lt, _ in msgs] == sorted(lt for *_, lt, _ in msgs)


def test_ros2_profile_decodes_as_standard_messages(recording, tmp_path):
    from mcap_ros2.decoder import DecoderFactory

    out = export_mcap(recording, str(tmp_path / "out_ros2.mcap"), profile="ros2")
    with open(out, "rb") as f:
        reader = mcap_reader.make_reader(f, decoder_factories=[DecoderFactory()])
        assert reader.get_header().profile == "ros2"
        got = {}
        for schema, channel, _, msg in reader.iter_decoded_messages():
            got.setdefault(channel.topic, []).append((schema.name, msg))
    name, imu = got["/allsense/phone/sensors/accelerometer"][0]
    assert name == "sensor_msgs/msg/Imu" and imu.linear_acceleration.z == 9.8
    assert imu.orientation_covariance[0] == -1.0 and imu.header.stamp.sec == 1_760_700_000
    assert math.isnan(got["/allsense/phone/sensors/accelerometer"][1][1].linear_acceleration.y)
    name, fix = got["/allsense/phone/gnss/position"][0]
    assert name == "sensor_msgs/msg/NavSatFix" and fix.latitude == 51.477928123456 and fix.position_covariance[0] == 16.0
    assert fix.status.status == 0 and got["/allsense/phone/gnss/position"][1][1].status.status == -1
    name, bat = got["/allsense/phone/battery/battery"][0]
    assert name == "sensor_msgs/msg/BatteryState" and abs(bat.percentage - 0.87) < 1e-6 and abs(bat.current + 0.412) < 1e-6
    name, level = got["/allsense/phone/mic/mic_level"][0]
    assert name == "std_msgs/msg/Float64MultiArray" and list(level.data) == [-40.0, -20.0] and level.layout.dim[0].label == "rms,peak"


def test_types_follow_the_ros_bridge(recording):
    rec = load_recording(recording)
    assert [ros2_type(s) for s in rec] == ["sensor_msgs/msg/Imu", "sensor_msgs/msg/NavSatFix", "sensor_msgs/msg/BatteryState",
                                           "std_msgs/msg/Float64MultiArray"]


def test_cli_writes_next_to_the_recording(recording, capsys):
    assert main(["mcap", recording]) == 0
    assert main(["mcap", recording, "--ros2"]) == 0
    out = capsys.readouterr().out
    assert recording + ".mcap" in out and recording + "_ros2.mcap" in out
