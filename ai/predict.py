"""Pakai model nominal uang (ONNX) tanpa ultralytics/torch: hanya onnxruntime + opencv.

Dependency ringan (hemat disk & RAM, cocok untuk server CPU):
    onnxruntime, opencv-python-headless, numpy, pillow

Command line:
    .venv/Scripts/python predict.py foto.jpg
    .venv/Scripts/python predict.py folder_foto/ --save hasil/      # simpan foto berkotak

Dari kode Python lain (mis. server FastAPI):
    from predict import MoneyDetector
    det = MoneyDetector()                      # default: models/uang_v2.onnx di CPU
    hasil = det.predict("foto.jpg")            # path, bytes JPEG, array BGR, atau PIL.Image
    hasil["status"]   # "ok" | "ragu" | "kosong"
    hasil["total"]    # jumlah rupiah dari uang yang yakin (confidence >= ambang)
    hasil["items"]    # list dict per uang: kelas, nominal, jenis, confidence, yakin, box
    hasil["result"]   # gambar BGR hasil anotasi kotak + label
"""
import argparse
import glob
import os

import cv2
import numpy as np
import onnxruntime as ort

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = os.path.join(HERE, "models", "uang_v2.onnx")
DEFAULT_THR = 0.85
DEFAULT_CONF = 0.25
DEFAULT_IMGSZ = 640

NOMINAL = {"k1000": 1000, "k2000": 2000, "k5000": 5000, "k10000": 10000, "k20000": 20000,
           "k50000": 50000, "k100000": 100000,
           "c100": 100, "c200": 200, "c500": 500, "c1000": 1000, "lain": 0}
IMG_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def letterbox(img, new_shape=(DEFAULT_IMGSZ, DEFAULT_IMGSZ)):
    h, w = img.shape[:2]
    r = min(new_shape[0] / h, new_shape[1] / w)
    nh, nw = round(h * r), round(w * r)
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((new_shape[0], new_shape[1], 3), 114, dtype=np.uint8)
    top, left = (new_shape[0] - nh) // 2, (new_shape[1] - nw) // 2
    canvas[top:top + nh, left:left + nw] = resized
    return canvas, r, top, left


def nms(boxes, scores, iou_thr=0.45):
    idx = cv2.dnn.NMSBoxes(boxes.tolist(), scores.tolist(), score_threshold=0, nms_threshold=iou_thr)
    return [] if idx is None else np.asarray(idx).reshape(-1).tolist()


class MoneyDetector:
    def __init__(self, model=DEFAULT_MODEL, thr=DEFAULT_THR, conf=DEFAULT_CONF,
                 imgsz=DEFAULT_IMGSZ, device="cpu"):
        providers = ["CPUExecutionProvider"] if device == "cpu" else ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self.sess = ort.InferenceSession(model, providers=providers)
        names = self.sess.get_modelmeta().custom_metadata_map.get("names", "")
        self.names = {}
        for item in names.strip("{}").split(","):
            if ":" in item:
                k, v = item.split(":", 1)
                self.names[int(k.strip())] = v.strip().strip("'\"")
        self.thr, self.conf, self.imgsz = thr, conf, imgsz

    @staticmethod
    def _load(image):
        if isinstance(image, (bytes, bytearray)):            # body HTTP POST dari ESP32-CAM
            image = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("bytes bukan gambar yang valid")
            return image
        if isinstance(image, (str, os.PathLike)):            # path file
            image = cv2.imread(str(image))
            if image is None:
                raise ValueError(f"tidak bisa membaca gambar: {image}")
            return image
        try:                                                # PIL.Image
            import PIL.Image
            if isinstance(image, PIL.Image.Image):
                return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        except ImportError:
            pass
        return image

    def predict(self, image):
        img = self._load(image)
        if img is None:
            raise ValueError("gambar tidak valid")
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        h0, w0 = img.shape[:2]
        canvas, r, top, left = letterbox(img, (self.imgsz, self.imgsz))
        blob = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0   # BGR->RGB, CHW, [0,1]
        out = self.sess.run(None, {self.sess.get_inputs()[0].name: blob})[0][0]          # [16, 8400]

        boxes, scores, classes = [], [], []
        for i in range(out.shape[1]):
            row = out[:, i]
            cls_id = int(np.argmax(row[4:]))
            conf = float(row[4 + cls_id])
            if conf < self.conf:
                continue
            cx, cy, w, h = row[0:4]
            x1, y1, x2, y2 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
            boxes.append([x1, y1, x2 - x1, y2 - y1])
            scores.append(conf)
            classes.append(cls_id)
        boxes, scores, classes = map(np.asarray, (boxes, scores, classes))
        keep = nms(boxes, scores) if len(scores) else []

        items = []
        for i in keep:
            x1, y1, x2, y2 = boxes[i]
            x1, y1 = (x1 - left) / r, (y1 - top) / r                    # skala balik ke ukuran asli
            x2, y2 = (x2 - left) / r, (y2 - top) / r
            kelas = self.names.get(int(classes[i]), "lain")
            items.append({
                "kelas": kelas,
                "nominal": NOMINAL[kelas],
                "jenis": "koin" if kelas.startswith("c") else ("lain" if kelas == "lain" else "kertas"),
                "confidence": round(float(scores[i]), 4),
                "yakin": float(scores[i]) >= self.thr,
                "box": [round(max(0, x1), 1), round(max(0, y1), 1),
                        round(min(w0, x2), 1), round(min(h0, y2), 1)],
            })
        items.sort(key=lambda d: (d["box"][1], d["box"][0]))
        if not items:
            status = "kosong"
        elif all(d["yakin"] for d in items):
            status = "ok"                                     # langsung catat
        else:
            status = "ragu"                                   # goyang nampan + foto ulang, lalu Gemini
        total = sum(d["nominal"] for d in items if d["yakin"])

        annotated = img.copy()
        for d in items:
            x1, y1, x2, y2 = map(int, d["box"])
            color = (0, 200, 0) if d["yakin"] else (0, 0, 255)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label = f"{d['kelas']} {d['confidence']:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(annotated, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(annotated, label, (x1 + 2, y1 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        return {"status": status, "total": total, "items": items, "result": annotated}


def expand(paths):
    for p in paths:
        if os.path.isdir(p):
            yield from sorted(f for f in glob.glob(os.path.join(p, "*")) if f.lower().endswith(IMG_EXT))
        else:
            yield p


def main():
    ap = argparse.ArgumentParser(description="Deteksi nominal uang Rupiah di foto (ONNX, CPU)")
    ap.add_argument("source", nargs="+", help="file foto atau folder")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="path model .onnx")
    ap.add_argument("--thr", type=float, default=DEFAULT_THR, help="ambang yakin (0.85)")
    ap.add_argument("--conf", type=float, default=DEFAULT_CONF, help="ambang deteksi awal (0.25)")
    ap.add_argument("--save", help="folder untuk menyimpan foto berkotak")
    args = ap.parse_args()

    det = MoneyDetector(args.model, thr=args.thr, conf=args.conf)
    if args.save:
        os.makedirs(args.save, exist_ok=True)
    for path in expand(args.source):
        h = det.predict(path)
        print(f"\n{os.path.basename(path)}  ->  status={h['status']}  total=Rp{h['total']:,}".replace(",", "."))
        for d in h["items"]:
            print(f"   {d['kelas']:<8} conf {d['confidence']:.2f}  {'yakin' if d['yakin'] else 'RAGU'}")
        if args.save:
            cv2.imwrite(os.path.join(args.save, os.path.basename(path)), h["result"])


if __name__ == "__main__":
    main()