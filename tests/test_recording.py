import io
import json
import os
import shutil
import zipfile

import pytest

import allsense
from allsense.recording import _write_example

STREAMS = [
    {"name": "accelerometer", "fields": ["x", "y", "z"], "units": ["m/s²"] * 3,
     "rows": [(1_000_000_000, (0.1, 9.8, 0.2)), (1_020_000_000, (0.0, 9.81, None))]},
    {"name": "position", "fields": ["latitude", "longitude", "altitude"], "units": ["°", "°", "m"],
     "rows": [(1_500_000_000, (51.477928123456, -0.001545987654, None))]},
]


def test_loads_folder_and_zip(tmp_path):
    folder = tmp_path / "allsense_phone_20261017-191502"
    _write_example(str(folder), STREAMS)
    archive = tmp_path / "rec.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name in os.listdir(folder):
            z.write(folder / name, f"{folder.name}/{name}")
    for path in (folder, archive):
        rec = allsense.load_recording(str(path))
        assert rec.device == "phone" and set(rec.streams) == {"accelerometer", "position"}
        acc = rec["accelerometer"]
        assert len(acc) == 2 and acc.t_ns == [1_000_000_000, 1_020_000_000] and acc.units == ("m/s²",) * 3
        assert acc.values[1] == (0.0, 9.81, None)
        assert rec["position"].column("latitude") == [51.477928123456]  # full precision kept


def test_loads_a_folder_nested_by_unzipping(tmp_path):
    _write_example(str(tmp_path / "outer" / "allsense_phone_20261017-191502"), STREAMS)
    assert len(allsense.load_recording(str(tmp_path / "outer"))["accelerometer"]) == 2


def test_arrays_and_errors(tmp_path):
    _write_example(str(tmp_path / "r"), STREAMS)
    rec = allsense.load_recording(str(tmp_path / "r"))
    times, values = rec["accelerometer"].arrays()
    assert values.shape == (2, 3) and times[0] == 1.0
    import math
    assert math.isnan(values[1, 2])
    with pytest.raises(KeyError, match="accelerometer"):
        rec["gyroscope"]
    os.remove(tmp_path / "r" / "meta.json")
    with pytest.raises(ValueError, match="meta.json"):
        allsense.load_recording(str(tmp_path / "r"))


def test_zip_reads_only_listed_files_within_limits(tmp_path, monkeypatch):
    import allsense.recording as recording

    folder = tmp_path / "r"
    _write_example(str(folder), STREAMS)
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for name in os.listdir(folder):
            z.write(folder / name, name)
        z.writestr("unlisted.bin", b"\0" * (4 * 1024 * 1024))  # compresses to a few KB
    monkeypatch.setattr(recording, "MAX_FILE_BYTES", 1024 * 1024)
    rec = allsense.load_recording(str(archive))  # the unlisted member is never read
    assert set(rec.streams) == {"accelerometer", "position"}

    # A listed file over the limit is refused, by its declared size and while reading.
    monkeypatch.setattr(recording, "MAX_FILE_BYTES", 50)
    with pytest.raises(ValueError, match="accelerometer.csv.*too large"):
        allsense.load_recording(str(archive))
    monkeypatch.setattr(recording, "MAX_FILE_BYTES", 1024 * 1024)
    monkeypatch.setattr(recording, "MAX_TOTAL_BYTES", 100)
    with pytest.raises(ValueError, match="too large"):
        allsense.load_recording(str(archive))


def test_limit_holds_even_if_the_zip_lies_about_sizes(tmp_path):
    from allsense.recording import _Limited

    with pytest.raises(ValueError, match="big.csv in this recording is too large"):
        _Limited(io.BytesIO(b"x" * 100), "big.csv", 10, [10 ** 9]).read()


def test_meta_cannot_point_outside_the_recording(tmp_path):
    _write_example(str(tmp_path / "rec"), STREAMS)
    (tmp_path / "secret.csv").write_text("t_ns,time_unix,x\n1,0.1,42\n", encoding="utf-8")
    meta_path = tmp_path / "rec" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["streams"][0]["file"] = "../secret.csv"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    assert set(allsense.load_recording(str(tmp_path / "rec")).streams) == {"position"}
