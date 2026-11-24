"""Server AI Kotak Amal: terima foto dari ESP32-CAM, deteksi nominal otomatis.

Pengganti photo_receiver.py (port dan URL sama):
    POST /capture?cam=top   <- dari ESP32-CAM (cam.ino tidak perlu diubah)

Setiap foto masuk langsung dideteksi:
    - photos/<cam>/<waktu>.jpg     foto asli
    - hasil/<cam>/<waktu>.jpg      foto berkotak (label + confidence)
    - hasil/log.txt                ringkasan per deteksi (terminal juga menampilkan)
    - hasil/latest.json            hasil deteksi terakhir (untuk dashboard/browser)

Jalankan (dari folder ai/, pakai venv AI):
    .venv/bin/python ../server.py            # Linux
    .venv/Scripts/python ../server.py        # Windows

Browser: http://localhost:8000  -> foto terakhir + hasil deteksi terakhir.
"""
import json
import socket
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "ai"))

from predict import MoneyDetector  # noqa: E402

PORT = 8000
PHOTO_DIR = HERE / "photos"
OUT_DIR = HERE / "hasil"
LOG_FILE = OUT_DIR / "log.txt"


def now():
    return datetime.now()


def latest_photo():
    files = sorted(PHOTO_DIR.glob("*/*.jpg"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def append_log(line):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


class Handler(BaseHTTPRequestHandler):
    detector = None

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/capture":
            self.send_error(404)
            return
        cam = parse_qs(url.query).get("cam", ["top"])[0].replace("/", "_")
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))

        ts = now()
        folder = PHOTO_DIR / cam
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (f"{ts:%Y%m%d_%H%M%S_%f}"[:-3] + ".jpg")
        path.write_bytes(data)

        hasil = None
        try:
            hasil = self.detector.predict(data)
        except Exception as e:
            hasil = {"status": "error", "total": 0, "items": [], "error": str(e), "result": None}

        # ringkasan baris: waktu cam status total n per-uang
        ringkas = " ".join(f"{d['kelas']}@{d['confidence']:.2f}" for d in hasil["items"])
        line = (f"{ts:%H:%M:%S} cam={cam} status={hasil['status']} "
                f"total=Rp{hasil['total']:,} n={len(hasil['items'])} {ringkas}".replace(",", "."))
        print(line)
        append_log(f"{ts:%Y-%m-%d %H:%M:%S} | {line}")

        # foto berkotak + JSON
        if hasil.get("result") is not None:
            import cv2
            of = OUT_DIR / cam
            of.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(of / path.name), hasil["result"])
        latest = {"waktu": ts.isoformat(), "cam": cam, "foto": str(path), **{k: hasil[k] for k in ("status", "total", "items")}}
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")

        count = len(list(folder.glob("*.jpg")))
        body = json.dumps({"cam": cam, "saved": path.name, "count": count,
                           "status": hasil["status"], "total": hasil["total"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/latest.jpg"):
            p = latest_photo()
            if not p:
                self.send_error(404, "no photo yet")
                return
            self._send_bytes("image/jpeg", p.read_bytes())
            return
        if self.path.startswith("/latest.json"):
            p = OUT_DIR / "latest.json"
            if not p.exists():
                self.send_error(404, "no result yet")
                return
            self._send_bytes("application/json", p.read_bytes())
            return
        p = latest_photo()
        info = f"Foto terakhir: {p.parent.name}/{p.name}" if p else "Belum ada foto."
        res = ""
        lp = OUT_DIR / "latest.json"
        if lp.exists():
            d = json.loads(lp.read_text(encoding="utf-8"))
            det = " | ".join(f"{i['kelas']} Rp{i['nominal']:,} ({i['confidence']:.2f})".replace(",", ".") for i in d["items"])
            res = (f"<p>status=<b>{d['status']}</b> &nbsp; total=<b>Rp{d['total']:,}</b></p>"
                   f"<p>{det or 'tidak ada uang'}</p>".replace(",", "."))
        body = (f"<html><head><meta http-equiv=refresh content=2><title>Kotak Amal</title></head>"
                f"<body style='font-family:sans-serif;padding:16px'><h2>Kotak Amal: foto + deteksi</h2><p>{info}</p>{res}"
                + (f"<img src='/latest.jpg?{p.stat().st_mtime}' style='max-width:100%'>" if p else "")
                + "</body></html>").encode()
        self._send_bytes("text/html", body)

    def _send_bytes(self, ctype, data):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    PHOTO_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    try:
        Handler.detector = MoneyDetector()
        print(f"AI dimuat: model uang_v2.onnx. Mulai mendengarkan di port {PORT}...")
    except Exception as e:
        print(f"GAGAL memuat AI: {e}")
        print("Jalankan dari folder ai/ pakai venv AI (.venv/bin/python ../server.py)")
        sys.exit(1)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close()
    except OSError:
        ip = "<ip-laptop>"
    print(f"Server jalan. Di cam.ino: SERVER = \"http://{ip}:{PORT}\"")
    print(f"Foto: {PHOTO_DIR}  |  Hasil: {OUT_DIR}  |  Browser: http://localhost:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()