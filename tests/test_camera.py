import io

import pytest

import allsense
from allsense.camera import camera_url, read_frames

from allsense_testkit.fake_phone import FAKE_JPEG


def test_cameras_are_in_the_catalog(phone):
    cams = phone.cameras()
    assert [c.name for c in cams] == ["back"] and cams[0].raw["sensor_orientation"] == 90
    with pytest.raises(allsense.AllSenseError, match="isn't a camera"):
        phone.snapshot("accelerometer")


def test_snapshot(phone):
    assert phone.snapshot("back") == FAKE_JPEG


def test_url_keeps_token_and_params():
    url = camera_url("ws://10.0.0.2:8765/ws?token=abc", "/camera/back.mjpg", size="320x240", fps=5, quality=None)
    assert url == "http://10.0.0.2:8765/camera/back.mjpg?token=abc&size=320x240&fps=5"


def test_mjpeg_parsing():
    def part(jpeg, t):
        head = b"--allsenseframe\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\nX-Timestamp-Ns: %d\r\nX-Size: 640x480\r\n\r\n"
        return head % (len(jpeg), t) + jpeg + b"\r\n"

    body = io.BytesIO(part(b"\xff\xd8one\xff\xd9", 100) + part(b"\xff\xd8two\xff\xd9", 200))
    frames = list(read_frames(body))
    assert [f.jpeg for f in frames] == [b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9"]
    assert frames[1].t_ns == 200 and frames[0].size == (640, 480)


def test_h264_parts_carry_keyframe_flags():
    head = b"--allsenseframe\r\nContent-Type: video/h264\r\nContent-Length: %d\r\nX-Timestamp-Ns: 7\r\nX-Keyframe: %d\r\n\r\n"
    unit = b"\x00\x00\x00\x01\x67sps\x00\x00\x00\x01\x65idr"
    body = io.BytesIO(head % (len(unit), 1) + unit + b"\r\n" + head % (3, 0) + b"abc" + b"\r\n")
    frames = list(read_frames(body))
    assert frames[0].keyframe is True and frames[0].data == unit and frames[1].keyframe is False


def test_streams_from_the_fake_phone(phone):
    with phone.camera("back") as cam:
        frames = list(cam)
    assert len(frames) == 10 and frames[0].jpeg.startswith(b"\xff\xd8") and frames[1].t_ns == 5_100_000_000
    with phone.camera("back", codec="h264") as cam:
        units = list(cam)
    assert [u.keyframe for u in units[:3]] == [False, True, False] and b"\x67" in units[1].data
