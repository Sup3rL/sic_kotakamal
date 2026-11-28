"""
Kotak Amal Cerdas - perekam dataset gerakan (MPU6050) dari ESP32 ke CSV.

Semua sesi dengan label yang sama masuk ke SATU file:
    data/Normal.csv  data/Diguncang.csv  data/Diangkat.csv  data/Terguling.csv     (fitur 1/detik, scikit-learn)
    data/raw/Normal.csv  ... data/raw/Terguling.csv                               (mentah 50/detik, Edge Impulse)
    data/logbook.csv                                                              (1 baris per sesi)
Tiap baris tetap punya kolom sesi dan isi -> sesi dipakai untuk split train/test per sesi.

Cara pakai (TUTUP Serial Monitor Arduino IDE dulu!), dari terminal VS Code di folder ini:
    python record.py COM7 Diguncang s01 kosong
    python record.py COM7 Normal s02 penuh --durasi 60
    python record.py COM7 Normal s00 kosong --durasi 10 --uji      # uji coba: tidak disimpan

Argumen:
    port      : port ESP32 (Arduino IDE -> Tools -> Port), mis. COM7
    label     : Normal / Diguncang / Diangkat / Terguling
    sesi      : kode sesi, harus beda tiap rekaman (s01, s02, ...)
    isi       : kosong / setengah / penuh
    --durasi  : lama rekam dalam detik (default 90). Ctrl+C untuk berhenti lebih awal.
    --uji     : uji coba, data hanya ditampilkan, tidak disimpan
    --tanpa-raw : hanya simpan fitur per detik

Setelah rekaman selesai, script bertanya "Simpan sesi ini?". Enter = simpan, n = buang
(misal gerakannya salah). Sesi yang sudah tersimpan bisa dihapus dengan hapus_sesi.py.

Butuh esp.ino versi yang punya perintah "f" (fitur) dan "r" (raw).
"""
import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import serial  # dari paket "pyserial"
except ImportError:
    sys.exit("Library pyserial belum terinstall. Jalankan: pip install pyserial")

try:
    import winsound  # bunyi "bip" di Windows, biar yang menggerakkan kotak tahu kapan mulai/selesai
except ImportError:
    winsound = None

LABELS = ["Normal", "Diguncang", "Diangkat", "Terguling"]
ISI = ["kosong", "setengah", "penuh"]
FITUR = ["mean_x", "mean_y", "mean_z", "std_x", "std_y", "std_z",
         "acc_max", "acc_min", "gyro_max", "gyro_mean"]
KOLOM_FITUR = FITUR + ["servo", "label", "sesi", "isi", "waktu"]
RAW = ["timestamp", "ax", "ay", "az", "gx", "gy", "gz", "servo"]
KOLOM_RAW = RAW + ["label", "sesi", "isi"]
KOLOM_LOG = ["sesi", "label", "isi", "baris_fitur", "baris_raw", "waktu"]


def bip(freq=1000, ms=200):
    if winsound:
        winsound.Beep(freq, ms)
    else:
        print("\a", end="", flush=True)


def parse_feat(line):
    """Baris '[FEAT] {...}' (1 per detik) -> dict. Baris lain -> None."""
    if not line.startswith("[FEAT] {"):
        return None
    try:
        row = json.loads(line[len("[FEAT] "):])
    except json.JSONDecodeError:
        return None  # baris terpotong/rusak, lewati
    return row if all(k in row for k in FITUR) else None


def parse_raw(line):
    """Baris '[RAW] ms,ax,ay,az,gx,gy,gz,servo' (50 per detik) -> dict. Baris lain -> None."""
    if not line.startswith("[RAW] ") or line in ("[RAW] ON", "[RAW] OFF"):
        return None
    parts = line[len("[RAW] "):].split(",")
    if len(parts) != len(RAW):
        return None
    try:
        nilai = [int(parts[0])] + [float(x) for x in parts[1:7]] + [int(parts[7])]
    except ValueError:
        return None
    return dict(zip(RAW, nilai))


def sesi_terpakai(folder):
    """Semua kode sesi yang sudah ada di logbook dan file fitur."""
    ada = set()
    for f in [folder / "logbook.csv"] + [folder / f"{lb}.csv" for lb in LABELS]:
        if f.exists():
            with open(f, newline="", encoding="utf-8-sig") as fh:
                ada.update(r.get("sesi", "") for r in csv.DictReader(fh))
    return ada


def cek_header(path, kolom):
    """File lama harus punya kolom yang sama, supaya data tidak campur aduk."""
    if not path.exists() or path.stat().st_size == 0:
        return
    with open(path, newline="", encoding="utf-8-sig") as fh:
        header = next(csv.reader(fh), [])
    if header != kolom:
        sys.exit(f"Kolom di {path} beda dengan format script ini.\n"
                 f"Pindahkan/rename file lama itu dulu, lalu rekam lagi.")


def tambahkan(path, kolom, rows):
    """Tambahkan baris ke file (header ditulis kalau file baru)."""
    baru = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=kolom, extrasaction="ignore")
        if baru:
            w.writeheader()
        w.writerows(rows)


def baca_baris(ser):
    return ser.readline().decode(errors="ignore").strip()


def nyalakan(ser, pakai_raw):
    """Nyalakan output [FEAT] ('f') dan [RAW] ('r'). Keduanya toggle: kalau jadi OFF, kirim lagi."""
    ser.reset_input_buffer()
    ser.write(b"f")
    if pakai_raw:
        ser.write(b"r")
    feat_on, raw_on = False, not pakai_raw
    batas = time.time() + 5
    while time.time() < batas and not (feat_on and raw_on):
        line = baca_baris(ser)
        if line == "[FEAT] OFF":
            ser.write(b"f")
        elif line == "[RAW] OFF" and pakai_raw:
            ser.write(b"r")
        elif line == "[FEAT] ON" or parse_feat(line):
            feat_on = True
        elif line == "[RAW] ON" or parse_raw(line):
            raw_on = True
    return feat_on, raw_on


def main():
    p = argparse.ArgumentParser(description="Rekam dataset gerakan kotak amal dari ESP32")
    p.add_argument("port")
    p.add_argument("label", choices=LABELS)
    p.add_argument("sesi")
    p.add_argument("isi", choices=ISI)
    p.add_argument("--durasi", type=int, default=90, help="lama rekam (detik), default 90")
    p.add_argument("--folder", default="data")
    p.add_argument("--uji", action="store_true", help="uji coba, tidak disimpan")
    p.add_argument("--tanpa-raw", action="store_true", help="jangan simpan data mentah Edge Impulse")
    a = p.parse_args()
    pakai_raw = not a.tanpa_raw

    folder = Path(a.folder)
    file_fitur, file_raw = folder / f"{a.label}.csv", folder / "raw" / f"{a.label}.csv"
    file_log = folder / "logbook.csv"
    if not a.uji:
        (folder / "raw").mkdir(parents=True, exist_ok=True)
        if a.sesi in sesi_terpakai(folder):
            sys.exit(f"Kode sesi {a.sesi} sudah dipakai. Pakai kode lain (lihat {file_log}).")
        cek_header(file_fitur, KOLOM_FITUR)
        cek_header(file_raw, KOLOM_RAW)
        cek_header(file_log, KOLOM_LOG)

    print(f"Membuka {a.port} ...")
    try:
        ser = serial.Serial(a.port, 115200, timeout=1)
    except serial.SerialException as e:
        sys.exit(f"Gagal membuka {a.port}: {e}\n"
                 "-> Serial Monitor / Serial Plotter Arduino IDE masih terbuka? Tutup dulu.\n"
                 "-> Nama port benar? Cek di Arduino IDE -> Tools -> Port (tulis tanpa spasi: COM7).")

    time.sleep(2)  # banyak board ESP32 restart sendiri saat port dibuka
    feat_on, raw_on = nyalakan(ser, pakai_raw)
    if not feat_on:
        ser.close()
        sys.exit("Tidak ada data [FEAT] dari ESP32. Cek: esp.ino sudah di-upload? "
                 "MPU6050 terbaca (OK di self-check)?")
    if not raw_on:
        ser.close()
        sys.exit("Data [RAW] tidak muncul. Upload esp.ino versi terbaru (yang punya perintah 'r'), "
                 "atau jalankan dengan --tanpa-raw.")

    judul = "UJI COBA (tidak disimpan) | " if a.uji else ""
    print(f"\n{judul}Sesi {a.sesi} | label {a.label} | kotak {a.isi} | {a.durasi} detik")
    for i in (3, 2, 1):
        print(f"  {i}...")
        bip(800, 150)
        time.sleep(1)
    ser.reset_input_buffer()  # buang data selama hitung mundur
    print("  MULAI! (Ctrl+C untuk berhenti lebih awal)\n")
    bip(1500, 400)

    info = dict(label=a.label, sesi=a.sesi, isi=a.isi)
    fitur_rows, raw_rows = [], []   # disimpan di memori dulu, ditulis ke file setelah dikonfirmasi
    mulai = time.time()
    try:
        while time.time() - mulai < a.durasi:
            line = baca_baris(ser)
            raw = parse_raw(line) if pakai_raw else None
            if raw:
                raw.update(info)
                raw_rows.append(raw)
                continue
            row = parse_feat(line)
            if row is None:
                continue
            row.update(info, waktu=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            fitur_rows.append(row)
            sisa = a.durasi - int(time.time() - mulai)
            tanda = "  (servo bergerak, nanti dibuang)" if row.get("servo") else ""
            print(f"  detik {len(fitur_rows):3d} | sisa {sisa:3d} | std_x {row['std_x']:.3f} "
                  f"| gyro_max {row['gyro_max']:7.1f} | raw {len(raw_rows)}{tanda}")
    except KeyboardInterrupt:
        print("\nDihentikan manual (Ctrl+C).")
    lama = time.time() - mulai

    bip(1500, 150); bip(1500, 150)
    try:
        ser.write(b"f")                 # matikan lagi output [FEAT] / [RAW]
        if pakai_raw:
            ser.write(b"r")
        ser.close()
    except serial.SerialException:
        pass

    print(f"\nTerekam: {len(fitur_rows)} baris fitur, {len(raw_rows)} baris mentah ({lama:.0f} detik).")
    if len(fitur_rows) < 0.8 * lama - 2:
        print("Catatan: baris fitur lebih sedikit dari seharusnya (1 per detik). Cek kabel USB / MPU6050.")
    if pakai_raw and fitur_rows and len(raw_rows) < 40 * len(fitur_rows):
        print("Catatan: data mentah kurang dari ~50 per detik. Ada baris yang hilang di serial.")

    if a.uji:
        print("Mode uji: tidak ada yang disimpan.")
        return
    if not fitur_rows:
        print("Tidak ada data, tidak ada yang disimpan.")
        return
    try:
        jawab = input(f"Simpan sesi {a.sesi} ({a.label}, {a.isi})? [Enter = simpan / n = buang]: ")
    except (KeyboardInterrupt, EOFError):
        jawab = "n"
        print()
    if jawab.strip().lower().startswith("n"):
        print("Sesi dibuang. Ulangi dengan kode sesi yang sama juga boleh.")
        return

    tambahkan(file_fitur, KOLOM_FITUR, fitur_rows)
    if pakai_raw:
        tambahkan(file_raw, KOLOM_RAW, raw_rows)
    tambahkan(file_log, KOLOM_LOG, [dict(sesi=a.sesi, label=a.label, isi=a.isi,
                                         baris_fitur=len(fitur_rows), baris_raw=len(raw_rows),
                                         waktu=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))])
    print(f"Tersimpan: +{len(fitur_rows)} baris -> {file_fitur}")
    if pakai_raw:
        print(f"           +{len(raw_rows)} baris -> {file_raw}")
    print(f"Logbook:   {file_log}")


if __name__ == "__main__":
    main()