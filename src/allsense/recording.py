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
from typing import Dict, List, Optional, Sequence, Tuple


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


def _parse_csv(text: str) -> Tuple[List[int], List[float], List[Tuple[Optional[float], ...]]]:
    rows = csv.reader(io.StringIO(text))
    next(rows, None)  # header
    t_ns, times, values = [], [], []
    for row in rows:
        if len(row) < 2:
            continue  # a last line cut off by a dead battery
        t_ns.append(int(row[0]))
        times.append(float(row[1]))
        values.append(tuple(float(v) if v != "" else None for v in row[2:]))
    return t_ns, times, values


def load_recording(path: str) -> Recording:
    """Loads a recording from its folder or its .zip (as downloaded from the phone)."""
    files: Dict[str, str] = {}
    if os.path.isdir(path):
        # Unzipping on Windows often nests the folder in one of the same name: look one level down.
        if not os.path.exists(os.path.join(path, "meta.json")):
            inner = [d for d in os.listdir(path) if os.path.exists(os.path.join(path, d, "meta.json"))]
            if len(inner) == 1:
                path = os.path.join(path, inner[0])
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isfile(full):
                with open(full, encoding="utf-8") as f:
                    files[name] = f.read()
    else:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if not info.is_dir():
                    files[os.path.basename(info.filename)] = z.read(info).decode("utf-8")
    if "meta.json" not in files:
        raise ValueError(f"{path} isn't an AllSense recording (no meta.json)")
    meta = json.loads(files["meta.json"])
    streams = {}
    for s in meta.get("streams", []):
        text = files.get(s.get("file", ""))
        if text is None:
            continue
        t_ns, times, values = _parse_csv(text)
        streams[s["name"]] = RecordedStream(s["name"], tuple(s.get("fields", ())), tuple(s.get("units", ())), s, t_ns, times, values)
    return Recording(meta, streams)


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
