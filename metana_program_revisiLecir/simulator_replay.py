"""
SIMULATOR REPLAY DATA SENSOR METANA (EXCEL TO FLASK SERVER)
Digunakan untuk menguji pipeline Server (BMA, CSW, Gap Detection)
dengan data riil historis dari file Excel.
"""

import os
import sys
import time
import argparse
import requests
import pandas as pd
from datetime import datetime

DEFAULT_EXCEL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "hasil_sensor_26 OKT.xlsx"
)
DEFAULT_SERVER_URL = "http://127.0.0.1:5500/data"


def clean_payload(row_dict):
    """
    Membersihkan NaN dan format kolom agar kompatibel dengan JSON payload Flask.
    """
    payload = {}
    for k, v in row_dict.items():
        if pd.isna(v) or v is None:
            continue
        if str(k).startswith("Unnamed:"):
            continue
        
        # Format angka jika perlu
        if isinstance(v, (int, float)):
            if pd.isna(v):
                continue
            payload[k] = float(v) if isinstance(v, float) else int(v)
        else:
            payload[k] = str(v)
            
    # Pastikan default minimal
    if "device_id" not in payload:
        payload["device_id"] = "ESP32_GAS_NODE"
    if "battery_v" not in payload:
        payload["battery_v"] = 8.0
    if "battery_pct" not in payload:
        payload["battery_pct"] = 90
        
    return payload


def run_replay(excel_file, server_url, start_row=1, end_row=None, delay=0.3):
    print("=" * 60)
    print("METANA PIPELINE - REPLAY SIMULATOR")
    print("=" * 60)
    print(f"File Excel : {excel_file}")
    print(f"Target URL : {server_url}")
    print(f"Delay/req  : {delay} detik")
    print("=" * 60)

    if not os.path.exists(excel_file):
        print(f"ERROR: File {excel_file} tidak ditemukan!")
        return

    df = pd.read_excel(excel_file)
    total_data = len(df)
    print(f"Total baris di Excel: {total_data}")

    start_idx = max(0, start_row - 1)
    end_idx = min(total_data, end_row) if end_row else total_data

    df_subset = df.iloc[start_idx:end_idx]
    print(f"Rentang baris yang diproses: {start_idx + 1} s.d. {end_idx} ({len(df_subset)} data)\n")

    success_count = 0
    fail_count = 0
    bma_count = 0

    log_rows = []

    for i, (_, row) in enumerate(df_subset.iterrows(), start=start_idx + 1):
        payload = clean_payload(row.to_dict())
        ts = payload.get("timestamp", "-")
        idx_val = payload.get("idx", "-")
        ch4 = payload.get("ch4_ppm", "-")

        print(f"[{i:4d}/{end_idx}] Mengirim idx={idx_val} | TS={ts} | CH4={ch4} ppm ... ", end="", flush=True)

        t0 = time.time()
        try:
            resp = requests.post(server_url, json=payload, timeout=20)
            latency = round((time.time() - t0) * 1000, 1)

            if resp.status_code == 200:
                success_count += 1
                res_json = resp.json()
                bma_res = res_json.get("bma_results", [])
                gap_info = res_json.get("gap_info") or {}
                gap_status = gap_info.get("status", "normal")
                
                bma_msg = ""
                if bma_res:
                    bma_count += len(bma_res)
                    bma_msg = f" -> [BMA GENERATED {len(bma_res)} SLOT!]"
                
                print(f"OK ({resp.status_code}) [{latency}ms] Status: {gap_status}{bma_msg}")
            else:
                fail_count += 1
                print(f"GAGAL ({resp.status_code}) [{latency}ms]: {resp.text[:100]}")

        except Exception as e:
            fail_count += 1
            print(f"ERROR: {str(e)[:80]}")

        if delay > 0:
            time.sleep(delay)

    print("\n" + "=" * 60)
    print("REPLAY SELESAI")
    print(f"Sukses Diterima : {success_count}")
    print(f"Gagal           : {fail_count}")
    print(f"Slot BMA Dibuat : {bma_count}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulator Replay Data Excel ke Flask")
    parser.add_argument("--file", default=DEFAULT_EXCEL, help="Path ke file Excel data sensor")
    parser.add_argument("--url", default=DEFAULT_SERVER_URL, help="URL Flask /data endpoint")
    parser.add_argument("--start", type=int, default=1, help="Baris mulai di Excel (1-based)")
    parser.add_argument("--end", type=int, default=None, help="Baris akhir di Excel")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay antar-pengiriman (detik)")

    args = parser.parse_args()
    run_replay(
        excel_file=args.file,
        server_url=args.url,
        start_row=args.start,
        end_row=args.end,
        delay=args.delay
    )

