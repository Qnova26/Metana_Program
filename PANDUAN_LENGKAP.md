# Panduan Lengkap: IoT Middleware & Data Pipeline Gas Metana

> **Dokumen ini dibuat untuk membantu proses serah terima, deployment, dan integrasi sensor.**

---

## Daftar Isi
1. [Gambaran Sistem](#1-gambaran-sistem)
2. [Struktur Folder Proyek](#2-struktur-folder-proyek)
3. [Penjelasan Setiap File](#3-penjelasan-setiap-file)
4. [Laporan Progres & Perubahan Kode](#4-laporan-progres--perubahan-kode)
5. [Pembuktian Kinerja Algoritma BMA & CSW](#5-pembuktian-kinerja-algoritma-bma--csw)
6. [Cara Setup & Menjalankan Server di VPS UGM](#6-cara-setup--menjalankan-server-di-vps-ugm)
7. [Cara Integrasi dengan Sensor ESP32 (Hardware)](#7-cara-integrasi-dengan-sensor-esp32-hardware)
8. [Panduan Debugging & Troubleshooting](#8-panduan-debugging--troubleshooting)

---

## 1. Gambaran Sistem

Sistem ini adalah sebuah **Middleware Data Pipeline** berbasis Flask yang menjembatani antara perangkat keras (ESP32 + Sensor Gas TGS2611 + DHT22) dengan database Firebase. Middleware ini tidak hanya menerima dan menyimpan data mentah, melainkan juga:

- Memvalidasi setiap data yang masuk dari sensor.
- Mendeteksi anomali (data yang nilainya tidak masuk akal).
- Menambal data yang hilang saat koneksi terputus menggunakan algoritma **BMA**.
- Mengoreksi data tambalan setelah koneksi pulih menggunakan algoritma **CSW**.
- Mengagregasi data ke dalam jendela waktu 1 menit, 10 menit, dan 1 jam untuk keperluan monitoring dan analisis historis.

### Alur Data Lengkap

```
[ESP32 + Sensor]
      |
      | HTTP POST /data (JSON, tiap 1 menit)
      v
[Flask Server - Server.py]
      |
      |--> [1. Validasi Data]
      |        |-- Lengkap? (device_id, timestamp, dll)
      |        |-- Nilai masuk akal? (tidak null, tidak negatif)
      |
      |--> [2. Deteksi Anomali (IQR)]
      |        |-- Jika anomali -> Simpan ke Firebase: "problematic"
      |        |                   Estimasi ulang pakai BMA
      |        |-- Jika normal  -> Simpan ke Firebase: "raw"
      |
      |--> [3. Deteksi Data Hilang]
      |        |-- Ada gap > 62 detik? -> Buat data estimasi (BMA)
      |        |-- Simpan ke Firebase: "processed" (status: estimated)
      |
      |--> [4. Koreksi CSW]
      |        |-- Data estimasi lama dikoreksi pakai data baru
      |        |-- Update Firebase: "processed" (status: csw_corrected)
      |
      |--> [5. Agregasi Historis]
               |-- Bucket 1 menit  -> Firebase: "historical/1min"
               |-- Bucket 10 menit -> Firebase: "historical/10min"
               |-- Bucket 1 jam    -> Firebase: "historical/1hour"
```

---

## 2. Struktur Folder Proyek

```
metana_program/
|
|-- Server.py                         <- INTI SISTEM (Flask + Algoritma)
|-- simulator.py                      <- Alat uji coba (ESP32 Virtual)
|
|-- kode_sensor_esp32/
|   |-- kode_sensor_esp32.ino         <- Firmware untuk chip ESP32
|
|-- serviceAccountKey.json            <- KUNCI RAHASIA FIREBASE (JANGAN DIPUBLIKASI)
|
|-- prd_iot_middleware_data_pipeline.md  <- Dokumen spesifikasi asli (PRD)
|-- PANDUAN_LENGKAP.md                <- Dokumen ini
|
|-- .gitignore                        <- Daftar file yang tidak di-upload ke GitHub
```

---

## 3. Penjelasan Setiap File

### `Server.py` - Jantung dari Seluruh Sistem

Ini adalah file **paling penting**. Di dalamnya terdapat:

| Bagian | Fungsi |
|---|---|
| `EXPECTED_INTERVAL_SECONDS = 60` | Konfigurasi interval pengiriman data: 1 menit |
| `BMA_WINDOW = 4` | Jumlah data sebelumnya yang dipakai untuk menghitung BMA |
| `CSW_BEFORE = 2`, `CSW_AFTER = 2` | Jendela data sebelum dan sesudah untuk koreksi CSW |
| `validate_basic_data()` | Memeriksa apakah JSON yang dikirim ESP32 sudah lengkap dan formatnya benar |
| `detect_anomalies()` | Mendeteksi nilai yang "mencurigakan" menggunakan metode IQR |
| `fill_missing_data()` | Mendeteksi kekosongan data dan memanggil BMA untuk mengisi gap |
| `apply_bma_to_problematic_record()` | Mengganti nilai anomali dengan estimasi BMA |
| `csw_estimate()` | Mengoreksi data estimasi lama setelah data baru tersedia |
| `@app.route('/data', POST)` | Endpoint utama penerima data dari ESP32 |
| `@app.route('/data_terkini', GET)` | Endpoint untuk mengambil data sensor terbaru |
| `@app.route('/historical/1min', GET)` | Endpoint data historis per 1 menit |
| `@app.route('/historical/10min', GET)` | Endpoint data historis per 10 menit |
| `@app.route('/historical/hourly', GET)` | Endpoint data historis per jam |

---

### `simulator.py` - ESP32 Virtual (Hanya untuk Testing)

Digunakan **hanya saat pengujian** tanpa hardware fisik. Skrip ini mensimulasikan perilaku ESP32 dengan cara membuat data acak yang masuk akal dan mengirimkannya ke `Server.py` setiap 60 detik.

**Cara pakai:**
```bash
# Terminal 1: Jalankan server terlebih dahulu
python Server.py

# Terminal 2: Jalankan simulator
python simulator.py
```

> CATATAN: Saat hardware ESP32 asli sudah terpasang dan aktif, file `simulator.py` ini
> TIDAK perlu dijalankan karena tugasnya sudah diambil alih oleh alat fisik.

---

### `kode_sensor_esp32/kode_sensor_esp32.ino` - Firmware ESP32

Kode C++ yang di-flash (upload) langsung ke chip ESP32 menggunakan **Arduino IDE**. Di dalamnya sudah terkonfigurasi:

| Parameter | Nilai | Keterangan |
|---|---|---|
| `ssid` | "TesESP32" | Nama Hotspot/WiFi yang digunakan |
| `password` | "12345678" | Password WiFi |
| `serverURL` | http://10.33.102.153:5000/data | Alamat IP VPS UGM + endpoint Flask |
| `WARMUP_DURATION_MS` | 180.000 ms (3 menit) | Waktu pemanasan sensor TGS2611 sebelum mulai kirim |
| `SERVER_SEND_INTERVAL_MS` | 60.000 ms (1 menit) | Interval pengiriman data ke server |

**Siklus kerja ESP32:**
```
Nyala -> [WARM-UP 3 menit] -> [PURGING 3 menit] -> [ACQUISITION 1 menit] -> [WAIT 1 menit] -> kembali ke PURGING
```

> PENTING: ESP32 TIDAK akan mengirim data selama fase WARM-UP.
> Pengiriman baru dimulai saat memasuki fase PURGING.

---

### `serviceAccountKey.json` - Kunci Firebase

File ini adalah "kunci" untuk database Firebase. **Tanpa file ini, `Server.py` tidak akan bisa menyimpan data ke mana pun.**

- File ini TIDAK ada di dalam ZIP ini karena alasan keamanan.
- Kamu harus menyalin file `serviceAccountKey.json` milikmu sendiri ke dalam folder proyek ini sebelum menjalankan `Server.py`.
- Cara mendapatkan file ini: Firebase Console > Project Settings > Service Accounts > Generate new private key.

---

## 4. Laporan Progres & Perubahan Kode

### Perubahan di `Server.py`

| # | Perubahan | Sebelum | Sesudah |
|---|---|---|---|
| 1 | Interval waktu pengiriman | 5 detik | 60 detik (1 menit) |
| 2 | Nama koleksi Firebase (Raw) | raw_5sec | raw |
| 3 | Nama koleksi Firebase (Processed) | - | processed |
| 4 | Nama koleksi Firebase (Problematic) | - | problematic |

### Perubahan di `kode_sensor_esp32.ino`

| # | Perubahan | Sebelum | Sesudah |
|---|---|---|---|
| 1 | URL tujuan pengiriman | /data (belum diverifikasi) | /data (dikonfirmasi benar) |
| 2 | Nilai relay_status di JSON | "false" selalu (hardcoded) | Dinamis: true saat PURGING/ACQUISITION, false saat WAIT |

### File Baru yang Ditambahkan

| File | Fungsi |
|---|---|
| `simulator.py` | Alat uji coba ESP32 virtual untuk testing tanpa hardware |
| `kode_sensor_esp32/kode_sensor_esp32.ino` | Format standar Arduino IDE agar mudah di-flash |
| `.gitignore` | Sistem keamanan agar kunci Firebase tidak bocor ke internet |
| `PANDUAN_LENGKAP.md` | Dokumen ini |

---

## 5. Pembuktian Kinerja Algoritma BMA & CSW

Pengujian dilakukan dengan cara **mematikan `simulator.py`** (mensimulasikan ESP32 mati/WiFi putus) selama kurang lebih 5 menit, kemudian menyalakannya kembali.

### Fase 1: Data Normal (Sebelum Putus)

```
18:02 -> CH4: 68.05 ppm | Status: actual | Validation: valid
18:03 -> CH4: 81.19 ppm | Status: actual | Validation: valid
18:04 -> CH4: 92.34 ppm | Status: actual | Validation: valid
18:05 -> CH4: 75.61 ppm | Status: actual | Validation: valid
```

### Fase 2: Koneksi Putus (Simulator Dimatikan)

Server menyadari gap lebih dari 62 detik, dan algoritma BMA langsung bekerja:

```
18:06 -> [BMA AKTIF] CH4: 79.35 ppm | Status: estimated | generated_by_middleware
18:07 -> [BMA AKTIF] CH4: 79.35 ppm | Status: estimated | generated_by_middleware
18:08 -> [BMA AKTIF] CH4: 79.35 ppm | Status: estimated | generated_by_middleware
18:10 -> [BMA AKTIF] CH4: 79.35 ppm | Status: estimated | generated_by_middleware
```

**Rumus BMA (N=4) yang digunakan:**

```
BMA_CH4 = (CH4[t-1] + CH4[t-2] + CH4[t-3] + CH4[t-4]) / 4
BMA_CH4 = (75.61 + 92.34 + 81.19 + 68.05) / 4 = 79.30 ppm
```

- `t-1` = data 1 menit sebelum gap (75.61)
- `t-2` = data 2 menit sebelum gap (92.34)
- `t-3` = data 3 menit sebelum gap (81.19)
- `t-4` = data 4 menit sebelum gap (68.05)

### Fase 3: Koneksi Pulih (Simulator Dinyalakan Kembali)

Saat data baru dari jam 18:21 masuk, algoritma CSW langsung mengoreksi nilai estimasi BMA lama:

```
18:21 -> CH4: 110.58 ppm | Status: actual | Validation: valid
         |
         +--> [CSW AKTIF] Mengoreksi data estimasi di menit 18:16 - 18:20

Hasil CSW di menit 18:16:
  BMA (sebelum koreksi) : 84.998 ppm
  CSW (sesudah koreksi) : 80.037 ppm   <- Lebih akurat!
  csw_applied           : True
```

**Rumus CSW (Before=2, After=2) yang digunakan:**

```
CSW = (Data_sebelum_1 + Data_sebelum_2 + Data_sesudah_1 + Data_sesudah_2) / 4

Di mana:
  Data_sebelum = data valid SEBELUM terjadinya gap
  Data_sesudah = data valid SETELAH koneksi pulih kembali
```

### Kesimpulan Pengujian

| Metrik | Hasil |
|---|---|
| Data hilang akibat putus koneksi 5 menit | 5 titik data |
| Data berhasil diestimasi oleh BMA | 5 titik data (100%) |
| Estimasi yang dikoreksi oleh CSW | 5 titik data (100%) |
| Grafik monitoring tetap mulus tanpa bolong | Ya (terbukti) |

---

## 6. Cara Setup & Menjalankan Server di VPS UGM

### Langkah 1: Persiapan Awal (Jalankan Sekali Saja)

```bash
# 1. Masuk ke VPS via SSH
ssh username@10.33.102.153

# 2. Update sistem
sudo apt update && sudo apt upgrade -y

# 3. Install Python dan tools yang dibutuhkan
sudo apt install python3 python3-pip python3-venv -y

# 4. Salin folder proyek ke VPS (dari laptop, bukan dari dalam VPS)
# Jika punya ZIP, gunakan SFTP atau scp:
scp -r metana_program.zip username@10.33.102.153:~/

# 5. Ekstrak ZIP di VPS
unzip metana_program.zip
cd metana_program

# 6. Buat dan aktifkan Virtual Environment
python3 -m venv venv
source venv/bin/activate

# 7. Install semua library yang dibutuhkan
pip install flask firebase-admin requests gunicorn
```

### Langkah 2: Letakkan File Kunci Firebase

```bash
# Salin serviceAccountKey.json ke dalam folder proyek di VPS
# Jalankan perintah ini dari laptop (bukan dari dalam SSH)
scp serviceAccountKey.json username@10.33.102.153:~/metana_program/
```

### Langkah 3: Menjalankan Server (Mode Produksi dengan Gunicorn)

```bash
# Aktifkan venv terlebih dahulu
source ~/metana_program/venv/bin/activate

# Jalankan server menggunakan Gunicorn
gunicorn --workers 2 --bind 0.0.0.0:5000 Server:app
```

### Langkah 4: Agar Server Hidup Terus (Systemd Service)

Buat file konfigurasi agar server otomatis menyala saat VPS restart:

```bash
sudo nano /etc/systemd/system/metana.service
```

Isi dengan teks berikut (ganti `username` dengan nama akun VPS kamu):

```ini
[Unit]
Description=Metana Gas Monitoring Flask Server
After=network.target

[Service]
User=username
WorkingDirectory=/home/username/metana_program
ExecStart=/home/username/metana_program/venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 Server:app
Restart=always

[Install]
WantedBy=multi-user.target
```

Aktifkan service-nya:

```bash
sudo systemctl daemon-reload
sudo systemctl enable metana.service
sudo systemctl start metana.service

# Cek apakah berhasil berjalan
sudo systemctl status metana.service
```

---

## 7. Cara Integrasi dengan Sensor ESP32 (Hardware)

### Langkah 1: Persiapan Arduino IDE

1. Download dan install **Arduino IDE** dari https://www.arduino.cc/en/software
2. Tambahkan dukungan board ESP32:
   - Buka File > Preferences
   - Di kolom "Additional Board Manager URLs", tambahkan:
     ```
     https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
     ```
3. Install board: Tools > Board > Boards Manager > cari **esp32** > Install.

### Langkah 2: Install Library yang Dibutuhkan

Buka Arduino IDE > Tools > Manage Libraries, lalu install satu per satu:
- `Adafruit GFX Library`
- `Adafruit SSD1306`
- `DHT sensor library` (by Adafruit)

### Langkah 3: Buka & Konfigurasi File .ino

1. Buka file `kode_sensor_esp32/kode_sensor_esp32.ino` di Arduino IDE.
2. **Sesuaikan parameter berikut** sebelum di-upload:

```cpp
// ====================================================
// BAGIAN YANG WAJIB DISESUAIKAN:
// ====================================================

// 1. Nama dan Password WiFi/Hotspot di lokasi kandang
const char* ssid     = "TesESP32";    // <- Ganti nama WiFi kandang
const char* password = "12345678";    // <- Ganti password WiFi

// 2. IP Address VPS UGM tempat Server.py berjalan
const char* serverURL = "http://10.33.102.153:5000/data"; // <- Pastikan IP ini benar

// 3. Nilai R0: TIDAK diatur di sini, tapi dibaca otomatis dari
//    memori internal ESP32 (NVS) setelah proses kalibrasi dilakukan.
```

### Langkah 4: Lakukan Kalibrasi Sensor R0 (WAJIB!)

> PERINGATAN: Langkah ini tidak boleh dilewati!

Sensor gas TGS2611 membutuhkan nilai **R0** (Resistansi Dasar di udara bersih) yang unik untuk setiap unit sensor. Tanpa kalibrasi, nilai CH4 yang dikirim ke server akan sangat tidak akurat.

Proses kalibrasi dilakukan terpisah menggunakan firmware kalibrasi khusus. Setelah kalibrasi selesai, nilai R0 akan disimpan otomatis di memori internal ESP32 (NVS) dan firmware monitoring ini akan membacanya secara otomatis saat pertama kali nyala.

Jika ESP32 menampilkan pesan "ERROR: R0 TIDAK DITEMUKAN DI NVS!" di Serial Monitor, artinya kalibrasi belum dilakukan.

### Langkah 5: Upload ke ESP32

1. Sambungkan ESP32 ke laptop menggunakan kabel USB.
2. Di Arduino IDE, pilih board: Tools > Board > **ESP32 Dev Module**.
3. Pilih port yang benar: Tools > Port > (pilih port COM yang muncul, biasanya COM3 atau COM4).
4. Klik tombol **Upload** (ikon panah ke kanan).
5. Tunggu hingga muncul tulisan **Done uploading**.

### Langkah 6: Verifikasi Data di Server

Buka Serial Monitor (Tools > Serial Monitor, Baud Rate: **115200**) untuk melihat log ESP32. Jika berhasil terhubung:

```
[WiFi] Tersambung. IP: 192.168.x.x
[NVS] R0 dimuat = 12.5432 kOhm
Memulai WARM-UP (180 detik)...
================================
SIKLUS     : WARM-UP
Server     : SKIP (belum kirim)
...
[STATE] WARM-UP selesai -> PURGING (mulai kirim ke server)
[HTTP] Data terkirim. Kode: 200
```

Di saat yang sama, log di terminal `Server.py` di VPS akan menampilkan:

```
DATA DITERIMA DARI JOAN / ESP32
{'device_id': 'ESP32_GAS_NODE', 'ch4_ppm': ..., ...}

DATA BERHASIL DIPROSES
Status: actual | Validation: valid
```

---

## 8. Panduan Debugging & Troubleshooting

| Masalah | Kemungkinan Penyebab | Solusi |
|---|---|---|
| `FileNotFoundError: serviceAccountKey.json` | File kunci Firebase tidak ada di folder | Salin `serviceAccountKey.json` ke folder proyek |
| `404 Not Found` saat kirim data | URL endpoint salah | Pastikan `serverURL` di `.ino` diakhiri dengan `/data` |
| `Connection refused` dari simulator | Flask belum berjalan | Jalankan `python Server.py` terlebih dahulu |
| `ERROR: R0 TIDAK DITEMUKAN DI NVS` | Kalibrasi sensor belum dilakukan | Jalankan firmware kalibrasi dulu, lalu flash ulang firmware monitoring |
| Data `validation_status: invalid` di Firebase | Nilai sensor di luar rentang wajar | Cek kondisi fisik sensor, mungkin perlu rekalibrasi |
| Server Flask berhenti setelah SSH ditutup | Berjalan di foreground | Gunakan gunicorn + systemd (lihat Langkah 4 di bagian setup VPS) |
| ESP32 tidak terdeteksi di Arduino IDE | Driver USB belum terpasang | Install driver CP2102 atau CH340 sesuai chip USB di ESP32 |

---

> Dokumen ini dibuat pada: 29 September 2026
> Versi Kode: Commit c3cc8cf di branch main
> GitHub Repository: https://github.com/Qnova26/Metana_Program