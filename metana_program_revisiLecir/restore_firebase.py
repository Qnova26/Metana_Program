import json
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, db

# Konfigurasi
BACKUP_FILE = Path("backup_hardware_data_FINAL_20261026_135155.json")
SERVICE_ACCOUNT_FILE = Path(
    "pembacaan-sensor-ch4-firebase-adminsdk-fbsvc-1db5a3a79d.json"
)
DATABASE_URL = (
    "https://pembacaan-sensor-ch4-default-rtdb."
    "asia-southeast1.firebasedatabase.app"
)
NODE_PATH = "hardware_data"
TARGET_ID = "-P4qy3Hg4bCG-WlbPxZP"

# Pastikan file tersedia
if not BACKUP_FILE.is_file():
    raise FileNotFoundError(f"Cadangan tidak ditemukan: {BACKUP_FILE}")

if not SERVICE_ACCOUNT_FILE.is_file():
    raise FileNotFoundError(
        f"Service account tidak ditemukan: {SERVICE_ACCOUNT_FILE}"
    )

# Baca dan validasi cadangan sebelum menyentuh Firebase
with BACKUP_FILE.open("r", encoding="utf-8") as f:
    backup = json.load(f)

if not isinstance(backup, dict) or not backup:
    raise ValueError("Struktur cadangan kosong atau tidak sesuai.")

if TARGET_ID not in backup:
    raise ValueError(f"ID target {TARGET_ID} tidak ada dalam cadangan.")

if not all(isinstance(k, str) and isinstance(v, dict)
           for k, v in backup.items()):
    raise ValueError("Struktur record cadangan tidak valid.")

print("Jumlah record cadangan:", len(backup))
print("ID target ada di cadangan:", TARGET_ID in backup)

# Hubungkan ke Firebase
if not firebase_admin._apps:
    cred = credentials.Certificate(str(SERVICE_ACCOUNT_FILE))
    firebase_admin.initialize_app(
        cred, {"databaseURL": DATABASE_URL}
    )

ref = db.reference(NODE_PATH)

# Periksa kondisi terbaru sebelum pemulihan
current = ref.get()

if current is None:
    current = {}
elif not isinstance(current, dict):
    raise ValueError(
        f"Node {NODE_PATH} bukan objek data. Pemulihan dibatalkan."
    )

missing = {
    key: value
    for key, value in backup.items()
    if key not in current
}

print("Record yang sudah ada di Firebase:", len(current))
print("Record yang akan dipulihkan:", len(missing))

# Tambahkan hanya ID yang belum ada.
# Record yang sudah ada tidak ditimpa.
if missing:
    ref.update(missing)

# Verifikasi hasil
restored = ref.get() or {}
print("\n=== HASIL PEMULIHAN ===")
print("Jumlah record Firebase sekarang:", len(restored))
print("ID target ditemukan:", TARGET_ID in restored)
print(
    "Data ID target sesuai cadangan:",
    restored.get(TARGET_ID) == backup[TARGET_ID]
)
print(
    "Semua ID dari cadangan tersedia:",
    all(key in restored for key in backup)
)
print("Selesai.")
