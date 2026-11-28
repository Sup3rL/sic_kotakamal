"""
Kotak Amal Cerdas - cleaning dataset gerakan (tahap "Clean data" + validasi label).

    pip install pandas          (sekali saja)
    python clean_data.py

Input  : data/<Label>.csv        (fitur per detik, dari record.py)
         data/raw/<Label>.csv    (data mentah 50 Hz)
Output : data_bersih/<Label>.csv
         data_bersih/raw/<Label>.csv
         data_bersih/ringkasan_cleaning.csv   (jumlah baris tiap langkah, buat laporan)

Folder data/ tidak diubah sama sekali. data_bersih/ dibuat ulang setiap script dijalankan,
jadi kalau batas di bawah diubah, cukup jalankan lagi.

Langkah cleaning (sama untuk data per detik dan data mentah):
  1. Format  : angka yang tidak terbaca / kosong dibuang, baris duplikat dibuang,
               TRUE/FALSE sisa Excel diseragamkan, kolom yang tidak dipakai model dibuang.
  2. Servo   : detik/sampel saat servo bergerak dibuang (+ 1 detik sesudahnya untuk berjaga-jaga).
  3. Transisi: 2 detik pertama dan 2 detik terakhir tiap sesi dibuang (baru mulai / sudah berhenti).
  4. Validasi label: baris yang isinya tidak sesuai labelnya dibuang:
       Normal    : kotak miring lebih dari BATAS_MIRING_NORMAL derajat
       Diguncang : kotak ternyata diam (jeda di tengah sesi)
       Diangkat  : kotak ternyata diam
       Terguling : kotak ternyata tegak DAN diam (sebelum digulingkan / setelah diberdirikan lagi).
                   Proses menggulingkan dan posisi rebah/terbalik tetap disimpan.
"""
import sys
from pathlib import Path

try:
    import numpy as np
    import pandas as pd
except ImportError:
    sys.exit("Library pandas belum terinstall. Jalankan: pip install pandas")

# ---------------------------------------------------------------- batas-batas (boleh diubah)
BUANG_AWAL_DETIK = 2          # langkah 3
BUANG_AKHIR_DETIK = 2
BUANG_SETELAH_SERVO_DETIK = 1  # langkah 2: jeda aman setelah servo berhenti

# "Diam": gyro_max di bawah ini DAN std terbesar (x/y/z) di bawah ini.
# Dari data Normal s01: saat diam gyro_max ~2.0-2.8 deg/s, std ~0.002-0.008 g.
DIAM_GYRO_MAX = 4.0
DIAM_STD_MAX = 0.01

BATAS_MIRING_NORMAL = 30.0     # derajat. Normal harus tegak.
BATAS_MIRING_TERGULING = 45.0  # derajat. Di sesi Terguling, di bawah ini + diam = masih tegak, dibuang.
# --------------------------------------------------------------------------------------------

LABELS = ["Normal", "Diguncang", "Diangkat", "Terguling"]
FITUR = ["mean_x", "mean_y", "mean_z", "std_x", "std_y", "std_z",
         "acc_max", "acc_min", "gyro_max", "gyro_mean"]
RAW = ["timestamp", "ax", "ay", "az", "gx", "gy", "gz"]
INFO = ["label", "sesi", "isi"]

ASAL, HASIL = Path("data"), Path("data_bersih")


def baca_csv(path):
    """Baca CSV dari record.py, juga yang pernah di-Save Excel (BOM / titik koma)."""
    with open(path, encoding="utf-8-sig") as fh:
        header = fh.readline()
    sep = ";" if header.count(";") > header.count(",") else ","
    df = pd.read_csv(path, sep=sep, encoding="utf-8-sig", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df


def ke_bool(s):
    return s.astype(str).str.strip().str.lower().isin(["true", "1", "yes"])


def ke_angka(df, kolom):
    for k in kolom:
        df[k] = pd.to_numeric(df[k].astype(str).str.replace(",", ".", regex=False), errors="coerce")
    return df


def sudut_miring(mx, my, mz, ref):
    """Sudut (derajat) antara arah gravitasi baris ini dan arah gravitasi saat kotak tegak."""
    v = np.stack([mx, my, mz], axis=1).astype(float)
    n = np.linalg.norm(v, axis=1)
    n[n == 0] = np.nan
    cos = (v @ ref) / (n * np.linalg.norm(ref))
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def tidak_sesuai(label, gyro_max, std_max, miring):
    """True = baris ini isinya tidak sesuai labelnya (langkah 4)."""
    diam = (gyro_max < DIAM_GYRO_MAX) & (std_max < DIAM_STD_MAX)
    if label == "Normal":
        return miring > BATAS_MIRING_NORMAL
    if label in ("Diguncang", "Diangkat"):
        return diam
    if label == "Terguling":
        return diam & (miring < BATAS_MIRING_TERGULING)
    return np.zeros(len(gyro_max), dtype=bool)


# ------------------------------------------------------------------- data per detik (fitur)
def bersihkan_fitur(df, label, ref):
    catat = {"awal": len(df)}
    df = df.copy()
    df["urutan"] = np.arange(len(df))  # urutan asli dalam file = urutan waktu

    # 1. format
    df = ke_angka(df, FITUR)
    df["servo"] = ke_bool(df["servo"]) if "servo" in df else False
    df = df.dropna(subset=FITUR + ["sesi"])
    df = df.drop_duplicates(subset=FITUR + ["sesi"])
    catat["format_duplikat"] = catat["awal"] - len(df)

    # 2. servo (+ beberapa detik sesudahnya, per sesi)
    buang = df["servo"].copy()
    for _ in range(BUANG_SETELAH_SERVO_DETIK):
        buang |= df.groupby("sesi")["servo"].shift(1, fill_value=False) & ~df["servo"]
        df["servo"] = buang
    n = len(df)
    df = df[~buang]
    catat["servo"] = n - len(df)

    # 3. transisi awal/akhir tiap sesi
    df = df.sort_values(["sesi", "urutan"])
    ke = df.groupby("sesi").cumcount()
    dari_belakang = df.groupby("sesi").cumcount(ascending=False)
    n = len(df)
    df = df[(ke >= BUANG_AWAL_DETIK) & (dari_belakang >= BUANG_AKHIR_DETIK)]
    catat["transisi"] = n - len(df)

    # 4. validasi label
    std_max = df[["std_x", "std_y", "std_z"]].max(axis=1).to_numpy()
    miring = sudut_miring(df["mean_x"], df["mean_y"], df["mean_z"], ref)
    salah = tidak_sesuai(label, df["gyro_max"].to_numpy(), std_max, miring)
    n = len(df)
    df = df[~salah]
    catat["tidak_sesuai_label"] = n - len(df)

    catat["akhir"] = len(df)
    df = df.sort_values("urutan")
    df["label"] = label
    return df[FITUR + INFO], catat


# ------------------------------------------------------------------- data mentah 50 Hz
def bersihkan_raw(df, label, ref):
    catat = {"awal": len(df)}
    df = df.copy()

    # 1. format
    df = ke_angka(df, RAW)
    df["servo"] = ke_bool(df["servo"]) if "servo" in df else False
    df = df.dropna(subset=RAW + ["sesi"])
    df = df.drop_duplicates(subset=["sesi", "timestamp"])
    catat["format_duplikat"] = catat["awal"] - len(df)
    df = df.sort_values(["sesi", "timestamp"])

    # 2. servo (+ jeda aman sesudahnya)
    buang = df["servo"].to_numpy().copy()
    for sesi, g in df.groupby("sesi"):
        t = g["timestamp"].to_numpy()
        akhir_servo = t[g["servo"].to_numpy()]
        if len(akhir_servo):
            # sampel yang jatuh <= jeda aman setelah sampel servo mana pun
            idx = np.searchsorted(akhir_servo, t, side="right") - 1
            dekat = (idx >= 0) & (t - akhir_servo[np.clip(idx, 0, None)] <= BUANG_SETELAH_SERVO_DETIK * 1000)
            buang[df.index.get_indexer(g.index)] |= dekat
    n = len(df)
    df = df[~buang]
    catat["servo"] = n - len(df)

    # 3. transisi awal/akhir tiap sesi (berdasarkan waktu)
    t0 = df.groupby("sesi")["timestamp"].transform("min")
    t1 = df.groupby("sesi")["timestamp"].transform("max")
    n = len(df)
    df = df[(df["timestamp"] - t0 >= BUANG_AWAL_DETIK * 1000) & (t1 - df["timestamp"] >= BUANG_AKHIR_DETIK * 1000)]
    catat["transisi"] = n - len(df)

    # 4. validasi label: dihitung per jendela 1 detik, aturan sama dengan data per detik
    df = df.copy()
    df["jendela"] = ((df["timestamp"] - df.groupby("sesi")["timestamp"].transform("min")) // 1000).astype(int)
    df["g"] = np.sqrt(df["gx"] ** 2 + df["gy"] ** 2 + df["gz"] ** 2)
    w = df.groupby(["sesi", "jendela"]).agg(
        mean_x=("ax", "mean"), mean_y=("ay", "mean"), mean_z=("az", "mean"),
        std_x=("ax", lambda s: s.std(ddof=0)), std_y=("ay", lambda s: s.std(ddof=0)),
        std_z=("az", lambda s: s.std(ddof=0)), gyro_max=("g", "max"), n=("g", "size"))
    std_max = w[["std_x", "std_y", "std_z"]].max(axis=1).to_numpy()
    miring = sudut_miring(w["mean_x"], w["mean_y"], w["mean_z"], ref)
    w["buang"] = tidak_sesuai(label, w["gyro_max"].to_numpy(), std_max, miring) | (w["n"] < 10)
    df = df.join(w["buang"], on=["sesi", "jendela"])
    n = len(df)
    df = df[~df["buang"]]
    catat["tidak_sesuai_label"] = n - len(df)

    catat["akhir"] = len(df)
    df["label"] = label
    return df[RAW + INFO], catat


def arah_tegak(fitur_normal):
    """Arah gravitasi saat kotak tegak = median mean_x/y/z dari baris Normal yang diam."""
    if fitur_normal is None:
        return np.array([0.0, 0.0, 1.0])
    df = ke_angka(fitur_normal.copy(), FITUR).dropna(subset=FITUR)
    diam = (df["gyro_max"] < DIAM_GYRO_MAX) & (df[["std_x", "std_y", "std_z"]].max(axis=1) < DIAM_STD_MAX)
    pakai = df[diam] if diam.sum() >= 5 else df
    return pakai[["mean_x", "mean_y", "mean_z"]].median().to_numpy()


def main():
    if not ASAL.exists():
        sys.exit("Folder data/ tidak ditemukan. Jalankan script ini dari folder sic_kotakamal.")

    fitur_asli = {lb: baca_csv(ASAL / f"{lb}.csv") for lb in LABELS if (ASAL / f"{lb}.csv").exists()}
    raw_asli = {lb: baca_csv(ASAL / "raw" / f"{lb}.csv") for lb in LABELS if (ASAL / "raw" / f"{lb}.csv").exists()}
    if not fitur_asli:
        sys.exit("Tidak ada file data/<Label>.csv.")

    # sesi yang sama tidak boleh muncul di dua label
    pemilik = {}
    for lb, df in fitur_asli.items():
        for s in df["sesi"].dropna().unique():
            pemilik.setdefault(s, []).append(lb)
    ganda = {s: lbs for s, lbs in pemilik.items() if len(lbs) > 1}
    if ganda:
        print("PERINGATAN: sesi ini muncul di lebih dari satu label, cek lagi:", ganda)

    ref = arah_tegak(fitur_asli.get("Normal"))
    print(f"Arah tegak kotak (dari data Normal): x={ref[0]:.3f} y={ref[1]:.3f} z={ref[2]:.3f}\n")

    (HASIL / "raw").mkdir(parents=True, exist_ok=True)
    for f in list(HASIL.glob("*.csv")) + list((HASIL / "raw").glob("*.csv")):
        f.unlink()  # mulai bersih tiap dijalankan

    ringkas = []
    for jenis, kumpulan, fungsi, folder in [("fitur", fitur_asli, bersihkan_fitur, HASIL),
                                            ("raw", raw_asli, bersihkan_raw, HASIL / "raw")]:
        for lb in LABELS:
            if lb not in kumpulan:
                continue
            bersih, catat = fungsi(kumpulan[lb], lb, ref)
            bersih.to_csv(folder / f"{lb}.csv", index=False)
            ringkas.append({"data": jenis, "label": lb, **catat,
                            "sesi": bersih["sesi"].nunique(),
                            "persen_tersisa": round(100 * catat["akhir"] / max(catat["awal"], 1), 1)})

            # sesi yang hampir habis setelah cleaning -> kemungkinan salah gerakan / salah label
            if jenis == "fitur":
                awal = kumpulan[lb].groupby("sesi").size()
                akhir = bersih.groupby("sesi").size().reindex(awal.index, fill_value=0)
                for s in awal.index[(akhir / awal) < 0.5]:
                    print(f"  Cek sesi {s} ({lb}): tinggal {akhir[s]} dari {awal[s]} baris")

    tabel = pd.DataFrame(ringkas)
    tabel.to_csv(HASIL / "ringkasan_cleaning.csv", index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(tabel.to_string(index=False))

    fitur = tabel[tabel["data"] == "fitur"].set_index("label")["akhir"]
    print(f"\nTotal data bersih (fitur): {int(fitur.sum())} baris, {len(fitur)} label")
    if len(fitur) > 1 and fitur.max() > 1.5 * fitur.min():
        print(f"Catatan: jumlah per label tidak seimbang ({fitur.idxmax()} {fitur.max()} vs "
              f"{fitur.idxmin()} {fitur.min()}). Diseimbangkan nanti di tahap training.")
    if (fitur < 150 / max(len(fitur), 1)).any():
        print("Catatan: ada label dengan data sangat sedikit, pertimbangkan rekam sesi tambahan.")
    print(f"\nSelesai. Hasil ada di folder {HASIL}/")


if __name__ == "__main__":
    main()
