"""
Simple photo receiver for the ESP32-CAM. No AI, no MQTT, nothing to pip install (Python 3 only).

The camera sends every photo to  POST http://<laptop-ip>:8000/capture?cam=top
This script saves them to server/photos/top/20261123_190512_123.jpg and prints a line per photo.
Open http://localhost:8000 in a browser to see the latest photo (refreshes every 2 s).

Run (from the server/ folder):   python photo_receiver.py
Laptop and ESP32-CAM must be on the same 2.4 GHz hotspot. If Windows asks, allow Python
through the firewall (Private networks). Later, server.py replaces this script (same port and URL).
"""
import socket
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8000
DIR = Path(__file__).resolve().parent / "photos"


def latest():
    files = sorted(DIR.glob("*/*.jpg"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/capture":
            self.send_error(404)
            return
        cam = parse_qs(url.query).get("cam", ["top"])[0].replace("/", "_")
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        folder = DIR / cam
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (f"{datetime.now():%Y%m%d_%H%M%S_%f}"[:-3] + ".jpg")
        path.write_bytes(data)
        count = len(list(folder.glob("*.jpg")))
        print(f"{datetime.now():%H:%M:%S}  photo from '{cam}': {len(data):,} bytes -> {path.name}  (total {count})")
        body = f'{{"cam":"{cam}","saved":"{path.name}","count":{count}}}'.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/latest.jpg"):
            p = latest()
            if not p:
                self.send_error(404, "no photo yet")
                return
            data = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        p = latest()
        info = f"Latest: {p.parent.name}/{p.name}" if p else "No photo yet. Drop a coin in the box."
        body = (f"<html><head><meta http-equiv=refresh content=2><title>Kotak Amal photos</title></head>"
                f"<body style='font-family:sans-serif;padding:16px'><h2>Kotak Amal: photos</h2><p>{info}</p>"
                + (f"<img src='/latest.jpg?{p.stat().st_mtime}' style='max-width:100%'>" if p else "")
                + "</body></html>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the terminal clean: one line per photo is printed above
        pass


if __name__ == "__main__":
    DIR.mkdir(exist_ok=True)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
    except OSError:
        ip = "<laptop-ip>"
    print(f"Photo receiver running. In esp32cam_capture.ino set:  SERVER = \"http://{ip}:{PORT}\"")
    print(f"Photos are saved in {DIR}. Live view: http://localhost:{PORT}   (Ctrl+C to stop)")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
