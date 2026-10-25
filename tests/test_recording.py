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
