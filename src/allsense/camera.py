"""Camera video from the phone: MJPEG over HTTP (https://docs.allsense.otterbridge.org/PROTOCOL/#camera).

    jpeg = phone.snapshot("back")                    # one JPEG (bytes)
    for frame in phone.camera("back", fps=10):       # live; frame.jpeg, frame.t_ns, frame.size
        ...
    for unit in phone.camera("back", codec="h264"):  # H.264 access units: unit.data, unit.keyframe
        ...                                          # decode with PyAV: av.CodecContext.create("h264", "r")
    # NumPy / OpenCV: cv2.imdecode(numpy.frombuffer(frame.jpeg, numpy.uint8), cv2.IMREAD_COLOR)

Frames are as the camera sensor sees them; rotate by ``info.raw["sensor_orientation"]`` degrees
clockwise to show them upright. Standard library only.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qs

from .client import AllSenseError, redact

# Upper bounds on what the phone may send, so a broken or hostile server can't make us allocate
# without limit. Far above real frames (a 4K JPEG is a few MB).
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_FRAME_BYTES = 32 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
_MAX_HEADER_LINE = 8192


@dataclass(frozen=True)
class CameraFrame:
    jpeg: bytes  # the frame's bytes: a JPEG, or for codec="h264" one H.264 access unit (Annex B)
    t_ns: Optional[int]  # sensor timestamp on the phone's boot clock (like samples' t)
    size: Optional[Tuple[int, int]]
    keyframe: Optional[bool] = None  # H.264 only: True for keyframes (they carry SPS/PPS; decoding can start there)

    @property
    def data(self) -> bytes:
        return self.jpeg


def check_path(path: str) -> str:
    """``path`` if it's safe to put after ``http://host:port`` (paths come from the phone's catalog)."""
    if not isinstance(path, str) or not path.startswith("/") or any(c in path for c in "@\\\r\n"):
        raise AllSenseError(f"Unusable media path {path!r}: it must start with '/' and contain no '@', '\\' or line breaks")
    return path


def camera_url(ws_url: str, path: str, **params) -> str:
    """http(s)://host:port<path>?token=…&params from the client's ws:// URL."""
    check_path(path)
    u = urlparse(ws_url)
    token = parse_qs(u.query).get("token", [""])[0]
    scheme = "https" if u.scheme == "wss" else "http"
    query = urlencode({"token": token, **{k: v for k, v in params.items() if v is not None}})
    return f"{scheme}://{u.netloc}{path}?{query}"


def _open(url: str, timeout: float):
    try:
        return urllib.request.urlopen(url, timeout=timeout)
    except urllib.error.HTTPError as e:
        # The phone explains (not shared, no camera access, camera busy…).
        raise AllSenseError(f"Camera: {e.read(MAX_ERROR_BYTES).decode(errors='replace').strip() or e.reason}") from None
    except OSError as e:
        raise AllSenseError(f"Can't reach the phone's camera at {url.split('?')[0]}: {redact(e)}") from None


def _readline(stream: BinaryIO) -> bytes:
    line = stream.readline(_MAX_HEADER_LINE)
    if len(line) >= _MAX_HEADER_LINE and not line.endswith(b"\n"):
        raise AllSenseError("The camera stream sent an overlong header line")
    return line


def read_frames(stream: BinaryIO) -> Iterator[CameraFrame]:
    """Parses a multipart/x-mixed-replace MJPEG body into frames."""
    while True:
        headers = {}
        line = _readline(stream)
        while line in (b"\r\n", b"\n"):  # the blank line after the previous frame
            line = _readline(stream)
        if not line:
            return
        if not line.startswith(b"--"):
            raise AllSenseError(f"Unexpected data in the camera stream: {line[:40]!r}")
        while True:
            line = _readline(stream)
            if not line:
                return
            line = line.strip()
            if not line:
                break
            k, _, v = line.decode(errors="replace").partition(":")
            headers[k.strip().lower()] = v.strip()
            if len(headers) > 64:
                raise AllSenseError("The camera stream sent too many headers")
        try:
            n = int(headers.get("content-length", "0"))
        except ValueError:
            raise AllSenseError(f"Bad Content-Length in the camera stream: {headers['content-length'][:40]!r}") from None
        if not 0 <= n <= MAX_FRAME_BYTES:
            raise AllSenseError(f"Camera frame of {n} bytes refused (limit {MAX_FRAME_BYTES})")
        jpeg = stream.read(n)
        if len(jpeg) < n:
            return
        size = None
        if "x-size" in headers and "x" in headers["x-size"]:
            w, _, h = headers["x-size"].partition("x")
            size = (int(w), int(h))
        t = headers.get("x-timestamp-ns")
        key = headers.get("x-keyframe")
        yield CameraFrame(jpeg, int(t) if t else None, size, (key == "1") if key is not None else None)


class CameraStream:
    """Live frames from one camera. Iterate it; ``close()`` (or leaving a ``with``) stops it."""

    def __init__(self, url: str, timeout: float):
        self._response = _open(url, timeout)
        self._frames = read_frames(self._response)

    def __iter__(self) -> Iterator[CameraFrame]:
        return self._frames

    def __next__(self) -> CameraFrame:
        return next(self._frames)

    def close(self) -> None:
        self._response.close()

    def __enter__(self) -> "CameraStream":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_limited(r, limit: int, what: str) -> bytes:
    """The whole body of response ``r``, refusing more than ``limit`` bytes."""
    declared = (getattr(r, "headers", None) or {}).get("Content-Length")
    if declared and declared.strip().isdigit() and int(declared) > limit:
        raise AllSenseError(f"{what} of {int(declared)} bytes refused (limit {limit})")
    data = r.read(limit + 1)
    if len(data) > limit:
        raise AllSenseError(f"{what} larger than {limit} bytes refused")
    return data


def snapshot(url: str, timeout: float) -> bytes:
    with _open(url, timeout) as r:
        return read_limited(r, MAX_SNAPSHOT_BYTES, "Snapshot")
