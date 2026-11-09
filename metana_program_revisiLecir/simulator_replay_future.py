"""
=============================================================================
 SIMULATOR REPLAY FUTURE TIMESTAMP — simulator_replay_future.py
=============================================================================
 Dibuat khusus untuk pengujian alur data hilang DATA_UJI_BMA_CSW.xlsx
 langsung ke SERVER2_FUTURE.py.

 Cara Kerja:
 1. Membaca file DATA_UJI_BMA_CSW.xlsx secara berurutan (idx 0 s.d. 150).
 2. Jika baris memiliki Future_timestamp (135 data):
    -> DIKIRIM ke server menggunakan Future_timestamp sebagai timestamp event.
 3. Jika baris Future_timestamp == None (16 data):
    -> DILEWATI / TIDAK DIKIRIM (mensimulasikan paket data hilang / packet loss).
 4. Saat ada data yang dilewati, server akan mendeteksi lonjakan waktu (gap 16-32 mnt)
    dan secara otomatis:
    - Menghitung BMA untuk slot yang hilang
    - Menyimpan baris estimasi ke Firebase
    - Saat data aktual berikutnya tiba, server menginterpolasi dengan CSW!

 Cara Menjalankan:
   1. Jalankan server:
        python SERVER2_FUTURE.py
   2. Jalankan simulator:
        python simulator_replay_future.py
        python simulator_replay_future.py --delay 0.5
        python simulator_replay_future.py --dry-run
=============================================================================
"""

import os
import sys
import time
import math
import argparse
from datetime import datetime

try:
    import requests
except ImportError:
    print("[ERROR] 'requests' belum terinstall. Jalankan: pip install requests")
    sys.exit(1)

try:
    import openpyxl
except ImportError:
    print("[ERROR] 'openpyxl' belum terinstall. Jalankan: pip install openpyxl")
    sys.exit(1)

DEFAULT_FILE = "DATA_UJI_BMA_CSW.xlsx"
DEFAULT_URL  = "http://127.0.0.1:5500/data"
DEFAULT_DELAY = 1.0  # detik antar kiriman aktual

def safe_float(v):
    if v is None:
        return None
    try:
        val = float(v)
        return val if math.isfinite(val) else None
    except Exception:
        return None

def main():
    parser = argparse.ArgumentParser(
        description="Simulator Replay Future_timestamp untuk pengujian BMA & CSW"
    )
    parser.add_argument("--file", "-f", default=DEFAULT_FILE, help="File Excel uji")
    parser.add_argument("--url", "-u", default=DEFAULT_URL, help="URL server")
    parser.add_argument("--delay", "-d", type=float, default=DEFAULT_DELAY, help="Delay antar request aktual (detik)")
    parser.add_argument("--start", type=int, default=1, help="Baris data mulai (1-based data row)")
    parser.add_argument("--end", type=int, default=None, help="Baris data selesai")
    parser.add_argument("--dry-run", action="store_true", help="Preview alur tanpa kirim HTTP")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    f_path = args.file if os.path.isabs(args.file) else os.path.join(base_dir, args.file)

    if not os.path.exists(f_path):
        print(f"[ERROR] File tidak ditemukan: {f_path}")
        return

    print("=" * 70)
    print("  SIMULATOR REPLAY DATA UJI DENGAN FUTURE_TIMESTAMP")
    print("=" * 70)
    print(f"  File Sumber : {f_path}")
    print(f"  Target URL  : {args.url}")
    print(f"  Delay       : {args.delay} detik")
    print(f"  Mode        : {'DRY RUN (Preview Saja)' if args.dry_run else 'KIRIM KE SERVER'}")
    print("=" * 70)

    wb = openpyxl.load_workbook(f_path, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    headers = rows[0]
    data_rows = rows[1:]
    total_data = len(data_rows)

    start_idx = max(1, args.start) - 1
    end_idx = min(total_data, args.end) if args.end else total_data
    selected_rows = data_rows[start_idx:end_idx]

    print(f"\n[INFO] Total baris data di Excel: {total_data}")
    print(f"[INFO] Memproses baris {start_idx + 1} s.d. {end_idx} ({len(selected_rows)} data)\n")

    sent_count = 0
    skip_count = 0
    bma_event_count = 0

    for i, r in enumerate(selected_rows, start=start_idx + 1):
        idx_val = safe_float(r[28])
        idx = int(idx_val) if idx_val is not None else None
        orig_ts = str(r[47]) if r[47] is not None else ""
        future_ts = str(r[48]) if r[48] is not None else None
        ch4 = safe_float(r[7])

        # KASUS A: DATA HILANG (Future_timestamp == None)
        if future_ts is None or future_ts == "" or future_ts == "None":
            skip_count += 1
            print(f"[{i:3d}/{total_data}] idx={idx:3d} | TS_Asli={orig_ts} | CH4={ch4} ppm")
            print(f"        -> [SKIP DATA HILANG] Baris ini sengaja dihilangkan (simulasi packet loss)!\n")
            continue

        # KASUS B: DATA AKTUAL (Future_timestamp tersedia)
        sent_count += 1
        
        # Susun payload persis ESP32 dengan Future_timestamp
        payload = {
            "device_id": str(r[21] or "ESP32_GAS_NODE"),
            "timestamp": future_ts,
            "Future_timestamp": future_ts,
            "original_timestamp": orig_ts,
            "idx": idx,
            "elapsed_s": safe_float(r[22]),
            "ch4_ppm": ch4,
            "temperature_c": safe_float(r[46]),
            "humidity_pct": safe_float(r[27]),
            "battery_v": safe_float(r[4]) or 0.0,
            "battery_pct": safe_float(r[3]) or 0.0,
            "adc_raw": safe_float(r[2]),
            "flow_status": str(r[25] or "FLOW_NOT_MEASURED"),
            "pump_control": str(r[30] or "DIGITAL_ON_OFF"),
            "system_status": str(r[40] or "SEND"),
        }

        print(f"[{i:3d}/{total_data}] idx={idx:3d} | Future_TS={future_ts} | CH4={ch4} ppm")

        if args.dry_run:
            print(f"        -> [DRY RUN] Payload siap dikirim ke server.\n")
            continue

        print(f"        -> Mengirim ke server...", end="", flush=True)
        t0 = time.perf_counter()
        try:
            resp = requests.post(args.url, json=payload, timeout=20)
            t_ms = (time.perf_counter() - t0) * 1000

            if resp.status_code == 200:
                resp_json = resp.json()
                gap_info = resp_json.get("gap_info") or {}
                status_gap = gap_info.get("status", "normal")
                missing_cnt = gap_info.get("missing_count", 0)
                gap_min = gap_info.get("gap_minutes", 0)
                proc_ms = resp_json.get("processing_latency_actual_ms")

                print(f" OK (200) [{t_ms:.1f}ms]")
                print(f"           Latency Server: {proc_ms} ms | Status Gap: {status_gap}")
                
                if "missing" in str(status_gap):
                    bma_event_count += 1
                    print(f"           [!] GAP TERDETEKSI! Jeda={gap_min:.1f} mnt | Missing={missing_cnt} slot -> BMA DIHASILKAN!")
                elif "cleaning" in str(status_gap):
                    print(f"           [i] Siklus Cleaning Terjadwal ({gap_min:.1f} mnt)")
                print()
            else:
                print(f" GAGAL ({resp.status_code}) [{t_ms:.1f}ms]")
                print(f"           Response: {resp.text[:150]}\n")

        except requests.exceptions.ConnectionError:
            print(" GAGAL — Server tidak dapat dihubungi!")
            print(f"           Pastikan SERVER2_FUTURE.py sudah aktif di {args.url}\n")
            break
        except Exception as e:
            print(f" ERROR: {e}\n")

        if args.delay > 0:
            time.sleep(args.delay)

    print("=" * 70)
    print("  SIMULASI REPLAY SELESAI")
    print("=" * 70)
    print(f"  Total Data Aktual Dikirim : {sent_count}")
    print(f"  Total Data Hilang Di-skip : {skip_count}")
    print(f"  Event Gap BMA Terdeteksi  : {bma_event_count}")
    print("=" * 70)

if __name__ == "__main__":
    main()

