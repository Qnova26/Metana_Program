# Dokumentasi Methane Pipeline (Server & Simulasi)

Proyek ini terdiri dari dua bagian utama:
1. **Server Backend (`Server.py`)**: Bertugas untuk menerima data sensor dari alat IoT, memprosesnya, menyimpannya di memori sementara (buffer), dan mengirimkan rata-rata dari 5 data berturut-turut (sistem windowing) ke database Firebase.
2. **Simulasi Sensor (`methane_pipeline.PY`)**: Skrip untuk mensimulasikan bagaimana data dikirim, di-parsing, dikonversi ke CH4, dan di-windowing (mirip dengan logika Server namun dijalankan secara mandiri/lokal).

---

## 1. Persiapan Awal
Sebelum menjalankan, pastikan Anda sudah menginstall library Python yang dibutuhkan. Buka terminal di VSCode dan jalankan perintah ini:
```bash
pip install flask firebase-admin pandas
```
*(Catatan: Pastikan juga file kredensial `serviceAccountKey.json` dari Firebase berada di folder yang sama dengan file `Server.py`).*

---

## 2. Cara Menjalankan Server (`Server.py`)
1. Buka folder proyek ini di VSCode.
2. Buka Terminal di VSCode (Pilih menu **Terminal** > **New Terminal** di bagian atas).
3. Jalankan perintah berikut:
   ```bash
   python Server.py
   ```
4. Jika berhasil, terminal akan memunculkan tulisan `Running on http://127.0.0.1:5000` (atau `0.0.0.0:5000`).
5. **Tahan tombol Ctrl** di keyboard Anda, lalu **klik kiri** pada link `http://127.0.0.1:5000` tersebut. Browser akan otomatis terbuka dan menampilkan tulisan "SERVER FLASK BERHASIL DIAKSES".

---

## 3. Penjelasan Alur Kerja Server (`Server.py`)
Saat server menyala, berikut adalah urutan apa yang terjadi ketika alat sensor (misal milik Joan) mengirimkan data ke server:

1. **Menerima Data**: Alat mengirimkan data ke alamat `http://localhost:5000/data` melalui metode HTTP **POST**.
   Format data teks dari alat: `Suhu: 24.0 C | RH: 40.0 % | V_ADC: 1.10 V | V_Out: 2.75 V |`
2. **Parsing Data**: Teks berantakan di atas dibersihkan dan diambil nilai angkanya saja.
3. **Konversi CH4**: Nilai tegangan output (`V_Out`) dimasukkan ke rumus regresi kalibrasi matematika untuk dikonversi menjadi satuan metana (ppm).
4. **Validasi & Cleaning**: Data divalidasi agar tidak ada variabel yang kosong atau memiliki nilai error/negatif.
5. **Sistem Windowing**: Data suhu, kelembaban, dan CH4 disimpan di memori sementara. Server akan menunggu. **Hanya setelah 5 data terkumpul**, server akan menjumlahkan dan mencari nilai rata-ratanya (averaging).
6. **Simpan ke Firebase**: Nilai rata-rata dari 5 rekaman tersebut barulah dikirimkan dan disimpan permanen ke database Firebase (Realtime Database).

---

## 4. Cara Mengetes Server Tanpa Alat Asli
Karena alat asli mungkin belum menyala, Anda bisa mengetes seolah-olah Anda adalah alat sensor tersebut menggunakan **Thunder Client** (ekstensi VSCode) atau **Postman**:
1. Buat request baru (**New Request**).
2. Ubah metode yang awalnya GET menjadi **POST**.
3. Masukkan URL: `http://127.0.0.1:5000/data`
4. Di bagian opsi **Body**, pilih **Raw** (atau Text), lalu masukkan teks (sebagai contoh data):
   `Suhu: 24.5 C | RH: 42.0 % | V_ADC: 1.12 V | V_Out: 2.80 V |`
5. Klik **Send**. Server akan merespon "Data diterima, menunggu window 5 data".
6. Ulangi klik **Send** sebanyak 5 kali. Pada klik ke-5, server akan membalas dengan respon berhasil dan data akan otomatis masuk ke Firebase!
