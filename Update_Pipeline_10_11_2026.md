# Laporan Perubahan & Pembaruan Pipeline IoT Metana (10/11/2026)
**Proyek:** IoT Methane Gas (CH4 - TGS2611) Middleware Pipeline & Data Imputation  
**Tanggal Rilis:** 10 November 2026  
**Status Pengujian:** Terverifikasi 100% Cocok (*Exact Match*)  

---

## 1. Ringkasan Eksekutif

Pembaruan pada tanggal 10 November 2026 berfokus pada penyelesaian kendala pengujian imputasi data hilang (*missing packet imputation*) menggunakan algoritma **BMA (*Backward Moving Average*)** dan **CSW (*Centered Sliding Window*)**. 

Pembaruan ini mencakup:
1. **Koreksi Logika Status Data:** Memperbaiki pemetaan status `ACTUAL` (data riil) dan `ESTIMATED` (data hasil estimasi) yang sebelumnya sempat terbalik.
2. **Penyelarasan Formula CSW:** Menyelaraskan rumus CSW backend dengan hitungan manual rujukan (`=AVERAGE(...)` / *step-based centered window*) sehingga menghasilkan akurasi identik 100%.
3. **Penyelesaian Masalah "Jeda Panjang" (Latency & Transmission):** Mendukung pengujian berbasis `Future_timestamp` sehingga pengiriman berjarak teratur (~8.1 mnt normal, ~18.1 mnt cleaning, dan 16–32 mnt saat gap data hilang) tanpa memicu alarm *outage*.
4. **Penyusunan Laporan Evaluasi 4 Sheet:** Menghasilkan dokumen benchmark final [`HASIL_EVALUASI_IMPUTASI_BMA_CSW.xlsx`]

---

## 2. Analisis Masalah & Solusi Teknis

### A. Masalah Pemetaan Status Terbalik (*Flag Inversion*)
* **Gejala:** Baris data riil sensor sempat terlabeli `ESTIMATED`, sementara baris data hilang (yang diimputasi) malah berstatus `ACTUAL`.
* **Penyebab:** Pada file `DATA_UJI_BMA_CSW.xlsx`, field `Future_timestamp` diisi untuk 135 data riil dan dikosongkan (`None`/blank) untuk 16 data hilang. Skrip awal salah mengecek kondisi `if future_ts is not None` sebagai data hilang.
* **Solusi:** Diperbaiki menjadi `is_missing = (future_ts is None)`. Data riil berlabel `ACTUAL (Valid)` dengan warna hijau lembut, dan data hilang berlabel `ESTIMATED (Imputasi)` dengan warna merah muda lembut.

### B. Perbedaan Formula CSW (Akurasi Nilai)
* **Gejala:** Nilai estimasi CSW sebelumnya sedikit meleset dari perhitungan manual di file `DATA_UJI_BMA_CSW_rata_rata.xlsx`.
* **Penyebab:** Sistem sebelumnya menggunakan pembobotan berbasis selisih detik waktu riil (*time-delta*). Karena siklus pembersihan sensor memiliki jeda 18 menit (berbeda dari jeda normal 8 menit), rasio bobot waktu menjadi tidak simetris.
* **Solusi:** Formula diselaraskan menjadi rata-rata jendela terpusat berbasis langkah indeks (*step-based centered window*):
  $$\text{CSW (1 slot hilang)} = \frac{\text{Data Sebelum} + \text{Data Sesudah}}{2}$$
  $$\text{CSW (multi-slot)} = \text{Data Sebelum} + \left(\frac{\text{step}}{\text{total\_step}}\right) (\text{Data Sesudah} - \text{Data Sebelum})$$

### C. Masalah "Jeda Panjang" pada Sheet Latency
* **Gejala:** Pada pengujian awal, tabel latensi mencatat selisih waktu puluhan hingga ratusan menit sehingga dikira sistem mengalami gangguan panjang (*long outage*).
* **Penyebab:** Simulator awal membaca *raw timestamp* historis yang melompat-lompat atau mencoba melakukan *time rescaling*.
* **Solusi:** Dibuat alur khusus yang membaca `Future_timestamp`. Dengan urutan waktu masa depan yang rapi, jeda antar-kiriman stabil di ~8.10 menit, pembersihan di ~18.10 menit, dan saat data hilang jeda terbaca tepat di 16.20 s.d. 32.40 menit (maksimal 32.4 menit untuk 3 data hilang beruntun).

---

## 3. Bukti Verifikasi Akurasi 100%

Tabel pembuktian perbandingan antara nilai hitungan manual di `DATA_UJI_BMA_CSW_rata_rata.xlsx` dengan hasil komputasi sistem:

| Baris / Timestamp | Parameter Sensor | Nilai Rujukan (Excel Manual) | Nilai Sistem (Script Baru) | Status Kecocokan |
| :--- | :--- | :---: | :---: | :---: |
| **idx 10** (08:33:26) | Gas CH4<br>Suhu Lingkungan<br>Kelembaban RH | **2.1275** ppm<br>**32.85** °C<br>**82.25** % | **2.1275** ppm<br>**32.85** °C<br>**82.25** % | **100% IDENTIK** (Selisih 0.000000) ✅ |
| **idx 18** (09:37:37) | Gas CH4<br>Suhu Lingkungan<br>Kelembaban RH | **2.1645** ppm<br>**33.30** °C<br>**77.70** % | **2.1645** ppm<br>**33.30** °C<br>**77.70** % | **100% IDENTIK** (Selisih 0.000000) ✅ |
| **idx 21** (10:01:41) | Gas CH4<br>Suhu Lingkungan<br>Kelembaban RH | **2.1950** ppm<br>**34.25** °C<br>**71.65** % | **2.1950** ppm<br>**34.25** °C<br>**71.65** % | **100% IDENTIK** (Selisih 0.000000) ✅ |
| **idx 41** (12:42:09) | Gas CH4<br>Suhu Lingkungan<br>Kelembaban RH | **2.2855** ppm<br>**35.75** °C<br>**65.10** % | **2.2855** ppm<br>**35.75** °C<br>**65.10** % | **100% IDENTIK** (Selisih 0.000000) ✅ |

---

## 4. Struktur Dokumen Laporan Final (`HASIL_EVALUASI_IMPUTASI_BMA_CSW.xlsx`)

Dokumen laporan evaluasi terdiri atas 4 lembar kerja (*sheet*):

1. **Sheet 1: `Timeline Imputasi Lengkap`**
   * Berisi deret waktu kontinu 151 data (135 baris `ACTUAL (Valid)` warna hijau + 16 baris `ESTIMATED (Imputasi)` warna merah muda lembut).
   * Menampilkan kolom berdampingan: **Nilai Asli Sensor (Ground Truth)** vs **Estimasi Realtime BMA** vs **Rekonstruksi CSW** beserta nilai absolut galat/error masing-masing.
2. **Sheet 2: `Evaluasi Akurasi (MAE-RMSE)`**
   * Rangkuman metrik objektif performa imputasi model:
     * **CH4 CSW MAE**: **0.4990 ppm** *(RMSE: 0.5861)* — Terbukti lebih akurat dibanding BMA (MAE: 0.5393 ppm).
     * **Suhu Lingkungan CSW MAE**: **0.2581 °C**.
     * **Kelembaban RH CSW MAE**: **0.4913 %**.
3. **Sheet 3: `Analisis per Celah Hilang`**
   * Evaluasi performa berdasarkan panjang celah data yang hilang (gap 1 data, gap 2 data berturut-turut, dan gap 3 data berturut-turut).
4. **Sheet 4: `Detail Transmisi & Latency`**
   * Rincian log pengiriman 135 paket data aktual berbasis `Future_timestamp`.
   * Menampilkan interval jeda waktu riil, status interval server, dan waktu pemrosesan middleware server:
     * **Rata-rata Processing Latency Server:** **19.61 ms** *(Sangat cepat & real-time)*.
     * **Minimum Processing Latency:** **15.00 ms**.
     * **Maksimum Processing Latency:** **34.70 ms** *(terjadi saat mendeteksi 3 data hilang berurutan dan menjalankan kalkulasi BMA/CSW)*.
     * **Kejadian Jeda Panjang / Outage (> 45 menit):** **0 Kejadian (Nol)**.

---

## 5. Inventaris Berkas (File Inventory)

### A. Berkas Baru yang Ditambahkan
* [`metana_program_revisiLecir/HASIL_EVALUASI_IMPUTASI_BMA_CSW.xlsx`]Berkas laporan evaluasi akhir 4 sheet.
* [`metana_program_revisiLecir/generate_laporan_imputasi_lengkap.py`] Skrip otomatisasi pembentukan laporan evaluasi 4 sheet.
* [`metana_program_revisiLecir/SERVER2_FUTURE.py`]: Server middleware mandiri yang memprioritaskan field `Future_timestamp`.
* [`metana_program_revisiLecir/simulator_replay_future.py`]: Skrip simulator pengujian live streaming data aktual dan simulasi *packet loss*.
* [`metana_program_revisiLecir/DATA_UJI_BMA_CSW.xlsx`]: Dataset pengujian utama dengan kolom `Future_timestamp`.
* [`metana_program_revisiLecir/DATA_UJI_BMA_CSW_rata_rata.xlsx`]: Dataset rujukan hitungan manual.

### B. Berkas yang Dimodifikasi
* [`metana_program_revisiLecir/SERVER2.py`]: Menjadikan batas jeda gangguan (`LONG_OUTAGE_THRESHOLD_MINUTES = 45`) dapat dikonfigurasi melalui variabel di bagian atas file.

### C. Berkas yang Dihapus / Dibersihkan
* `simulator_uji_bma_csw.py`: Skrip pengujian versi awal yang sudah usang (*obsolete*).

---

## 6. Panduan Penggunaan Sistem (Operational Guide)

### Skenario 1: Menghasilkan Ulang Laporan Excel
Jika ada penyesuaian data atau format, jalankan perintah berikut:
```powershell
cd metana_program_revisiLecir
python generate_laporan_imputasi_lengkap.py
```

### Skenario 2: Menjalankan Pengujian Live Streaming ke Server
Jika pengujian live streaming ke server dan penyimpanan otomatis ke Firebase ingin diperagakan:
1. **Jalankan Server di Terminal 1:**
   ```powershell
   cd metana_program_revisiLecir
   python SERVER2_FUTURE.py
   ```
2. **Jalankan Simulator di Terminal 2:**
   ```powershell
   cd metana_program_revisiLecir
   # Pengiriman bertahap dengan jeda 0.5 detik per paket
   python simulator_replay_future.py --delay 0.5
   ```

---

## 7. Instruksi Push ke GitHub

Untuk menyimpan seluruh riwayat perubahan ke repositori GitHub:

```powershell
# 1. Tambahkan seluruh berkas ke staging
git add metana_program_revisiLecir/SERVER2.py `
        metana_program_revisiLecir/SERVER2_FUTURE.py `
        metana_program_revisiLecir/simulator_replay_future.py `
        metana_program_revisiLecir/generate_laporan_imputasi_lengkap.py `
        metana_program_revisiLecir/DATA_UJI_BMA_CSW.xlsx `
        metana_program_revisiLecir/DATA_UJI_BMA_CSW_rata_rata.xlsx `
        metana_program_revisiLecir/HASIL_EVALUASI_IMPUTASI_BMA_CSW.xlsx `
        Update_Pipeline_10_11_2026.md

# 2. Lakukan commit
git commit -m "feat(imputation): sinkronisasi formula BMA-CSW, server future timestamp, dan laporan evaluasi lengkap"

# 3. Push ke branch utama
git push origin main
```

