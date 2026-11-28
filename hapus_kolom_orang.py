"""
Hapus kolom "orang" dari semua CSV dataset (dijalankan SEKALI saja).

    python hapus_kolom_orang.py

File yang diproses:
    data/Normal.csv, data/Diguncang.csv, data/Diangkat.csv, data/Terguling.csv
    data/raw/<4 file yang sama>
    data/logbook.csv

File langsung ditimpa, tanpa salinan cadangan. Aman dijalankan lebih dari sekali:
file yang sudah tidak punya kolom orang dilewati.
Kolom lain (sesi, isi, servo, dst.) TIDAK diubah sama sekali.
"""
import argparse
import csv
import sys
from pathlib import Path

LABELS = ["Normal", "Diguncang", "Diangkat", "Terguling"]


def deteksi_pemisah(path):
    """CSV dari script pakai koma; kalau pernah di-Save Excel (setelan Indonesia) bisa jadi titik koma."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        header = fh.readline()
    return ";" if header.count(";") > header.count(",") else ","


def proses(path):
    if not path.exists():
        return None
    sep = deteksi_pemisah(path)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=sep)
        kolom = reader.fieldnames or []
        rows = list(reader)
    if "orang" not in kolom:
        return f"  {path}: tidak ada kolom orang, dilewati"

    kolom_baru = [k for k in kolom if k != "orang"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=kolom_baru, delimiter=",", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    catatan = " (pemisah ; dari Excel diubah jadi koma)" if sep == ";" else ""
    return f"  {path}: {len(rows)} baris, kolom orang dihapus{catatan}"


def main():
    p = argparse.ArgumentParser(description="Hapus kolom orang dari dataset")
    p.add_argument("--folder", default="data")
    a = p.parse_args()

    folder = Path(a.folder)
    if not folder.exists():
        sys.exit(f"Folder {folder} tidak ditemukan. Jalankan script ini dari folder sic_kotakamal.")

    files = [folder / "logbook.csv"]
    for lb in LABELS:
        files += [folder / f"{lb}.csv", folder / "raw" / f"{lb}.csv"]

    hasil = [proses(f) for f in files]
    hasil = [h for h in hasil if h]
    if not hasil:
        sys.exit("Tidak ada file CSV dataset yang ditemukan.")
    print("\n".join(hasil))
    print("\nSelesai.")


if __name__ == "__main__":
    main()
