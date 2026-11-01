
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime, timezone, timedelta

SERVICE_ACCOUNT = (
    "pembacaan-sensor-ch4-firebase-adminsdk-fbsvc-1db5a3a79d.json"
)

DATABASE_URL = (
    "https://pembacaan-sensor-ch4-default-rtdb."
    "asia-southeast1.firebasedatabase.app/"
)

NODE = "hardware_data"

IDX_AWAL = 610
IDX_AKHIR = 630  # Total 21 data, termasuk kedua batas

WIB = timezone(timedelta(hours=7))

if not firebase_admin._apps:
    cred = credentials.Certificate(SERVICE_ACCOUNT)
    firebase_admin.initialize_app(
        cred, {"databaseURL": DATABASE_URL}
    )

records = db.reference(NODE).get() or {}
rows = []

def collect(obj):
    if not isinstance(obj, dict):
        return

    if obj.get("timestamp") is not None:
        try:
            ts = datetime.fromisoformat(
                str(obj["timestamp"]).replace("Z", "+00:00")
            )
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=WIB)

            idx = obj.get("idx")
            rows.append({
                "timestamp": ts,
                "idx": idx,
                "system_status": obj.get("system_status"),
                "firebase_key": obj.get("firebase_key"),
            })
        except (ValueError, TypeError):
            pass

    for value in obj.values():
        if isinstance(value, dict):
            collect(value)

collect(records)

def idx_number(value):
    try:
        return int(value)
    except (ValueError, TypeError):
        return None

selected = [
    r for r in rows
    if idx_number(r["idx"]) is not None
    and IDX_AWAL <= idx_number(r["idx"]) <= IDX_AKHIR
]

# Urutkan berdasarkan idx
selected.sort(key=lambda r: idx_number(r["idx"]))

print(f"\nDATA idx {IDX_AWAL} sampai {IDX_AKHIR}")
print("=" * 100)

previous = None

for r in selected:
    ts_wib = r["timestamp"].astimezone(WIB)
    gap = ""

    if previous is not None:
        seconds = (
            r["timestamp"] - previous["timestamp"]
        ).total_seconds()

        minutes = int(abs(seconds) // 60)
        remaining_seconds = int(abs(seconds) % 60)

        if seconds < 0:
            gap = "WAKTU MUNDUR"
        else:
            gap = f"{minutes} menit {remaining_seconds} detik"

    print(
        f"idx={str(r['idx']):>5} | "
        f"{ts_wib.strftime('%Y-%m-%d %H:%M:%S')} WIB | "
        f"gap={gap or '-'} | "
        f"status={r['system_status']}"
    )

    previous = r

if not selected:
    print("Tidak ada data pada rentang idx tersebut.")
    print("Periksa nama node dan struktur data Firebase.")
