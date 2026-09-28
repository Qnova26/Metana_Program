import time
import pandas as pd
from datetime import datetime, timezone

# ============================================================
# 1. DATA INGESTION
# ============================================================

raw_data = "Suhu: 24.0 C | RH: 40.0 % | V_ADC: 1.10 V | V_Out: 2.75 V |"

print("=== DATA INGESTION ===")
print(raw_data)

# ============================================================
# 2. PARSING DATA
# ============================================================

def parsing_data(raw_data):

    parts = raw_data.split("|")

    data = {}

    for part in parts:

        part = part.strip()

        if part.startswith("Suhu:"):
            data["temperature"] = float(
                part.replace("Suhu:", "")
                    .replace("C", "")
                    .strip()
            )

        elif part.startswith("RH:"):
            data["humidity"] = float(
                part.replace("RH:", "")
                    .replace("%", "")
                    .strip()
            )

        elif part.startswith("V_ADC:"):
            data["v_adc"] = float(
                part.replace("V_ADC:", "")
                    .replace("V", "")
                    .strip()
            )

        elif part.startswith("V_Out:"):
            data["v_out"] = float(
                part.replace("V_Out:", "")
                    .replace("V", "")
                    .strip()
            )

    return data

# ============================================================
# 5. KONVERSI / KALIBRASI V_OUT → CH4
# ============================================================

def convert_to_ch4(data):

    v_out = data["v_out"]

    # Koefisien kalibrasi
    # INI MASIH CONTOH
    REGRESSION_M = 1500
    REGRESSION_C = -500

    ch4_ppm = (REGRESSION_M * v_out) + REGRESSION_C

    data["ch4_ppm"] = ch4_ppm

    return data

# ============================================================
# 7. BUFFER DATA
# ============================================================

data_buffer = []

# Tempat menyimpan hasil setiap window 5 data
processed_dataset = []

# ============================================================
# 8. SIMULASI DATA JOAN
# ============================================================

for i in range(10):

    # --------------------------------------------------------
    # DATA YANG SEOLAH-OLAH DIKIRIM JOAN
    # --------------------------------------------------------

    raw_data = (
        f"Suhu: {24 + i * 0.1:.1f} C | "
        f"RH: {40 + i * 0.2:.1f} % | "
        f"V_ADC: {1.10 + i * 0.01:.2f} V | "
        f"V_Out: {2.75 + i * 0.01:.2f} V |"
    )

    print("\n================================")
    print(f"DATA KE-{i + 1}")
    print("================================")

    print(raw_data)

    # --------------------------------------------------------
    # PARSING
    # --------------------------------------------------------

    data = parsing_data(raw_data)

    # --------------------------------------------------------
    # TIMESTAMP
    # --------------------------------------------------------

    data["timestamp"] = datetime.now(timezone.utc).isoformat()

    # --------------------------------------------------------
    # KONVERSI CH4
    # --------------------------------------------------------

    data = convert_to_ch4(data)

    # --------------------------------------------------------
    # MASUKKAN DATA KE BUFFER
    # --------------------------------------------------------

    data_buffer.append(data)

    print("CH4:", data["ch4_ppm"], "ppm")

    # --------------------------------------------------------
    # CEK APAKAH SUDAH 5 DATA
    # --------------------------------------------------------

    if len(data_buffer) == 5:

        print("\n=== SUDAH 5 DATA ===")

        # ----------------------------------------------------
        # UBAH BUFFER MENJADI DATAFRAME
        # ----------------------------------------------------

        df = pd.DataFrame(data_buffer)

        # ----------------------------------------------------
        # BUAT PROCESSED DATA
        # ----------------------------------------------------

        processed_data = {

            "window_start": df["timestamp"].iloc[0],

            "window_end": df["timestamp"].iloc[-1],

            "temperature_avg": df["temperature"].mean(),

            "humidity_avg": df["humidity"].mean(),

            "v_adc_avg": df["v_adc"].mean(),

            "v_out_avg": df["v_out"].mean(),

            "ch4_avg": df["ch4_ppm"].mean(),

            "sample_count": len(df)
        }

        # ----------------------------------------------------
        # SIMPAN HASIL PROCESSED DATA
        # ----------------------------------------------------

        processed_dataset.append(processed_data)

        # ----------------------------------------------------
        # TAMPILKAN HASIL
        # ----------------------------------------------------

        print("\n=== PROCESSED DATA ===")

        for key, value in processed_data.items():

            print(f"{key}: {value}")

        # ----------------------------------------------------
        # KOSONGKAN BUFFER
        # UNTUK WINDOW BERIKUTNYA
        # ----------------------------------------------------

        data_buffer = []

    # --------------------------------------------------------
    # SIMULASI DATA MASUK SETIAP 1 DETIK
    # --------------------------------------------------------

    time.sleep(1)

# ============================================================
# 9. DATASET PROCESSED
# ============================================================

print("\n================================")
print("DATASET PROCESSED")
print("================================")

df_processed = pd.DataFrame(processed_dataset)

print(df_processed)