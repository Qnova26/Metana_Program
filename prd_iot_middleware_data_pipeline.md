# 📄 Product Requirement Document (PRD)
## **Sistem Middleware & Pipeline Data IoT Telemetri Berbasis Flask & Firebase**

| Attribute | Details |
| :--- | :--- |
| **Nama Project** | IoT Middleware & Data Processing Pipeline |
| **Penyusun** | Carlos |
| **Status** | Draft / Ready for Implementation |
| **Versi** | 1.0 |
| **Lingkungan Server** | VPS UGM (Akses via OpenVPN & VS Code Remote SSH) |

---

## 1. Ringkasan Eksekutif & Tujuan Project

Tujuan dari project ini adalah membangun sebuah pipeline data middleware IoT yang andal untuk menerima data telemetri dari perangkat hardware berbasis **ESP32**. Middleware ini bertugas:
1. Menerima (*ingestion*) data via HTTP POST.
2. Mengelola dan memvalidasi kualitas data (mendeteksi *missing data* / anomali).
3. Melakukan estimasi data yang hilang atau bermasalah menggunakan metode statistik **Backward Moving Average (BMA)** dan **Centered Sliding Window (CSW)**.
4. Menghasilkan data terstruktur melalui agregasi waktu (1 menit, 10 menit, 60 menit).
5. Menyimpan data hasil olahan ke **Firebase Cloud Firestore** untuk kebutuhan monitoring *realtime* maupun analisis *historical*.

Middleware ini dijalankan secara independen dan *continuous* (24/7) di **VPS UGM** menggunakan Flask server berbasis *Systemd Background Service*, sehingga sistem tetap berjalan meskipun laptop pengembang dalam keadaan mati/terputus dari koneksi SSH.

---

## 2. Spesifikasi Hardware & Format Data Telemetri

### 2.1. Spesifikasi Perangkat Hardware (ESP32 Node)
* **Mikrokontroler:** ESP32.
* **Sensor Utama:**
  * **Sensor Gas TGS:** Membutuhkan waktu *warm-up* (pemanasan) selama **3 menit (180 detik)** saat perangkat pertama kali *booting* sebelum data pembacaan gas dianggap valid.
  * **Sensor DHT22:** Mengukur Suhu (*Temperature*) dan Kelembapan (*Humidity*).
* **Telemetri Daya:** Pembacaan Tegangan Baterai (`battery_v`) dan Persentase Baterai (`battery_pct`).
* **Aktuator/Sinyal Kontrol:** Status Relay Pompa (`relay_status`: `true` untuk ON / `false` untuk OFF).
* **Konektivitas:** Wi-Fi via Hotspot.
* **Interval Pengiriman:** **1 menit sekali (60 detik)** dikirimkan ke Flask Server API.

### 2.2. Format JSON Payload Telemetri (Input ke Flask)
```json
{
  "device_id": "ESP32_NODE_01",
  "timestamp": 1727500000,
  "ch4_ppm": 125.4,
  "humidity": 68.5,
  "temperature": 29.2,
  "battery_v": 3.95,
  "battery_pct": 82,
  "relay_status": true
}
```

---

## 3. Arsitektur & Alur Pemrosesan Data (Middleware)

### 3.1. Flowchart Pemrosesan Data
```
                     [ Telemetri ESP32 ]
                              │ (1 Menit / HTTP POST)
                              ▼
                     [ Flask API Ingestion ]
                              │
                              ▼
                 [ Validasi Kualitas Data ]
                              │
             ┌────────────────┴────────────────┐
             ▼                                 ▼
       [ Data Valid ]                   [ Data Tidak Valid ]
             │                       (Missing / Null / Anomali)
             ▼                                 │
   [ Update Data Terkini ]                     ▼
             │                      [ Estimasi BMA ]
             │                 (4 Data Valid Sebelumnya)
             │                                 │
             │                                 ▼
             │                     [ Status Data: Estimated ]
             │                                 │
             │                                 ▼
             │                    [ Menunggu Data Aktual Berikutnya ]
             │                                 │
             │                                (YA)
             │                                 │
             │                                 ▼
             │                       [ Koreksi CSW ]
             │                 (2 Sebelum + 2 Sesudah)
             │                                 │
             └────────────────┬────────────────┘
                              ▼
                   [ Data Historis Pipeline ]
                              │
                              ▼
                       [ Agregasi Data ]
                     (1m , 10m , 60m)
                              │
                              ▼
                  [ Firebase Cloud Firestore ]
```

---

### 3.2. Rincian Logika & Algoritma Pipeline

#### A. Validasi Kualitas Data
Sistem memeriksa setiap *payload* yang masuk dengan kriteria:
* Kelengkapan *field* wajib: `device_id`, `timestamp`, `ch4_ppm`, `humidity`, `temperature`.
* Kelayakan rentang statistik (misal: suhu $0 - 60 ^\circ\text{C}$, kelembapan $0 - 100\%$, $\text{CH}_4 > 0$).
* **Keputusan:** Jika memenuhi kriteria, data masuk ke alur **Data Valid**. Jika tidak (null/outlier/terputus), data dialihkan ke alur **Data Estimasi**.

#### B. Estimasi Realtime via Backward Moving Average (BMA)
Jika data saat ini terdeteksi bermasalah atau hilang, sistem secara *realtime* menghitung nilai pengganti menggunakan data 4 *record* aktual-valid sebelumnya:

$$BMA_t = \frac{1}{4} \sum_{i=1}^{4} X_{t-i}$$

* **Status Output:** `estimated`
* Data estimasi ini dikirim langsung ke tampilan *Data Terkini/Dashboard* agar grafik monitoring tidak terputus.

#### C. Koreksi Data via Centered Sliding Window (CSW)
Setelah data aktual berikutnya berhasil diterima, sistem secara otomatis melakukan perbaikan (*smoothing/correction*) pada titik data estimasi sebelumnya menggunakan 2 data valid sebelum dan 2 data valid sesudah:

$$CSW_t = \frac{X_{t-2} + X_{t-1} + X_{t+1} + X_{t+2}}{4}$$

* Nilai hasil perhitungan CSW akan memperbarui data estimasi awal dan kini dilabeli sebagai data terverifikasi untuk disimpan dalam data historis.

#### D. Penanganan Data Terlambat (Late Data / Out-of-Order)
Jika terdapat data terlambat yang masuk setelah jeda waktu tertentu:
1. Simpan data aktual ke tabel *raw* & *processed*.
2. Hitung ulang (*recompute*) nilai BMA/CSW pada rentang waktu yang terpengaruh.
3. Lakukan agregasi ulang (*re-aggregate*) pada rentang 1m, 10m, dan 60m.

#### E. Agregasi Data
Data yang telah divalidasi/dikoreksi akan dikelompokkan ke dalam 3 level interval waktu:
* **1 Menit:** Menggunakan data hasil *ingestion* individual per menit.
* **10 Menit:** Rata-rata (*mean*) parameter sensor dalam durasi 10 menit.
* **60 Menit (1 Jam):** Rata-rata (*mean*) parameter sensor dalam durasi 1 jam.

---

## 4. Struktur Database (Firebase Cloud Firestore)

Data disimpan ke Firestore dengan skema koleksi sebagai berikut:

```
firestore-db/
├── devices/
│   └── {device_id}/
│       ├── latest/
│       │   └── current_status (Dokumen Data Terkini realtime)
│       ├── historical/
│       │   └── {timestamp_doc} (Data per menit terstruktur)
│       ├── aggregated_10m/
│       │   └── {timestamp_10m} (Ringkasan 10 menit)
│       └── aggregated_60m/
│           └── {timestamp_60m} (Ringkasan 1 jam)
```

---

## 5. Infrastruktur Server & Deployment (VPS UGM)

Untuk memastikan Flask Server berjalan terus-menerus (*always-on*) di VPS UGM tanpa bergantung pada koneksi SSH laptop:

1. **Akses Remote:**
   * VPN UGM (OpenVPN Client).
   * VS Code Remote SSH Extension.
2. **Web Server Stack:**
   * **Gunicorn / uWSGI:** Sebagai WSGI HTTP Application Server.
   * **Nginx:** Sebagai Reverse Proxy server di depan Gunicorn.
3. **Background Process Management:**
   * Menggunakan **Systemd Service Linux** (`/etc/systemd/system/iot-middleware.service`).
   * *Systemd* menjamin Flask tetap berjalan di *background*, otomatis aktif saat VPS menyala ulang (*reboot*), dan melakukan *auto-restart* jika terjadi *crash*.

---

## 6. Checklist Rencana Kerja (Implementation Roadmap)

- [ ] **Fase 1: Firmware ESP32**
  - [ ] Implementasi timer/delay *warm-up* 3 menit (180 detik) untuk sensor TGS pada fungsi `setup()`.
  - [ ] Pembacaan data sensor DHT22, level baterai, dan status relay.
  - [ ] Pembentukan payload JSON & pengiriman via HTTP POST setiap 60 detik.
- [ ] **Fase 2: Konfigurasi VPS & Flask Background Service**
  - [ ] Setup Python Virtual Environment di VPS UGM.
  - [ ] Pembuatan skrip Flask API endpoints (`/api/v1/telemetry`).
  - [ ] Konfigurasi Gunicorn & Systemd Service agar Flask berjalan 24/7.
- [ ] **Fase 3: Pengolahan Data & Middleware Engine**
  - [ ] Implementasi fungsi Validasi Kualitas Data.
  - [ ] Implementasi fungsi BMA (4 data valid sebelumnya).
  - [ ] Implementasi fungsi CSW (2 data sebelum + 2 data sesudah).
  - [ ] Implementasi fungsi Agregasi Data (1m, 10m, 60m).
- [ ] **Fase 4: Integrasi Firebase Firestore**
  - [ ] Konfigurasi `firebase-admin` Python SDK dengan Service Account Key.
  - [ ] Penulisan data ke koleksi `latest`, `historical`, dan `aggregated`.
- [ ] **Fase 5: Pengujian & Validesi Sistem**
  - [ ] Simulation test: Sengaja mematikan ESP32 untuk menguji algoritma BMA & CSW.
  - [ ] Persistence test: Mematikan koneksi SSH/laptop untuk memastikan Flask di VPS tetap aktif.

---

## 7. Catatan Pelaksanaan Tambahan
1. **Penyimpanan Cache Data:** Disarankan menggunakan struktur data *in-memory* (seperti `deque` bawaan Python atau Redis) pada Flask server untuk menyimpan 4-5 titik data terakhir agar perhitungan BMA/CSW berjalan sangat cepat tanpa *query* berulang ke Firestore.
2. **Buffer Offline ESP32 (Opsional):** Jika jaringan hotspot terputus sementara, ESP32 dapat menyimpan data sementara di memori internal/SPIFFS dan mengirimkannya secara kumulatif saat koneksi kembali stabil.