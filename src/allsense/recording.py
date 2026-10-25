"""Read AllSense recordings (https://docs.allsense.otterbridge.org/RECORDING/): a folder or .zip with meta.json and one CSV per stream.

    rec = allsense.load_recording("allsense_phone_20261017-191502.zip")
    acc = rec["accelerometer"]
    acc.t_ns, acc.time, acc.values      # phone boot-clock ns, Unix seconds, rows of values (None = missing)
    acc.fields, acc.units
    times, values = acc.arrays()        # NumPy: shapes (n,) and (n, k), missing values as NaN
"""

from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from typing import IO, Callable, Dict, List, Optional, Sequence, Tuple

# A recording (or a .zip pretending to be one) can't make us read more than this. Hours of 200 Hz
# IMU data are a few hundred MB of CSV.
MAX_META_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 ** 3
MAX_TOTAL_BYTES = 8 * 1024 ** 3


@dataclass
class RecordedStream:
    """One stream of a recording."""

    name: str
    fields: Tuple[str, ...]
    units: Tuple[str, ...]
    meta: dict = field(repr=False)
    t_ns: List[int] = field(default_factory=list, repr=False)
    time: List[float] = field(default_factory=list, repr=False)
    values: List[Tuple[Optional[float], ...]] = field(default_factory=list, repr=False)

    def __len__(self) -> int:
        return len(self.t_ns)

    def column(self, name: str) -> List[Optional[float]]:
        """All values of one field, e.g. ``rec["position"].column("latitude")``."""
        i = self.fields.index(name)
        return [row[i] if i < len(row) else None for row in self.values]

    def arrays(self):
        """``(times, values)`` as NumPy arrays (Unix seconds, and floats with NaN for missing)."""
        import numpy as np

        width = max((len(r) for r in self.values), default=len(self.fields))
        values = np.full((len(self.values), width), np.nan)
        for i, row in enumerate(self.values):
            for j, v in enumerate(row):
                if v is not None:
                    values[i, j] = v
        return np.asarray(self.time, dtype=float), values


@dataclass
class Recording:
    """A whole recording: ``rec["accelerometer"]``, ``rec.streams``, ``rec.meta``."""

    meta: dict
    streams: Dict[str, RecordedStream]

    @property
    def device(self) -> str:
        return self.meta.get("device", "")

    def __getitem__(self, name: str) -> RecordedStream:
        try:
            return self.streams[name]
        except KeyError:
            raise KeyError(f"No stream {name!r} in this recording; it has {', '.join(self.streams) or 'none'}") from None

    def __iter__(self):
        return iter(self.streams.values())


class _Limited(io.RawIOBase):
    """A binary file that raises once more than ``limit`` bytes come out of it (or ``budget`` across files)."""

    def __init__(self, raw: IO[bytes], name: str, limit: int, budget: List[int]):
        self._raw, self._name, self._left, self._budget = raw, name, limit, budget

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        data = self._raw.read(len(b))
        self._left -= len(data)
        self._budget[0] -= len(data)
        if self._left < 0:
            raise ValueError(f"{self._name} in this recording is too large")
        if self._budget[0] < 0:
            raise ValueError(f"This recording is too large (over {MAX_TOTAL_BYTES} bytes)")
        b[: len(data)] = data
        return len(data)

    def close(self) -> None:
        self._raw.close()
        super().close()


def _parse_csv(text) -> Tuple[List[int], List[float], List[Tuple[Optional[float], ...]]]:
    """Parses a stream's CSV from a string or a text file."""
    rows = csv.reader(io.StringIO(text) if isinstance(text, str) else text)
    next(rows, None)  # header
    t_ns, times, values = [], [], []
    for row in rows:
        if len(row) < 2:
            continue  # a last line cut off by a dead battery
        t_ns.append(int(row[0]))
        times.append(float(row[1]))
        values.append(tuple(float(v) if v != "" else None for v in row[2:]))
    return t_ns, times, values


def _load(path: str, open_file: Callable[[str, int], Optional[IO[str]]]) -> Recording:
    """Reads meta.json, then only the CSV files it lists (plain names, no folders)."""
    meta_file = open_file("meta.json", MAX_META_BYTES)
    if meta_file is None:
        raise ValueError(f"{path} isn't an AllSense recording (no meta.json)")
    with meta_file:
        meta = json.loads(meta_file.read())
    streams = {}
    for s in meta.get("streams", []):
        name = s.get("file", "")
        if not isinstance(name, str) or not name or os.path.basename(name) != name or name in (".", ".."):
            continue
        f = open_file(name, MAX_FILE_BYTES)
        if f is None:
            continue
        with f:
            t_ns, times, values = _parse_csv(f)
        streams[s["name"]] = RecordedStream(s["name"], tuple(s.get("fields", ())), tuple(s.get("units", ())), s, t_ns, times, values)
    return Recording(meta, streams)


def load_recording(path: str) -> Recording:
    """Loads a recording from its folder or its .zip (as downloaded from the phone).

    Only meta.json and the CSV files it lists are read, each up to ``MAX_FILE_BYTES``."""
    budget = [MAX_TOTAL_BYTES]

    def text(raw: IO[bytes], name: str, limit: int) -> IO[str]:
        return io.TextIOWrapper(io.BufferedReader(_Limited(raw, name, limit, budget)), encoding="utf-8", newline="")

    if os.path.isdir(path):
        # Unzipping on Windows often nests the folder in one of the same name: look one level down.
        if not os.path.exists(os.path.join(path, "meta.json")):
            inner = [d for d in os.listdir(path) if os.path.exists(os.path.join(path, d, "meta.json"))]
            if len(inner) == 1:
                path = os.path.join(path, inner[0])
        folder = path

        def open_file(name: str, limit: int) -> Optional[IO[str]]:
            full = os.path.join(folder, name)
            return text(open(full, "rb"), name, limit) if os.path.isfile(full) else None

        return _load(path, open_file)
    with zipfile.ZipFile(path) as z:
        members = {}
        for info in z.infolist():
            if not info.is_dir():
                members.setdefault(os.path.basename(info.filename), info)

        def open_file(name: str, limit: int) -> Optional[IO[str]]:
            info = members.get(name)
            if info is None:
                return None
            if info.file_size > limit:
                raise ValueError(f"{name} in {path} is too large ({info.file_size} bytes, limit {limit})")
            return text(z.open(info), name, limit)

        return _load(path, open_file)


def _write_example(folder: str, streams: Sequence[dict]) -> None:
    """Writes a small recording in the documented format (for tests and docs)."""
    os.makedirs(folder, exist_ok=True)
    meta = {"format": "allsense-recording", "version": 1, "device": "phone", "boot_to_unix_ns": 0, "streams": []}
    for s in streams:
        meta["streams"].append({k: s[k] for k in ("name", "fields", "units")} | {"file": s["name"] + ".csv"})
        header = ["t_ns", "time_unix"] + [f + (f" ({u})" if u else "") for f, u in zip(s["fields"], s["units"])]
        with open(os.path.join(folder, s["name"] + ".csv"), "w", encoding="utf-8", newline="") as f:
            f.write(",".join(header) + "\n")
            for t, row in s["rows"]:
                f.write(f"{t},{t / 1e9:.6f}," + ",".join("" if v is None else repr(v) for v in row) + "\n")
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f)
