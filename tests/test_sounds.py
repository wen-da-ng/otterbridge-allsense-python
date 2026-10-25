"""phone.upload_sound / phone.sounds over HTTP. The fake phone's server only does GET, so a tiny
HTTP server plays the phone's /sounds endpoints here."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from allsense import AllSenseError
from allsense.sounds import list_sounds, upload


class _Phone(BaseHTTPRequestHandler):
    saved = {}

    def _reply(self, code, body):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):
        path, _, query = self.path.partition("?")
        if query != "token=t0k":
            return self._reply(401, "Unauthorized")
        name, fmt = path.removeprefix("/sounds/").rsplit(".", 1)
        if name == "alarm":
            return self._reply(400, '"alarm" is one of the phone\'s own sounds')
        data = self.rfile.read(int(self.headers["Content-Length"]))
        self.saved[name] = (fmt, data)
        self._reply(200, {"ok": True, "name": name, "format": fmt, "bytes": len(data)})

    def do_GET(self):
        self._reply(200, {"sounds": [{"name": n, "format": f, "bytes": len(d)} for n, (f, d) in self.saved.items()]})

    def log_message(self, *args):
        pass


@pytest.fixture
def ws_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Phone)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"ws://127.0.0.1:{server.server_address[1]}/ws?token=t0k"
    server.shutdown()


def test_upload_names_the_sound_after_the_file(ws_url, tmp_path):
    f = tmp_path / "beep.WAV"
    f.write_bytes(b"RIFF1234WAVE")
    assert upload(ws_url, str(f), None, 5) == {"ok": True, "name": "beep", "format": "wav", "bytes": 12}
    assert _Phone.saved["beep"] == ("wav", b"RIFF1234WAVE")
    assert upload(ws_url, "r2d2", str(f), 5)["name"] == "r2d2"
    assert {s["name"] for s in list_sounds(ws_url, 5)} >= {"beep", "r2d2"}


def test_upload_errors_come_from_the_phone(ws_url, tmp_path):
    f = tmp_path / "x.mp3"
    f.write_bytes(b"ID3")
    with pytest.raises(AllSenseError, match="own sounds"):
        upload(ws_url, "alarm", str(f), 5)
    with pytest.raises(AllSenseError, match="formats"):
        upload(ws_url, str(tmp_path / "notes.txt"), None, 5)
