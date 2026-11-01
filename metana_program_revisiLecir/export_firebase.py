import requests
import pandas as pd
import json
from pathlib import Path
from datetime import datetime


URL = "https://pembacaan-sensor-ch4-default-rtdb.asia-southeast1.firebasedatabase.app/hardware_data.json"

# 1. Ambil data dari Firebase
response = requests.get(URL, timeout=60)
response.raise_for_status()
data = response.json()

# 2. Pastikan data tersedia
if not isinstance(data, dict) or not data:
    raise ValueError(
        "Data hardware_data kosong atau formatnya tidak sesuai. "
        "Backup dibatalkan."
    )

# 3. Simpan backup JSON mentah terlebih dahulu
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
json_path = Path(f"backup_hardware_data_{timestamp}.json")
excel_path = Path(f"hasil_sensor_{timestamp}.xlsx")

json_path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2),
    encoding="utf-8"
)

# 4. Ubah ke Excel dengan firebase key sebagai kolom
df = pd.DataFrame.from_dict(data, orient="index")
df.index.name = "firebase_key"
df = df.reset_index()

df.to_excel(excel_path, index=False)

# 5. Verifikasi file backup
with json_path.open("r", encoding="utf-8") as f:
    backup_data = json.load(f)

if len(backup_data) != len(data):
    raise RuntimeError("Verifikasi jumlah record JSON gagal.")

print("Backup selesai.")
print(f"Jumlah record: {len(data)}")
print(f"File JSON: {json_path.resolve()}")
print(f"File Excel: {excel_path.resolve()}")
print(f"Ukuran JSON: {json_path.stat().st_size:,} byte")
print(f"Ukuran Excel: {excel_path.stat().st_size:,} byte")
