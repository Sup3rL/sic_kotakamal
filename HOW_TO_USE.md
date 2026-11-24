# Panduan Penggunaan — AI Kotak Amal (sic_kotakamal)

Dokumen ini menjelaskan cara menjalankan sistem deteksi nominal uang dari awal sampai uji coba,
baik di **Linux (Arch)** maupun **Windows**.

---

## 1. Gambaran alur

```
Uang masuk → IR di ESP32 main → trigger → ESP32-CAM foto
   → POST http://<ip-laptop>:8000/capture?cam=top
   → server.py terima → deteksi AI (uang_v2.onnx)
   → simpan foto asli + foto berkotak + log.txt + latest.json
```

Semua deteksi otomatis: begitu foto masuk, hasil langsung muncul di **terminal** dan tersimpan
di folder `hasil/`. Tidak perlu menjalankan apa pun lagi setelah server menyala.

---

## 2. Struktur folder

```
sic_kotakamal/
├── cam.ino            # firmware ESP32-CAM (kirim foto ke server)
├── esp/esp.ino        # firmware ESP32 main (sensor, servo, trigger)
├── ir_test.ino        # uji sensor IR (tidak wajib)
├── photo_receiver.py  # (lama) hanya terima foto — GANTI ke server.py
├── server.py          # server AI: terima foto + deteksi otomatis
├── ai/
│   ├── predict.py     # detektor (class MoneyDetector + CLI)
│   ├── requirements.txt
│   ├── setup.bat      # setup sekali (Windows)
│   ├── README.md      # dokumentasi teknis ai/
│   └── models/uang_v2.onnx   # model 12 kelas (kertas+koin+lain)
├── photos/            # foto asli dari kamera (photos/<cam>/<waktu>.jpg)
├── hasil/             # output AI (dibuat otomatis)
└── .gitignore
```

---

## 3. Prasyarat

- Python 3.12+ (untuk membuat venv)
- ESP32-CAM (AI-Thinker), sudah terisi `cam.ino`
- Laptop (Arch/Windows) dan ESP32-CAM terhubung ke **jaringan yang sama** (hotspot 2.4 GHz, mis. "yey")

---

## 4. Setup (sekali saja)

### Linux (Arch)

```bash
cd path/ke/sic_kotakamal/ai
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Jika muncul error `libGL.so.1` saat import `cv2`:

```bash
sudo pacman -S libglvnd mesa
```

### Windows

```bash
cd C:\Users\mutaq\Documents\charitybox\sic_kotakamal\ai
setup.bat
```

---

## 5. Atur IP di firmware kamera

1. Cek IP laptop di jaringan kamera:
   - **Arch:** `ip addr` → IP di interface Wi-Fi/Ethernet
   - **Windows:** `ipconfig` → **Wireless LAN adapter Wi-Fi → IPv4 Address**
2. Buka `cam.ino`, baris 26, samakan IP-nya:

   ```cpp
   const char* SERVER = "http://<IP_LAPTOP>:8000";
   ```

3. Flash ulang `cam.ino` ke ESP32-CAM (Board: "AI Thinker ESP32-CAM", PSRAM enabled).
   Jika upload gagal: tahan **IO0**, tap **RST**, lepas IO0, lalu upload lagi.

> Catatan: IP berubah jika ganti jaringan/restart. Cek ulang `ipconfig`/`ip addr` lalu
> sesuaikan dan flash ulang.

---

## 6. Jalankan server AI

### Linux (Arch)

```bash
cd path/ke/sic_kotakamal/ai
.venv/bin/python ../server.py
```

### Windows

```bash
cd C:\Users\mutaq\Documents\charitybox\sic_kotakamal\ai
.venv\Scripts\python ..\server.py
```

Biarkan terminal ini terbuka (jangan ditutup). Server siap di port **8000**.

---

## 7. Uji coba

1. Pastikan server sudah jalan (lihat log: `Server jalan. ...`).
2. Masukkan uang ke kotak (atau trigger manual / kirim `c` lewat Serial Monitor kamera).
3. ESP32-CAM mengambil foto dan mengirimnya → server langsung mendeteksi.
4. Perhatikan terminal server, akan tampil per baris, contoh:

   ```
   14:36:53 cam=top status=ok total=Rp100.000 n=1 k100000@0.92
   ```

5. Buka browser `http://localhost:8000` → menampilkan foto terakhir + hasil deteksi
   (auto-refresh tiap 2 detik).

---

## 8. Lokasi hasil

| File/Folder | Isi |
|---|---|
| `photos/<cam>/<waktu>.jpg` | foto asli dari kamera |
| `hasil/<cam>/<waktu>.jpg` | foto berkotak (label kelas + confidence) |
| `hasil/log.txt` | ringkasan semua deteksi (satu baris per foto) |
| `hasil/latest.json` | hasil deteksi terakhir (untuk dashboard/aplikasi lain) |

---

## 9. Deteksi manual tanpa server (CLI)

Jika hanya ingin menguji foto yang sudah ada (tanpa ESP32-CAM):

```bash
cd path/ke/sic_kotakamal/ai
.venv/bin/python predict.py ../photos/top --save hasil_manual
# atau satu foto
.venv/bin/python predict.py foto.jpg
```

Arti output:

```
foto.jpg  ->  status=ok  total=Rp10.000
   k10000  conf 0.95  yakin
```

- `status=ok`     semua confidence ≥ 0,85 → langsung catat
- `status=ragu`   ada yang < 0,85 → perlu foto ulang / second opinion (Gemini)
- `status=kosong` tidak ada uang terdeteksi

Ambig yakin bisa diubah: `--thr 0.80` (default 0.85).

---

## 10. Troubleshooting

| Masalah | Solusi |
|---|---|
| Server tidak menyala, "GAGAL memuat AI" | Jalankan dari folder `ai/` memakai venv AI, pastikan `.venv/bin/python` |
| Error `libGL.so.1` | `sudo pacman -S libglvnd mesa` (Arch) |
| ESP32-CAM tidak mengirim (foto tidak muncul) | Pastikan IP di `cam.ino` benar & satu jaringan; cek Wi-Fi kamera (Serial Monitor) |
| POST gagal / connection refused | Server `server.py` belum jalan — mulai dulu, baru masukkan uang |
| Port 8000 tidak bisa diakses dari ESP32 | Izinkan port di firewall (`ufw allow 8000` / `firewalld`) |
| Foto tersimpan tapi status selalu `ragu` | Wajar untuk foto asli kotak (LED/pantulan/uang terlipat); itulah alur fallback yang dirancang |

---

## 11. Catatan WSL (jika server jalan di dalam WSL)

ESP32-CAM mengirim ke IP **Windows**, bukan IP WSL. Jika server jalan di WSL, teruskan port:

```bat
netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 connectport=8000 connectaddress=<IP_WSL>
```

Cek IP WSL dengan `ip -4 addr`.