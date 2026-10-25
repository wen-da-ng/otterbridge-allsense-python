"""A phone with its mount set: robot-frame streams share names with the phone's own."""

import pytest

import allsense
from allsense_testkit import FakePhone


@pytest.fixture
def mounted():
    server = FakePhone(mounted=True)
    with allsense.connect(server.url, name="pytest") as p:
        yield p
    server.stop()


def test_plain_names_stay_the_phones_own(mounted):
    assert mounted.find("accelerometer").category == "sensors"


def test_category_dot_name_picks_the_robot_frame(mounted):
    robot = mounted.find("robot.accelerometer")
    assert robot.category == "robot" and robot.topic == "allsense.rover.robot.accelerometer"
    assert list(mounted.find("robot.attitude").fields) == ["roll", "pitch", "yaw", "heading"]


def test_robot_streams_stream(mounted):
    with mounted.subscribe("robot.attitude", rate=50) as sub:
        samples = [s for _, s in zip(range(3), sub)]
    assert len(samples) == 3 and samples[0].values == (2.0, -1.5, 90.0, 0.0)
