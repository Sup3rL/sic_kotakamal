# AI Kotak Amal (lokal, untuk uji coba)

Deteksi nominal uang Rupiah dari foto, memakai model `models/uang_v2.onnx` (12 kelas:
k1000..k100000, c100..c1000, lain). Implementasi memakai **onnxruntime** saja (tanpa
ultralytics/torch), jadi install cepat dan hemat RAM/disk — cocok untuk laptop server CPU.

## Isi folder

```
ai/
├── predict.py          # detektor: CLI + class MoneyDetector
├── requirements.txt    # onnxruntime, opencv, numpy, pillow
├── setup.bat           # sekali jalan: buat venv + install dependency (Windows)
└── models/
    └── uang_v2.onnx    # model YOLO26n (9,5 MB)
```

## Setup (sekali saja, di Windows)

Klik dua kali `setup.bat`, atau dari cmd:

```bat
cd C:\Users\mutaq\Documents\charitybox\sic_kotakamal\ai
setup.bat
```

## Pakai dari terminal

```bat
.venv\Scripts\python predict.py foto.jpg
.venv\Scripts\python predict.py folder_foto\ --save hasil\     :: simpan foto berkotak
.venv\Scripts\python predict.py ..\photos\top --save hasil\    :: foto asli dari ESP32-CAM
```

Contoh output:

```
foto.jpg  ->  status=ok  total=Rp10.000
   k10000  conf 0.95  yakin
```

- `status=ok`    semua confidence >= 0,85 → langsung catat
- `status=ragu`  ada yang < 0,85 → goyang nampan + foto ulang, lalu second opinion Gemini
- `status=kosong`tidak ada uang terdeteksi

## Pakai dari kode lain (mis. server FastAPI)

```python
import sys; sys.path.insert(0, "ai")
from predict import MoneyDetector

det = MoneyDetector()                 # default: models/uang_v2.onnx, CPU
hasil = det.predict(jpeg_bytes)       # path, bytes JPEG (ESP32-CAM), array BGR, atau PIL.Image
hasil["status"]   # "ok" | "ragu" | "kosong"
hasil["total"]    # total rupiah yang yakin
hasil["items"]    # [{"kelas","nominal","jenis","confidence","yakin","box":[x1,y1,x2,y2]}, ...]
hasil["result"]   # gambar BGR dengan kotak + label
```

## Catatan

- Ambang yakin bisa diubah: `--thr 0.85` (default) atau `--conf 0.25` (ambang deteksi awal).
- Foto asli dari kotak sering menghasilkan `ragu` (LED, pantulan, uang terlipat) — itu memang
  alur yang dirancang: ragu → foto ulang → Gemini → unknown.
- File ini versi ringan dari `code/ai/ai/yolo/predict.py` (yang pakai ultralytics). Hasil
  deteksi setara; tanpa ketergantungan torch.