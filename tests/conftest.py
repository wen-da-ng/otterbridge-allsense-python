"""Fixtures shared by the tests: a fake phone and a client connected to it."""

import pytest

import allsense
from allsense_testkit import FakePhone


@pytest.fixture
def phone_server():
    server = FakePhone()
    yield server
    server.stop()


@pytest.fixture
def phone(phone_server):
    with allsense.connect(phone_server.url, name="pytest") as p:
        yield p
