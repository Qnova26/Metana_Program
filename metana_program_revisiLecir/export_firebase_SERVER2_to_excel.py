
import os
import pandas as pd
import firebase_admin

from firebase_admin import credentials, db
from datetime import datetime
from pandas import json_normalize


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SERVICE_ACCOUNT_FILE = os.path.join(
    BASE_DIR,
    "output-ch4-SERVER KE 2-firebase-adminsdk-fbsvc-144899e93c.json"
)

# GANTI dengan URL Firebase Realtime Database milikmu
DATABASE_URL = (
    "https://output-ch4-server2-default-rtdb."
    "asia-southeast1.firebasedatabase.app"
)

# Lokasi penyimpanan Excel
OUTPUT_FILE = os.path.join(
    BASE_DIR,
    "export_data_firebase.xlsx"
)


# ============================================================
# FIREBASE CONNECTION
# ============================================================

cred = credentials.Certificate(SERVICE_ACCOUNT_FILE)

firebase_admin.initialize_app(cred, {
    "databaseURL": DATABASE_URL
})

print("Firebase berhasil terhubung!")


# ============================================================
# FUNCTION: GET DATA FROM FIREBASE
# ============================================================

def get_firebase_data(node_name):

    print(f"\nMengambil data dari node: {node_name}")

    reference = db.reference(node_name)
    data = reference.get()

    if data is None:
        print(f"Node {node_name} kosong!")
        return pd.DataFrame()

    records = []

    if isinstance(data, dict):

        # Jika node berisi kumpulan record
        if any(isinstance(v, (dict, list)) for v in data.values()):

            for key, value in data.items():

                if isinstance(value, dict):
                    record = value.copy()
                    record["firebase_key"] = key
                    records.append(record)

                elif isinstance(value, list):
                    for i, item in enumerate(value):
                        if isinstance(item, dict):
                            record = item.copy()
                            record["firebase_key"] = key
                            record["item_index"] = i
                            records.append(record)

        # Jika node hanya berisi satu record
        else:
            records.append(data)

    elif isinstance(data, list):

        for i, item in enumerate(data):

            if isinstance(item, dict):
                record = item.copy()
                record["firebase_key"] = i
                records.append(record)

    if not records:
        return pd.DataFrame()

    # Mengubah JSON menjadi tabel
    df = json_normalize(records, sep="_")

    print(f"Berhasil mengambil {len(df)} record dari {node_name}")

    return df


# ============================================================
# EXPORT FIREBASE TO EXCEL
# ============================================================

# Daftar node Firebase yang akan diekspor
FIREBASE_NODES = [
    "data_terkini",
    "processed_12min",
    "problematic_12min",
    "historical_1hour"
]

with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:

    for node in FIREBASE_NODES:

        df = get_firebase_data(node)

        if not df.empty:

            # Nama sheet maksimal 31 karakter
            sheet_name = node[:31]

            df.to_excel(
                writer,
                sheet_name=sheet_name,
                index=False
            )

            print(f"Sheet {sheet_name} berhasil dibuat!")

        else:

            print(f"Tidak ada data untuk sheet {node}")


# ============================================================
# FINISH
# ============================================================

print("\n===================================")
print("EXPORT DATA FIREBASE SELESAI")
print("===================================")

print(f"File Excel tersimpan di:")
print(OUTPUT_FILE)
