"""The examples in examples/ (and so the ones in the docs) run against the fake phone."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from allsense_testkit import FakePhone

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def first_lines(script, url, n=5, *args, cwd=None):
    """Runs an endless example until it has printed n lines."""
    p = subprocess.Popen(
        [sys.executable, "-u", str(EXAMPLES / script), url, *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=ENV, cwd=cwd,
    )
    try:
        lines = [p.stdout.readline() for _ in range(n)]
    finally:
        p.kill()
        err = p.stderr.read()
    assert all(lines), f"{script} stopped early: {err}"
    return "".join(lines)


def run(script, url, *args, cwd=None):
    """Runs an example that ends by itself; returns its output."""
    r = subprocess.run([sys.executable, str(EXAMPLES / script), url, *args], capture_output=True, text=True,
                       encoding="utf-8", env=ENV, cwd=cwd, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.parametrize("script", ["quickstart.py", "print_values.py"])
def test_example_prints_samples(script, phone_server):
    assert "9.81" in first_lines(script, phone_server.url)  # the fake phone's z axis


def test_robot_attitude():
    server = FakePhone(mounted=True)
    try:
        out = first_lines("robot_attitude.py", server.url, 3)
    finally:
        server.stop()
    assert "roll   +2.0°  pitch   -1.5°  heading   0.0°" in out


def test_follow_tag():
    server = FakePhone(apriltags=True)
    try:
        out = first_lines("follow_tag.py", server.url, 3)
    finally:
        server.stop()
    assert "Tag 7: 1.20 m, +10° → left" in out


def test_robot_face(phone_server):
    assert "Done" in run("robot_face.py", phone_server.url)
    names = [(c["command"], c.get("face") or c.get("action")) for c in phone_server.commands_run]
    assert names == [("display", "happy"), ("say", None), ("vibrate", None), ("display", "thinking"), ("display", "clear")]


def test_record_run(phone_server):
    assert "Saved on the phone" in run("record_run.py", phone_server.url, "0.2")
    start, stop = phone_server.commands_run
    assert start["action"] == "start" and start["streams"] == ["accelerometer", "gyroscope", "position"]
    assert stop["action"] == "stop"


def test_camera_and_mic(phone_server, tmp_path):
    out = run("camera_and_mic.py", phone_server.url, cwd=tmp_path)
    assert "Saved photo.jpg from back" in out
    assert (tmp_path / "photo.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert "mic: -" in out and "dBFS" in out  # the fake 1 kHz tone, about -15 dBFS
