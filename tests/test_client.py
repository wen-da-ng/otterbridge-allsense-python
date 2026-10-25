import threading
import time

import pytest

import allsense
from allsense.client import _ws_url

from allsense_testkit import DEVICE, TOKEN, FakePhone


def test_url_forms():
    assert _ws_url("http://192.168.1.5:8765/?token=abc", None) == "ws://192.168.1.5:8765/ws?token=abc"
    assert _ws_url("192.168.1.5", "abc") == "ws://192.168.1.5:8765/ws?token=abc"
    assert _ws_url("localhost:8766", "abc") == "ws://localhost:8766/ws?token=abc"
    assert _ws_url("ws://10.0.0.2:9000/ws?token=xyz", None) == "ws://10.0.0.2:9000/ws?token=xyz"
    with pytest.raises(allsense.AllSenseError, match="token"):
        _ws_url("192.168.1.5", None)


def test_hello_and_catalog(phone):
    assert phone.device == DEVICE
    assert phone.protocol == 0
    assert [s.name for s in phone.streams()][:2] == ["accelerometer", "light"]
    assert len(phone.streams(all=True)) == len(phone.streams()) + 1
    accel = phone.find("accelerometer")
    assert accel.fields == ("x", "y", "z") and accel.unit == "m/s²" and accel.continuous
    assert accel.sensor_type == 1
    # Full topic and stable ID work too.
    assert phone.find(f"allsense.{DEVICE}.sensors.light").name == "light"
    assert phone.find("sensors/1/acme/accel/n/0").name == "accelerometer"


def test_unknown_name_suggests(phone):
    with pytest.raises(allsense.AllSenseError, match="accelerometer"):
        phone.find("acelerometer")


def test_subscribe_receives_samples_at_rate(phone, phone_server):
    with phone.subscribe("accelerometer", rate=100) as accel:
        first = accel.get(timeout=2)
        time.sleep(0.5)
        rest = accel.read()
    assert phone_server.subscribe_messages[-1]["rate_hz"] == 100
    assert first.fields == ("x", "y", "z") and len(first.values) == 3
    assert first["z"] == pytest.approx(9.81) and first[2] == first["z"]
    assert first.as_dict()["z"] == pytest.approx(9.81)
    assert 8 <= len(rest) <= 80  # ~50 in 0.5 s at 100 Hz; shared CI machines can deliver far fewer
    seqs = [first.seq] + [s.seq for s in rest]
    assert seqs == sorted(seqs)


def test_clock_sync_maps_phone_time_to_local_time(phone):
    with phone.subscribe("accelerometer", rate=50) as accel:
        time.sleep(1.3)  # let the initial sync burst complete
        sample = accel.read()[-1]
    assert phone.round_trip_ms is not None and phone.round_trip_ms < 100
    # The fake phone's clock is 1000 s ahead; synced sample times must still be "now" here.
    assert abs(sample.time - time.time()) < 0.2


def test_event_sensor_and_latest(phone):
    light = phone.subscribe("light")
    assert light.get(timeout=2).values[0] >= 0
    assert light.latest is not None
    light.close()


def test_refused_sensor_reports_status(phone):
    with phone.subscribe("locked") as locked:
        with pytest.raises(TimeoutError, match="won't deliver"):
            locked.get(timeout=0.5)
        assert "won't deliver" in locked.status


def test_not_shared_stream_reports_status(phone):
    assert not phone.find("heart_rate").shared
    assert phone.find("accelerometer").shared
    with phone.subscribe("heart_rate") as hr:
        with pytest.raises(TimeoutError, match="Not shared"):
            hr.get(timeout=0.5)
        assert "Streams it may send" in hr.status


def test_multiple_and_callback(phone):
    got = []
    subs = phone.subscribe(["accelerometer", "light"], rate=50, callback=got.append)
    time.sleep(0.4)
    for s in subs:
        s.close()
    assert {s.topic.rsplit(".", 1)[-1] for s in got} == {"accelerometer", "light"}


def test_slow_reader_drops_oldest(phone):
    with phone.subscribe("accelerometer", rate=200, maxlen=10) as accel:
        time.sleep(0.5)
        queued = accel.read()
    assert len(queued) == 10
    assert accel.dropped > 0
    # The kept samples are the newest ones: consecutive, ending at the latest, after the dropped ones.
    assert [s.seq for s in queued] == list(range(queued[0].seq, queued[0].seq + 10))
    assert queued[-1].seq == accel.latest.seq
    assert queued[0].seq == accel.dropped


def test_as_arrays(phone):
    np = pytest.importorskip("numpy")
    with phone.subscribe("accelerometer", rate=100) as accel:
        accel.get(timeout=2)
        time.sleep(0.2)
        samples = accel.read()
    times, values = allsense.as_arrays(samples)
    assert values.shape == (len(samples), 3)
    assert np.all(np.diff(times) >= 0)


def test_wrong_token_is_explained(phone_server):
    with pytest.raises(allsense.TokenError, match="token"):
        allsense.connect(f"http://127.0.0.1:{phone_server.port}/?token=nope", timeout=2)


def test_silent_link_is_detected_and_reconnected(phone_server):
    with allsense.connect(phone_server.url, stall_timeout=1.5) as phone:
        accel = phone.subscribe("accelerometer", rate=50)
        accel.get(timeout=2)
        phone_server.frozen.set()  # Wi-Fi gone: no data, no close
        start = time.monotonic()
        while phone_server.connections < 2 and time.monotonic() - start < 6:
            time.sleep(0.05)
        detected_after = time.monotonic() - start
        assert phone_server.connections == 2, "client never noticed the silent link"
        assert detected_after < 4  # stall_timeout 1.5 s + check interval + reconnect backoff
        accel.read()
        phone_server.frozen.clear()  # network back
        assert accel.get(timeout=3).values


def test_reconnects_and_resubscribes(phone_server):
    with allsense.connect(phone_server.url) as phone:
        accel = phone.subscribe("accelerometer", rate=50)
        accel.get(timeout=2)
        # Simulate Wi-Fi dropping: kill the socket from our side without closing the Phone.
        phone._ws.close()
        time.sleep(1.5)
        accel.read()
        assert accel.get(timeout=3).values  # data flows again
        assert phone_server.connections == 2


def test_sharing_changes_live(phone, phone_server):
    """The user stops and restarts sharing in the app: the subscription follows, no resubscribe."""
    with phone.subscribe("accelerometer", 50) as acc:
        acc.get(timeout=2)
        assert acc.state == "streaming"
        phone_server.set_shared("accelerometer", False)
        deadline = time.monotonic() + 2
        while acc.state != "not_shared" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert acc.state == "not_shared" and not acc.info.shared and "Streams it may send" in acc.status
        time.sleep(0.2)
        acc.read()  # drop what arrived before the change
        assert acc.state == "not_shared"  # a sample still on its way doesn't undo it
        phone_server.set_shared("accelerometer", True)
        acc.get(timeout=2)
        # The first sample can arrive just before "subscribed"; the state follows that message.
        deadline = time.monotonic() + 2
        while acc.state != "streaming" and time.monotonic() < deadline:
            time.sleep(0.02)
        assert acc.state == "streaming" and acc.info.shared and acc.status is None


def test_injected_values(phone, phone_server):
    phone_server.set_values("accelerometer", lambda t, seq: [0.0, 0.0, 30.0])
    with phone.subscribe("accelerometer", 50) as acc:
        assert acc.get(timeout=2).values == (0.0, 0.0, 30.0)


def test_wait_for_a_phone_that_starts_later():
    server = FakePhone()
    server.stop()  # the robot boots before the phone
    threading.Timer(1.0, server.restart).start()
    with allsense.connect(server.url, wait=10, timeout=2) as p:
        assert p.device == DEVICE
    server.stop()


def test_answers_link_test_pings(phone, phone_server):
    rtts = phone_server.ping()
    assert len(rtts) == 1 and rtts[0] is not None and rtts[0] < 1000


def test_non_sensor_streams_keep_precision(phone):
    pos = phone.find("position")
    assert pos.category == "gnss" and pos.sensor_type is None and pos.stream_type == "allsense.gnss.position"
    assert pos.units[:3] == ("°", "°", "m") and pos.unit == ""
    assert phone.find("accelerometer").units == ("m/s²",) * 3  # older catalogs: one unit repeated
    with phone.subscribe("position") as sub:
        s = sub.get(timeout=2)
        assert s["latitude"] == 51.477928123456 and s["longitude"] == -0.001545987654  # not rounded to float32
        assert s["bearing"] is None


def test_screen_and_sound_commands(phone, phone_server):
    assert phone.command("display", face="happy", text="Hi", color="#ffcc00")["ok"]
    assert phone.command("display", action="clear")["ok"]
    with pytest.raises(allsense.AllSenseError, match="Faces: happy"):
        phone.command("display", face="smug")
    with pytest.raises(allsense.AllSenseError, match="needs"):
        phone.command("display")
    assert phone.command("volume", level=0.5)["level"] == pytest.approx(0.5, abs=1 / 15)
    assert phone.command("volume")["level"] == pytest.approx(0.5, abs=1 / 15)  # reading only
    assert phone.command("sound", name="alarm", ms=500)["ok"]
    with pytest.raises(allsense.AllSenseError, match="alarm"):
        phone.command("sound", name="trumpet")


def test_saved_sounds_over_commands(phone):
    import base64

    assert phone.command("sound", action="save", name="beep", data=base64.b64encode(b"RIFF....WAVE").decode())["bytes"] == 12
    assert {"name": "beep", "format": "wav", "bytes": 12} in phone.command("sound", action="list")["sounds"]
    assert phone.command("sound", name="beep", loop=True, ms=1000)["ok"]
    assert phone.command("sound", action="delete", name="beep")["ok"]
    with pytest.raises(allsense.AllSenseError, match="No sound called"):
        phone.command("sound", name="beep")


def test_camera_settings(phone):
    r = phone.command("camera", camera="back", ev=-1, focus="infinity", white_balance="daylight")
    assert r["settings"]["focus"] == "infinity" and r["settings"]["ev"] == -1 and "iso_range" in r["supports"]
    assert phone.command("camera", camera="back")["settings"]["white_balance"] == "daylight"  # read back
    assert phone.command("camera", camera="back", reset=True)["settings"]["focus"] == "auto"
    with pytest.raises(allsense.AllSenseError, match="daylight"):
        phone.command("camera", camera="back", white_balance="disco")


def test_commands(phone, phone_server):
    assert phone.command("vibrate", ms=300)["ok"]
    assert phone_server.commands_run[-1]["ms"] == 300
    with pytest.raises(allsense.AllSenseError, match="Unknown command"):
        phone.command("teleport")
    phone_server.allow_commands = False
    with pytest.raises(allsense.AllSenseError, match="Robot can control the phone"):
        phone.command("ping")
