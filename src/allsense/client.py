"""Synchronous client for the AllSense protocol (https://docs.allsense.otterbridge.org/PROTOCOL/).

A background thread reads from the phone; your code just loops over samples. No asyncio needed.
"""

from __future__ import annotations

import collections
import difflib
import itertools
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Iterator, List, Optional, Sequence, Tuple, Union
from urllib.parse import parse_qs, urlencode, urlparse

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import ClientConnection
from websockets.sync.client import connect as ws_connect

log = logging.getLogger("allsense")

DEFAULT_PORT = 8765
WS_PATH = "/ws"


class AllSenseError(Exception):
    """Something went wrong talking to the phone (bad link, wrong token, unknown sensor…)."""


TokenRejected = "The phone rejected the pairing token"


class TokenError(AllSenseError):
    """The link has no pairing token, or the phone rejected it. Retrying won't help: get the current link."""


@dataclass(frozen=True)
class StreamInfo:
    """One stream the phone offers, from its catalog."""

    topic: str  # allsense.<device>.<category>.<name>
    name: str  # the name chosen in the app, e.g. "accelerometer"
    id: str  # stable hardware ID; survives renames and reboots
    category: str  # "sensors", "gnss", "battery", "wifi", "cellular"
    kind: str
    type: str  # human-readable, e.g. "Accelerometer", "GNSS position"
    sensor_type: Optional[int]  # Android Sensor.TYPE_* number, e.g. 1 = accelerometer; None for non-sensors
    stream_type: str  # machine-readable: "android.sensor.accelerometer", "allsense.gnss.position"…
    hardware: str
    vendor: str
    fields: Tuple[str, ...]
    unit: str  # shared by all fields, or "" when they differ (then see units)
    units: Tuple[str, ...]  # one per field
    reporting: str  # "continuous", "on change", "one shot" or "special trigger"
    max_rate_hz: Optional[float]
    wake_up: bool
    basic: bool
    shared: bool  # False: the user hasn't shared it over the network (Home → Connect → Browser → Streams it may send)
    raw: dict = field(repr=False, compare=False)

    @property
    def continuous(self) -> bool:
        return self.reporting == "continuous"

    @classmethod
    def from_json(cls, d: dict) -> "StreamInfo":
        return cls(
            topic=d["topic"],
            name=d.get("name", d["topic"].rsplit(".", 1)[-1]),
            id=d.get("id", ""),
            category=d.get("category", ""),
            kind=d.get("kind", ""),
            type=d.get("type", ""),
            sensor_type=_sensor_type(d),
            stream_type=d.get("stream_type", ""),
            hardware=d.get("hardware", ""),
            vendor=d.get("vendor", ""),
            fields=tuple(d.get("fields") or ()),
            unit=d.get("unit", ""),
            units=tuple(d.get("units") or [d.get("unit", "")] * len(d.get("fields") or ())),
            reporting=d.get("reporting", "continuous"),
            max_rate_hz=d.get("max_rate_hz"),
            wake_up=bool(d.get("wake_up", False)),
            basic=bool(d.get("basic", True)),
            shared=bool(d.get("shared", True)),
            raw=d,
        )


@dataclass(frozen=True)
class Sample:
    """One reading. ``s.values`` is a tuple; ``s["x"]`` or ``s[0]`` picks one value."""

    topic: str
    seq: int
    t: int  # phone hardware timestamp, ns on the phone's boot clock
    time: float  # the same moment as Unix time in seconds on *this* computer's clock (clock-synced)
    values: Tuple[Optional[float], ...]
    accuracy: int
    fields: Tuple[str, ...] = field(repr=False, default=())

    def __getitem__(self, key: Union[int, str]) -> Optional[float]:
        if isinstance(key, str):
            return self.values[self.fields.index(key)]
        return self.values[key]

    def as_dict(self) -> Dict[str, Optional[float]]:
        names = [self.fields[i] if i < len(self.fields) else f"v{i}" for i in range(len(self.values))]
        return dict(zip(names, self.values))


class Subscription:
    """Samples from one stream. Iterate it, or use :meth:`get`, :meth:`read` and :attr:`latest`.

    Keeps the newest ``maxlen`` samples; if your code is slower than the stream, the oldest are
    dropped (counted in :attr:`dropped`) so you always see fresh data.
    """

    def __init__(self, phone: "Phone", info: StreamInfo, rate: Optional[float], maxlen: int,
                 callback: Optional[Callable[[Sample], None]]):
        self.phone = phone
        self.info = info
        self.rate = rate
        self.callback = callback
        self.status: Optional[str] = None  # e.g. "Android won't deliver this sensor to apps"
        # "streaming", "armed" (one-shot sensor waiting), "refused" (Android won't deliver it) or
        # "not_shared" (the user doesn't share it over the network; it starts by itself once they do).
        # None until the phone answers.
        self.state: Optional[str] = None
        self.dropped = 0  # samples lost on the way (seq gaps) or not read in time (queue overflow)
        self._queue: Deque[Sample] = collections.deque(maxlen=maxlen)
        self._cond = threading.Condition()
        self._latest: Optional[Sample] = None
        self._last_seq: Optional[int] = None
        self._closed = False

    @property
    def topic(self) -> str:
        return self.info.topic

    @property
    def latest(self) -> Optional[Sample]:
        """The newest sample, without waiting (None until the first one arrives)."""
        return self._latest

    def get(self, timeout: Optional[float] = None) -> Sample:
        """Waits for the next sample. Raises ``TimeoutError`` after ``timeout`` seconds."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._queue or self._closed or self.phone.closed, timeout):
                raise TimeoutError(f"No data from {self.info.name} for {timeout} s" + (f" ({self.status})" if self.status else ""))
            if self._queue:
                return self._queue.popleft()
        raise AllSenseError(f"Subscription to {self.info.name} is closed")

    def read(self, n: Optional[int] = None, timeout: float = 0.0) -> List[Sample]:
        """Returns up to ``n`` queued samples (all if ``n`` is None), waiting up to ``timeout`` for the first."""
        with self._cond:
            if timeout:
                self._cond.wait_for(lambda: self._queue or self._closed or self.phone.closed, timeout)
            count = len(self._queue) if n is None else min(n, len(self._queue))
            return [self._queue.popleft() for _ in range(count)]

    def __iter__(self) -> Iterator[Sample]:
        while not self._closed and not self.phone.closed:
            try:
                yield self.get(timeout=1.0)
            except TimeoutError:
                continue
            except AllSenseError:
                return

    def set_rate(self, rate: float) -> None:
        """Changes the rate (Hz) of a continuous stream."""
        self.rate = rate
        self.phone._send({"op": "subscribe", "topics": [self.topic], "rate_hz": rate})

    def close(self) -> None:
        """Stops this stream (the phone stops the sensor if nobody else uses it)."""
        if self._closed:
            return
        self._closed = True
        self.phone._unsubscribe(self)
        with self._cond:
            self._cond.notify_all()

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<Subscription {self.topic} rate={self.rate}>"

    # Called from the reader thread.
    def _push(self, sample: Sample) -> None:
        if self._last_seq is not None and sample.seq > self._last_seq + 1:
            self.dropped += sample.seq - self._last_seq - 1
        self._last_seq = sample.seq
        self._latest = sample
        # A sample means the stream runs, unless the phone has since said it stopped: a sample can still be on its way
        # after a not_shared or refused status. Only "subscribed" undoes those.
        if self.state not in ("not_shared", "refused"):
            self.state = "streaming"
        with self._cond:
            if len(self._queue) == self._queue.maxlen:
                self.dropped += 1
            self._queue.append(sample)
            self._cond.notify_all()
        if self.callback is not None:
            try:
                self.callback(sample)
            except Exception:  # never let user code kill the reader thread
                log.exception("Error in callback for %s", self.topic)


class _ClockSync:
    """NTP-style estimate of (phone boot clock − this computer's monotonic clock).

    Keeps the recent exchange with the smallest round trip, which has the least error.
    """

    def __init__(self) -> None:
        self.samples: Deque[Tuple[int, int]] = collections.deque(maxlen=16)  # (rtt_ns, offset_ns)
        self.boot_to_unix_ns: Optional[int] = None  # phone's own estimate, used until we have samples
        self.mono_to_unix_ns = time.time_ns() - time.monotonic_ns()

    def add(self, t0: int, t1: int, t2: int, t3: int) -> None:
        rtt = (t3 - t0) - (t2 - t1)
        offset = ((t1 - t0) + (t2 - t3)) // 2
        if rtt >= 0:
            self.samples.append((rtt, offset))

    @property
    def round_trip_ms(self) -> Optional[float]:
        return min(self.samples)[0] / 1e6 if self.samples else None

    def to_unix(self, phone_ns: int) -> float:
        if self.samples:
            offset = min(self.samples)[1]
            return (phone_ns - offset + self.mono_to_unix_ns) / 1e9
        if self.boot_to_unix_ns is not None:
            return (phone_ns + self.boot_to_unix_ns) / 1e9
        return time.time()


def _sensor_type(d: dict) -> Optional[int]:
    if isinstance(d.get("sensor_type"), int):
        return d["sensor_type"]
    # Older app versions: the stable ID is "sensors/<type>/…".
    parts = d.get("id", "").split("/")
    if len(parts) > 1 and parts[0] == "sensors" and parts[1].isdigit():
        return int(parts[1])
    return None


def _ws_url(url: str, token: Optional[str]) -> str:
    """Accepts the app's link (http://ip:port/?token=…), a ws:// URL, "ip" or "ip:port"."""
    if "://" not in url:
        url = "http://" + url
    u = urlparse(url)
    query = parse_qs(u.query)
    token = token or (query.get("token") or [None])[0]
    if not token:
        raise TokenError("A pairing token is needed: copy the full link from the app (Home → Connect), or pass token=…")
    scheme = "wss" if u.scheme in ("https", "wss") else "ws"
    host = u.hostname or "localhost"
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    port = u.port or DEFAULT_PORT
    return f"{scheme}://{host}:{port}{WS_PATH}?{urlencode({'token': token})}"


class Phone:
    """A connection to one phone. Create it with :func:`allsense.connect`."""

    def __init__(self, url: str, token: Optional[str] = None, *, name: str = "python",
                 timeout: float = 5.0, reconnect: bool = True, stall_timeout: float = 6.0):
        self.url = _ws_url(url, token)
        # If nothing at all arrives for this long (the phone answers clock sync every 2 s), the
        # link is treated as dead. Wi-Fi that vanishes doesn't close the socket, and without this
        # the network stack can take ~40 s to notice.
        self.stall_timeout = stall_timeout
        self._last_rx = time.monotonic()
        self.client_name = name
        self.timeout = timeout
        self.reconnect = reconnect
        self.device: str = ""
        self.protocol: Optional[int] = None
        self.app_version: str = ""
        self.clock = _ClockSync()
        self.last_error: Optional[str] = None
        self._catalog: List[StreamInfo] = []
        self._subs: Dict[str, Subscription] = {}
        self._commands: Dict[int, list] = {}  # id -> [Event, result]
        self._next_command = itertools.count(1)
        self._ws: Optional[ClientConnection] = None
        self._send_lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = threading.Event()

        # Phones on Wi-Fi power save can miss the first attempt; try a few times.
        for attempt in range(3):
            try:
                self._open()
                break
            except AllSenseError:
                if attempt == 2:
                    raise
                time.sleep(0.5)
        self._reader = threading.Thread(target=self._read_loop, name="allsense-reader", daemon=True)
        self._reader.start()
        deadline = time.monotonic() + timeout
        while not self._ready.wait(0.05):
            if self.closed or time.monotonic() > deadline:
                reason = self.last_error or ("Connected, but the phone didn't answer. Check that the link's token is "
                                             "current (the app can change it) and that this is an AllSense phone.")
                self.close()
                raise (TokenError if reason == TokenRejected else AllSenseError)(reason)
        self._syncer = threading.Thread(target=self._sync_loop, name="allsense-clock", daemon=True)
        self._syncer.start()

    # ---- public API ----

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def catalog(self) -> List[StreamInfo]:
        """Every stream the phone offers (including Advanced-only ones; see ``info.basic``)."""
        return list(self._catalog)

    def streams(self, all: bool = False) -> List[StreamInfo]:
        """Streams to show a user: the app's Basic set, or everything with ``all=True``."""
        return [s for s in self._catalog if all or s.basic]

    def find(self, name: str) -> StreamInfo:
        """Looks up a stream by name ("accelerometer"), category and name ("robot.accelerometer", for
        the robot-frame copy when the phone's mount is set), full topic, or stable ID."""
        for s in self._catalog:
            if name in (s.name, f"{s.category}.{s.name}", s.topic, s.id):
                return s
        close = difflib.get_close_matches(name, [s.name for s in self._catalog], n=3)
        hint = f" Did you mean {', '.join(close)}?" if close else " See phone.streams() for names."
        raise AllSenseError(f"No stream called {name!r} on {self.device}.{hint}")

    def subscribe(self, names: Union[str, Sequence[str]], rate: Optional[float] = None, *,
                  maxlen: int = 1000, callback: Optional[Callable[[Sample], None]] = None):
        """Starts streaming. Returns a :class:`Subscription` (or a list, for several names).

        ``rate`` is in Hz for continuous sensors (default 50, max 1000, limited by the sensor).
        ``callback(sample)``, if given, runs on the reader thread for every sample; keep it quick.
        """
        single = isinstance(names, str)
        subs = []
        for n in ([names] if single else names):
            info = self.find(n)
            old = self._subs.get(info.topic)
            if old is not None:
                old._closed = True
            sub = Subscription(self, info, rate, maxlen, callback)
            if not info.shared:
                log.warning("%s isn't shared over the network; it starts once you share it in the app (Home → Connect → Browser → Streams it may send)", info.name)
            self._subs[info.topic] = sub
            msg = {"op": "subscribe", "topics": [info.topic]}
            if rate is not None:
                msg["rate_hz"] = rate
            self._send(msg)
            subs.append(sub)
        return subs[0] if single else subs

    def command(self, name: str, /, timeout: Optional[float] = None, **args) -> dict:
        """Runs a command on the phone and returns its result (https://docs.allsense.otterbridge.org/PROTOCOL/#commands):
        ``phone.command("vibrate", ms=200)``, ``phone.command("say", text="Hello")``,
        ``phone.command("torch", on=True)``, ``phone.command("record", action="start", streams=["accelerometer"])``,
        ``phone.command("display", face="happy", text="Hi")``, ``phone.command("sound", name="alarm")`` (``name`` is positional-only, so
        commands can take a ``name`` argument).
        Raises :class:`AllSenseError` with the phone's reason if it fails (e.g. commands not allowed in the app)."""
        cid = next(self._next_command)
        slot = [threading.Event(), None]
        self._commands[cid] = slot
        try:
            self._send({"op": "command", "id": cid, "command": name, **args})
            if not slot[0].wait(timeout or self.timeout):
                raise AllSenseError(f"No answer to the {name!r} command (older app without commands?)")
        finally:
            self._commands.pop(cid, None)
        result = slot[1]
        if not result.get("ok"):
            raise AllSenseError(f"{name}: {result.get('error', 'failed')}")
        return result

    def microphones(self) -> List[StreamInfo]:
        """The phone's microphones (category "mic"). Their loudness is also a stream: ``<name>_level``."""
        return [s for s in self._catalog if s.kind == "audio"]

    def microphone(self, name: str = "mic", rate: int = 16000, chunk_seconds: float = 0.1):
        """Live audio (mono, 16-bit) from a microphone. Iterate the result; each item has
        ``.samples``, ``.t_ns`` and ``.rate`` (``.as_numpy()`` with NumPy)."""
        from .microphone import MicStream, mic_url

        info = self.find(name)
        if info.kind != "audio":
            raise AllSenseError(f"{info.name} isn't a microphone; microphones: {', '.join(m.name for m in self.microphones()) or 'none'}")
        return MicStream(mic_url(self.url, info.raw.get("url") or f"/mic/{info.name}.wav", rate), self.timeout + 5, chunk_seconds)

    def cameras(self) -> List[StreamInfo]:
        """The phone's cameras (category "camera"), in the catalog like other streams."""
        return [s for s in self._catalog if s.kind == "image"]

    def _camera_path(self, name: str) -> str:
        info = self.find(name)
        if info.kind != "image":
            raise AllSenseError(f"{info.name} isn't a camera; cameras: {', '.join(c.name for c in self.cameras()) or 'none'}")
        return info.raw.get("url") or f"/camera/{info.name}.mjpg"

    def camera(self, name: str, size: str = "640x480", fps: int = 15, quality: Optional[int] = None,
               zoom: Optional[float] = None, codec: str = "mjpeg", bitrate: Optional[int] = None):
        """Live frames from a camera (MJPEG over HTTP). Iterate the result; each item has ``.jpeg``.

        ``zoom`` is the camera's zoom ratio (see ``info.raw["zoom_range"]``); on a multi-lens camera
        the phone switches lenses, e.g. 0.6 for ultra-wide or 3 for telephoto. The first viewer of a
        camera sets size, quality and zoom.

        ``codec="h264"`` gives H.264 access units from the phone's encoder instead (``bitrate`` in
        bits/s, default 1 000 000): far less bandwidth; decode ``frame.data`` with PyAV or ffmpeg."""
        from .camera import CameraStream, camera_url

        path = self._camera_path(name)
        if codec == "h264":
            path = path.replace(".mjpg", ".h264")
        elif codec != "mjpeg":
            raise AllSenseError(f"codec is 'mjpeg' or 'h264', not {codec!r}")
        url = camera_url(self.url, path, size=size, fps=fps, quality=quality, zoom=zoom, bitrate=bitrate)
        return CameraStream(url, self.timeout + 8)

    def snapshot(self, name: str, size: str = "640x480", quality: Optional[int] = None, zoom: Optional[float] = None) -> bytes:
        """One JPEG from a camera."""
        from .camera import camera_url, snapshot

        path = self._camera_path(name).replace(".mjpg", ".jpg")
        return snapshot(camera_url(self.url, path, size=size, quality=quality, zoom=zoom), self.timeout + 8)

    def upload_sound(self, name_or_path: str, path: Optional[str] = None) -> dict:
        """Saves a sound file on the phone for ``phone.command("sound", name=…)``:
        ``upload_sound("beep.wav")`` (named "beep") or ``upload_sound("r2d2", "clips/r2.mp3")``.
        Formats: wav, mp3, ogg, m4a, aac, flac, opus; up to 10 MB. Needs Commands allowed in the app."""
        from .sounds import upload

        return upload(self.url, name_or_path, path, self.timeout + 30)

    def sounds(self) -> List[dict]:
        """The sounds ``sound`` can play: the phone's own (``built_in``) and saved ones."""
        from .sounds import list_sounds

        return list_sounds(self.url, self.timeout + 5)

    def refresh_catalog(self, timeout: Optional[float] = None) -> List[StreamInfo]:
        """Asks for the catalog again, e.g. after renaming something in the app."""
        self._ready.clear()
        self._send({"op": "catalog"})
        self._ready.wait(timeout or self.timeout)
        return self.catalog

    @property
    def round_trip_ms(self) -> Optional[float]:
        """Best recent network round trip to the phone."""
        return self.clock.round_trip_ms

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        for sub in list(self._subs.values()):
            with sub._cond:
                sub._cond.notify_all()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

    def __enter__(self) -> "Phone":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<Phone {self.device or '?'} {'closed' if self.closed else 'open'} streams={len(self._catalog)}>"

    # ---- internals ----

    def _open(self) -> None:
        try:
            # __enter__ opens the connection on websockets >= 17.1 (where calling connect() directly is
            # deprecated) and returns the already-open connection on older versions.
            self._ws = ws_connect(self.url, open_timeout=self.timeout, close_timeout=1, max_size=2 ** 22,
                                  compression=None).__enter__()
        except OSError as e:
            raise AllSenseError(f"Can't reach the phone at {self.url.split('?')[0]}: {e}. Is streaming started, and are you on the same network?") from e
        except Exception as e:
            raise AllSenseError(f"Connection refused by the phone ({e}). Check the token in the link.") from e
        self._last_rx = time.monotonic()
        self._send({"op": "hello", "client": self.client_name})

    def _send(self, msg: dict) -> None:
        ws = self._ws
        if ws is None or self.closed:
            return
        try:
            with self._send_lock:
                ws.send(json.dumps(msg))
        except ConnectionClosed:
            pass  # the reader thread handles reconnecting

    def _unsubscribe(self, sub: Subscription) -> None:
        if self._subs.get(sub.topic) is sub:
            del self._subs[sub.topic]
            self._send({"op": "unsubscribe", "topics": [sub.topic]})

    def _read_loop(self) -> None:
        backoff = itertools.chain([0.5, 1, 2], itertools.repeat(5))
        while not self.closed:
            try:
                for raw in self._ws:
                    self._last_rx = time.monotonic()
                    self._handle(json.loads(raw))
            except ConnectionClosed as e:
                if e.rcvd is not None and e.rcvd.code == 1008:
                    self.last_error = TokenRejected
                    log.error(self.last_error)
                    break
            except Exception:
                log.exception("Unexpected error reading from the phone")
            if self.closed or not self.reconnect:
                break
            # Reconnect and resume every subscription (Wi-Fi on a moving robot drops now and then).
            while not self.closed:
                delay = next(backoff)
                log.warning("Lost the phone; reconnecting in %.1f s", delay)
                time.sleep(delay)
                try:
                    self._open()
                    for sub in list(self._subs.values()):
                        msg = {"op": "subscribe", "topics": [sub.topic]}
                        if sub.rate is not None:
                            msg["rate_hz"] = sub.rate
                        self._send(msg)
                    backoff = itertools.chain([0.5, 1, 2], itertools.repeat(5))
                    log.info("Reconnected to %s", self.device)
                    break
                except AllSenseError:
                    continue
        self._closed.set()
        for sub in list(self._subs.values()):
            with sub._cond:
                sub._cond.notify_all()

    def _sync_loop(self) -> None:
        # A quick burst first for a good initial estimate, then every 2 s.
        for i in itertools.count():
            if self._closed.wait(0.2 if i < 5 else min(2.0, self.stall_timeout / 3)):
                return
            self._send({"op": "time_sync", "t0": time.monotonic_ns()})
            if self.reconnect and time.monotonic() - self._last_rx > self.stall_timeout:
                log.warning("Nothing from the phone for %.0f s; reconnecting", self.stall_timeout)
                self._last_rx = time.monotonic()
                self._drop_connection()

    def _drop_connection(self) -> None:
        """Ends a dead connection right away, so the reader thread reconnects."""
        ws = self._ws
        if ws is None:
            return
        try:
            ws.socket.shutdown(socket.SHUT_RDWR)  # unblocks the reader immediately
        except OSError:
            pass
        try:
            ws.close()
        except Exception:
            pass

    def _handle(self, m: dict) -> None:
        op = m.get("op")
        if op == "data":
            sub = self._subs.get(m["topic"])
            if sub is None:
                return
            t = int(m["t"])
            sub._push(Sample(
                topic=m["topic"], seq=int(m.get("seq", 0)), t=t, time=self.clock.to_unix(t),
                values=tuple(m["v"]), accuracy=int(m.get("acc", 0)), fields=sub.info.fields,
            ))
        elif op == "time_sync":
            t3 = time.monotonic_ns()
            if isinstance(m.get("t0"), (int, float)):
                self.clock.add(int(m["t0"]), int(m["t1"]), int(m["t2"]), t3)
        elif op == "hello":
            self.device = m.get("device", "")
            self.protocol = m.get("protocol")
            self.app_version = m.get("app_version", "")
            if m.get("boot_to_unix_ns") is not None:
                self.clock.boot_to_unix_ns = int(m["boot_to_unix_ns"])
            if self.protocol not in (None, 0):
                log.warning("Phone speaks protocol %s; this library knows 0", self.protocol)
        elif op == "catalog":
            self._catalog = [StreamInfo.from_json(s) for s in m.get("streams", [])]
            # Sent again when the user changes names or sharing: keep subscriptions' info current.
            by_id = {s.id: s for s in self._catalog if s.id}
            for sub in list(self._subs.values()):
                fresh = by_id.get(sub.info.id)
                if fresh is not None and fresh.topic == sub.info.topic:
                    sub.info = fresh
            self.device = m.get("device", self.device)
            self._ready.set()
        elif op == "status":
            sub = self._subs.get(m.get("topic", ""))
            if sub is not None:
                sub.status = m.get("reason") or m.get("state")
                sub.state = m.get("state")
                if m.get("state") in ("refused", "not_shared"):
                    log.warning("%s: %s", sub.info.name, sub.status)
        elif op == "subscribed":
            # Also sent when the user shares a stream that was waiting for it.
            sub = self._subs.get(m.get("topic", ""))
            if sub is not None:
                sub.status = None
                sub.state = "streaming"
        elif op == "command_result":
            slot = self._commands.get(m.get("id"))
            if slot is not None:
                slot[1] = m
                slot[0].set()
        elif op == "ping":
            # Link test (the protocol's "Link test"): answer at once with the same id.
            self._send({"op": "pong", "id": m.get("id")})
        elif op == "error":
            self.last_error = m.get("message")
            log.warning("Phone says: %s", self.last_error)
        # "fill" (link test padding) and unknown ops need no action.


def as_arrays(samples: Sequence[Sample]):
    """``(times, values)`` as NumPy arrays: shape (n,) Unix seconds and (n, k) floats (None → NaN)."""
    import numpy as np

    times = np.fromiter((s.time for s in samples), dtype=float, count=len(samples))
    values = np.array([[float("nan") if v is None else v for v in s.values] for s in samples], dtype=float)
    return times, values


def connect(url: Optional[str] = None, token: Optional[str] = None, *, name: str = "python",
            timeout: float = 5.0, reconnect: bool = True, stall_timeout: float = 6.0,
            wait: Optional[float] = None) -> Phone:
    """Connects to a phone.

    ``url`` is the link from the app (Home → Connect) (it contains the token). Without a URL, the first
    phone found on the local network is used (needs ``pip install allsense[discovery]``) and
    ``token`` is required.

    Once connected, the client reconnects by itself if the phone goes away. ``wait`` covers the
    start: keep trying for that many seconds while the phone isn't reachable yet (``float("inf")``
    for a robot that starts before the phone). A rejected token raises :class:`TokenError` at once.
    """
    if wait:
        deadline = time.monotonic() + wait
        while True:
            try:
                return connect(url, token, name=name, timeout=timeout, reconnect=reconnect, stall_timeout=stall_timeout)
            except TokenError:
                raise
            except AllSenseError as e:
                if time.monotonic() >= deadline:
                    raise
                log.warning("%s Retrying…", e)
                time.sleep(min(2.0, max(0.0, deadline - time.monotonic())))
    if url is None:
        from .discovery import discover

        found = discover(timeout=timeout)
        if not found:
            raise AllSenseError("No AllSense phone found on this network. Pass the link from the app (Home → Connect) instead.")
        url = found[0].url
    return Phone(url, token, name=name, timeout=timeout, reconnect=reconnect, stall_timeout=stall_timeout)
