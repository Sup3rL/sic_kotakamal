"""
Hapus satu sesi yang sudah tersimpan (misal ketahuan salah label setelah dicek).

    python hapus_sesi.py s05

Baris dengan sesi itu dibuang dari data/<Label>.csv, data/raw/<Label>.csv, dan data/logbook.csv.
Salinan file sebelum dihapus disimpan dulu sebagai *.bak (kalau salah hapus, rename balik).
"""
import argparse
import csv
import shutil
import sys
from pathlib import Path

LABELS = ["Normal", "Diguncang", "Diangkat", "Terguling"]


def buang(path, sesi):
    if not path.exists():
        return 0
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        kolom = reader.fieldnames
        rows = list(reader)
    sisa = [r for r in rows if r.get("sesi") != sesi]
    dibuang = len(rows) - len(sisa)
    if dibuang:
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=kolom)
            w.writeheader()
            w.writerows(sisa)
        print(f"  {path}: {dibuang} baris dibuang")
    return dibuang


def main():
    p = argparse.ArgumentParser(description="Hapus satu sesi dari dataset")
    p.add_argument("sesi")
    p.add_argument("--folder", default="data")
    a = p.parse_args()
    folder = Path(a.folder)

    files = [folder / "logbook.csv"]
    for lb in LABELS:
        files += [folder / f"{lb}.csv", folder / "raw" / f"{lb}.csv"]
    total = sum(buang(f, a.sesi) for f in files)
    if not total:
        sys.exit(f"Sesi {a.sesi} tidak ditemukan.")
    print(f"Sesi {a.sesi} dihapus. Cadangan ada di file *.bak")


if __name__ == "__main__":
    main()
