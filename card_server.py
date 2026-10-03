# -*- coding: utf-8 -*-
"""分享卡片云服务（demo）：接收上传、静态托管、只保留最近 5 张。

仅用标准库，直接运行：
    python3 card_server.py
默认监听 0.0.0.0:8085，图片存 /opt/card-data。
"""

import hashlib
import json
import os
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(os.environ.get("CARD_DATA_DIR", "/opt/card-data"))
ROOT.mkdir(parents=True, exist_ok=True)
TOKEN = os.environ.get("CARD_UPLOAD_TOKEN", "haining-demo-token")
KEEP = int(os.environ.get("CARD_KEEP", "5"))
MAX_BYTES = 8 * 1024 * 1024
PORT = int(os.environ.get("CARD_PORT", "8085"))

# 速率限制：每个 IP 每分钟最多上传次数 / 两次上传最小间隔（秒）
RATE_PER_MIN = int(os.environ.get("CARD_UPLOAD_PER_MIN", "10"))
MIN_INTERVAL = float(os.environ.get("CARD_UPLOAD_MIN_INTERVAL", "1.0"))
# 每个 IP 每分钟最多下载次数（防止被当图床刷流量）
GET_PER_MIN = int(os.environ.get("CARD_GET_PER_MIN", "120"))

_hits = {}
_hits_lock = threading.Lock()


def _rate_ok(ip: str, per_min: int, min_interval: float = 0.0) -> bool:
    now = time.monotonic()
    with _hits_lock:
        times = [t for t in _hits.get(ip, []) if now - t < 60]
        if min_interval and times and now - times[-1] < min_interval:
            return False
        if len(times) >= per_min:
            return False
        times.append(now)
        _hits[ip] = times
        return True


def _is_image(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"


class Handler(BaseHTTPRequestHandler):
    timeout = 15

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path):
        if not path.exists() or not path.is_file():
            return self._json(404, {"error": "not found"})
        data = path.read_bytes()
        mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        clean = self.path.split("?", 1)[0]
        if clean == "/health":
            return self._json(200, {"ok": True})
        if clean.startswith("/cards/"):
            if not _rate_ok(self.client_address[0], GET_PER_MIN):
                return self._json(429, {"error": "too many requests"})
            return self._serve_file(ROOT / pathlib.Path(clean).name)
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        clean = self.path.split("?", 1)[0]
        if clean != "/upload":
            return self._json(404, {"error": "not found"})
        if self.headers.get("X-Upload-Token") != TOKEN:
            return self._json(401, {"error": "bad token"})
        if not _rate_ok(self.client_address[0], RATE_PER_MIN, MIN_INTERVAL):
            return self._json(429, {"error": "rate limited"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BYTES:
            return self._json(413, {"error": "too large"})
        data = self.rfile.read(length)
        if not _is_image(data):
            return self._json(400, {"error": "not an image"})

        name = ""
        query = self.path.split("?", 1)[1] if "?" in self.path else ""
        for part in query.split("&"):
            if part.startswith("name="):
                name = part[5:]
        ext = ".jpg" if name.lower().endswith((".jpg", ".jpeg")) else ".png"

        digest = hashlib.sha1(data).hexdigest()[:12]
        fname = digest + ext
        (ROOT / fname).write_bytes(data)
        self._prune()

        host = self.headers.get("Host", "121.40.76.121:8085")
        return self._json(200, {"url": f"http://{host}/cards/{fname}"})

    def _prune(self):
        files = sorted(ROOT.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[KEEP:]:
            try:
                old.unlink()
            except OSError:
                pass

    def log_message(self, *args):
        print("[card-server]", self.address_string(), *args)


if __name__ == "__main__":
    print(f"[card-server] serving on 0.0.0.0:{PORT}, data={ROOT}, keep={KEEP}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
