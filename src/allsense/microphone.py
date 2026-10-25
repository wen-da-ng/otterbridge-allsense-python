"""Microphone audio from the phone: an endless WAV over HTTP (https://docs.allsense.otterbridge.org/PROTOCOL/#microphone).

    with phone.microphone("mic", rate=16000) as mic:
        for chunk in mic:            # chunk.samples (array of int16), chunk.t_ns, chunk.rate
            ...
    numpy:  chunk.as_numpy() -> int16 array;  / 32768 for floats in [-1, 1)

Loudness alone is an ordinary stream: phone.subscribe("mic_level") gives rms/peak in dBFS.
"""

from __future__ import annotations

import array
import struct
import sys
from dataclasses import dataclass
from typing import Iterator, Optional

from .camera import _open, camera_url


@dataclass(frozen=True)
class AudioChunk:
    samples: "array.array"  # signed 16-bit mono
    t_ns: Optional[int]  # first sample's time on the phone's boot clock
    rate: int

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.rate

    def as_numpy(self):
        import numpy as np

        return np.frombuffer(self.samples.tobytes(), dtype=np.int16)


def read_wav_stream(stream, chunk_samples: int, t0_ns: Optional[int], rate_hint: int) -> Iterator[AudioChunk]:
    """Parses a (possibly endless) mono 16-bit WAV into chunks of ``chunk_samples``."""
    header = stream.read(44)
    if len(header) < 44 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
        raise ValueError("Not a WAV stream")
    channels, rate, bits = struct.unpack_from("<HIxxxxxxH", header, 22)
    if channels != 1 or bits != 16:
        raise ValueError(f"Expected mono 16-bit audio, got {channels} channel(s), {bits} bits")
    rate = rate or rate_hint
    done = 0
    while True:
        data = stream.read(chunk_samples * 2)
        if len(data) < 2:
            return
        data = data[: len(data) // 2 * 2]
        samples = array.array("h")
        samples.frombytes(data)
        if sys.byteorder == "big":
            samples.byteswap()
        t = t0_ns + done * 1_000_000_000 // rate if t0_ns is not None else None
        done += len(samples)
        yield AudioChunk(samples, t, rate)


class MicStream:
    """Live audio from one microphone. Iterate it; ``close()`` (or leaving a ``with``) stops it."""

    def __init__(self, url: str, timeout: float, chunk_seconds: float):
        self._response = _open(url, timeout)
        t0 = self._response.headers.get("X-Timestamp-Ns")
        rate = int(self._response.headers.get("X-Sample-Rate") or 16000)
        self.rate = rate
        self._chunks = read_wav_stream(self._response, max(1, int(rate * chunk_seconds)), int(t0) if t0 else None, rate)

    def __iter__(self) -> Iterator[AudioChunk]:
        return self._chunks

    def __next__(self) -> AudioChunk:
        return next(self._chunks)

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> "MicStream":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def mic_url(ws_url: str, path: str, rate: int) -> str:
    return camera_url(ws_url, path, rate=rate)
