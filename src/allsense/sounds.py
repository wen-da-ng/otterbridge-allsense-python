"""Sounds saved on the phone for the ``sound`` command (https://docs.allsense.otterbridge.org/PROTOCOL/#commands).

    phone.upload_sound("beep.wav")              # saved as "beep"
    phone.upload_sound("r2d2", "clips/r2.mp3")  # any name
    phone.command("sound", name="beep")         # play it (Commands must be allowed in the app)
    phone.sounds()                              # [{"name": "alarm", "built_in": True}, {"name": "beep", "format": "wav", …}]
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import List, Optional

from .camera import MAX_ERROR_BYTES, camera_url, read_limited
from .client import AllSenseError, redact

MAX_REPLY_BYTES = 4 * 1024 * 1024

FORMATS = ("wav", "mp3", "ogg", "m4a", "aac", "flac", "opus")


def upload(ws_url: str, name_or_path: str, path: Optional[str], timeout: float) -> dict:
    if path is None:
        path = name_or_path
        name = os.path.splitext(os.path.basename(path))[0]
    else:
        name = name_or_path
    fmt = os.path.splitext(path)[1].lstrip(".").lower()
    if fmt not in FORMATS:
        raise AllSenseError(f"Sound formats: {', '.join(FORMATS)}; {path!r} isn't one")
    with open(path, "rb") as f:
        data = f.read()
    req = urllib.request.Request(camera_url(ws_url, f"/sounds/{name}.{fmt}"), data=data, method="PUT",
                                 headers={"Content-Type": "application/octet-stream"})
    return json.loads(_call(req, timeout))


def list_sounds(ws_url: str, timeout: float) -> List[dict]:
    return json.loads(_call(urllib.request.Request(camera_url(ws_url, "/sounds")), timeout))["sounds"]


def _call(req: urllib.request.Request, timeout: float) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return read_limited(r, MAX_REPLY_BYTES, "The phone's reply")
    except urllib.error.HTTPError as e:
        # The phone explains (commands off, bad name, too big…).
        raise AllSenseError(f"Sounds: {e.read(MAX_ERROR_BYTES).decode(errors='replace').strip() or e.reason}") from None
    except OSError as e:
        raise AllSenseError(f"Can't reach the phone at {req.full_url.split('?')[0]}: {redact(e)}") from None
