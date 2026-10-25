# allsense (Python)

Stream sensors from an Android phone running **OtterBridge AllSense** into Python: for robots, experiments, data logging, or classes.

```python
import allsense

phone = allsense.connect("http://192.168.1.20:8765/?token=abcd")  # link from the app (Home → Connect)

with phone.subscribe("accelerometer", rate=100) as accel:
    for s in accel:
        print(s.time, s["x"], s["y"], s["z"])
```

## Install

```
pip install "allsense[all]"        # from PyPI (once published)
pip install "allsense[all] @ git+https://github.com/wen-da-ng/otterbridge-allsense-python"   # from GitHub
```

`[all]` adds NumPy helpers and network discovery. The core only needs `websockets`.

## Getting started

1. In the AllSense app, tap **Start streaming** on Home.
2. Copy the link from **Connect → Programming** (`http://<phone-ip>:8765/?token=…`). It contains the pairing token.
3. Your computer must be on the same Wi-Fi, connected to the phone's hotspot, or connected by USB:
   - **USB tethering:** pick "USB tethering" in the app to get its link.
   - **adb:** run `adb forward tcp:8766 tcp:8765`, then use `http://localhost:8766/?token=…`.

Without a link, `allsense.connect(token="…")` finds the phone on the network (`[discovery]`); with several phones it stops and lists them (`device="rover"` picks one). Prefer the link: discovery isn't authenticated, so a computer on the same network posing as a phone would receive the token.

## Command line

```
allsense discover                               # phones streaming on this network
allsense list   "http://…/?token=…"             # stream names (--all for everything)
allsense echo   "http://…/?token=…" accelerometer gyroscope --rate 100
allsense echo   "http://…/?token=…" accelerometer --csv --count 1000 > accel.csv
```

## Guide

**Names.** Streams use the names you give them in the app (Rename). `phone.subscribe("mag_left")` also accepts the full topic (`allsense.rover.sensors.mag_left`) or the stable hardware `id`.

```python
for info in phone.streams():          # phone.streams(all=True) adds vendor-specific sensors, wake-up copies and raw variants
    print(info.name, info.type, info.fields, info.unit, info.max_rate_hz)
```

**Reading samples.** Use whichever style fits your program:

```python
accel = phone.subscribe("accelerometer", rate=200)

s = accel.get(timeout=1)    # wait for the next sample
latest = accel.latest       # newest sample, never waits (control loops)
batch = accel.read()        # everything queued since the last call
for s in accel: ...         # loop forever

accel.set_rate(50)
accel.close()
```

Several at once, or with a callback (runs on a background thread; keep it quick):

```python
accel, gyro = phone.subscribe(["accelerometer", "gyroscope"], rate=100)
phone.subscribe("light", callback=lambda s: print("lux", s[0]))
```

**Samples.** `s.values` is a tuple; `s["x"]` or `s[0]` picks one value, and `s.as_dict()` gives `{"x": …}`.
- `s.t`: the phone's **hardware** timestamp (ns on its boot clock), the moment the sensor measured.
- `s.time`: that same moment as Unix seconds on **your computer's clock**, using automatic clock sync. Use it to line up phone data with your robot's other data. `phone.round_trip_ms` shows the sync quality.
- For intervals between samples (rates, debouncing), use `s.t`: it comes from one clock and never jumps when the sync improves.
- `s.seq` counts up; `accel.dropped` counts samples lost on the network or not read in time. If your code is slower than the stream, the **oldest** queued samples are dropped, so you always get fresh data.

**NumPy.**

```python
times, values = allsense.as_arrays(accel.read())   # shapes (n,) and (n, 3)
```

**Streams you can't receive.** `sub.state` says what the phone said about a subscription:

| `sub.state` | Meaning | What happens |
|---|---|---|
| `"streaming"` | Data is flowing | |
| `"armed"` | A one-shot sensor is waiting for its event | Data comes when it fires |
| `"refused"` | Android won't deliver this sensor to apps | Pick another stream |
| `"not_shared"` | The user doesn't share it over the network (app: Home → Connect → Browser → Streams it may send) | It **starts by itself** once they share it; no resubscribe |
| `None` | No answer yet | |

`sub.status` holds the phone's message for people, and `sub.info.shared` (also in `phone.catalog`) stays current when the user changes sharing. "Network" covers every WebSocket client: Wi-Fi, hotspot, USB tethering and `adb forward`.

**Reliability.**
- If Wi-Fi drops, the client reconnects and resumes your subscriptions automatically (`reconnect=False` to disable).
- A link that goes silent without closing, which is what vanishing Wi-Fi looks like, is detected after `stall_timeout` seconds (default 6; `connect(..., stall_timeout=2)` notices sooner).
- That covers drops after the first connection. If your program may start before the phone, use `connect(url, wait=float("inf"))` (or a number of seconds): it keeps trying until the phone answers.
- A missing or wrong token raises `allsense.TokenError` (a kind of `AllSenseError`) at once: retrying won't help, the user needs the current link.
- `get()` and `read(timeout=…)` return early if the phone closes the connection for good; `phone.closed` is then true.

**Cameras.** Video comes over HTTP (MJPEG), with the same link:

```python
phone.cameras()                                   # e.g. [StreamInfo(name='back_24mm', …), …]
open("photo.jpg", "wb").write(phone.snapshot("back_24mm"))
with phone.camera("back_24mm", size="640x480", fps=10) as cam:
    for frame in cam:                             # frame.jpeg (bytes), frame.t_ns, frame.size
        ...                                       # OpenCV: cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), 1)
```

H.264 instead of MJPEG (about a fifth of the bandwidth): `phone.camera("back_22mm", codec="h264")` gives access units (`frame.data`, `frame.keyframe`) to decode with PyAV or ffmpeg.

**Microphones.** Audio (mono 16-bit) over HTTP; loudness alone is the stream `<name>_level`:

```python
with phone.microphone("mic", rate=16000) as mic:
    for chunk in mic:                             # chunk.samples (int16), chunk.t_ns, chunk.rate; chunk.as_numpy()
        ...
```

**Commands.** If allowed in the app (Robot → Robot can control the phone):

```python
phone.command("vibrate", ms=300)
phone.command("say", text="Battery low")
phone.command("record", action="start", streams=["accelerometer", "position"])
phone.command("display", face="happy", text="Charging")
phone.upload_sound("beep.wav"); phone.command("sound", name="beep")          # your own sounds
phone.command("camera", camera="back", ev=-1, focus="infinity")              # exposure, focus, white balance
```

**Recordings.** Files the app saved ([format](https://docs.allsense.otterbridge.org/RECORDING/)), from their folder or .zip:

```python
rec = allsense.load_recording("allsense_phone_20261017-191502.zip")
times, values = rec["accelerometer"].arrays()
```

For Foxglove or ROS 2, convert it to MCAP (`pip install "allsense[mcap]"`): `allsense mcap <recording.zip> [--ros2]`, or `allsense.export_mcap(...)`.

## Examples

See [`examples/`](examples): print values, log to CSV, and a live matplotlib plot.

## Protocol

The client speaks the AllSense protocol over WebSocket ([spec](https://docs.allsense.otterbridge.org/PROTOCOL/), [otterbridge-allsense-protocol](https://github.com/wen-da-ng/otterbridge-allsense-protocol)). Version 0 is experimental and may still change.

## License

MIT. This library and the protocol are MIT, so you can use them in any project. The AllSense app is a free download from [otterbridge-allsense](https://github.com/wen-da-ng/otterbridge-allsense).
