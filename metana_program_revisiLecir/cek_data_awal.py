import firebase_admin
from firebase_admin import credentials, db

SERVICE_ACCOUNT_FILE = "pembacaan-sensor-ch4-firebase-adminsdk-fbsvc-1db5a3a79d.json"
DATABASE_URL = "https://pembacaan-sensor-ch4-default-rtdb.asia-southeast1.firebasedatabase.app"

cred = credentials.Certificate(SERVICE_ACCOUNT_FILE)
firebase_admin.initialize_app(cred, {"databaseURL": DATABASE_URL})

data = db.reference("hardware_data").get() or {}

records = []
for key, value in data.items():
    if isinstance(value, dict) and value.get("timestamp"):
        records.append({
            "timestamp": value.get("timestamp"),
            "idx": value.get("idx"),
            "firebase_key": key
        })

records.sort(key=lambda x: x["timestamp"])

print(f"Jumlah data: {len(records)}")
print("\n15 DATA PALING AWAL:")
for r in records[:15]:
    print(r)

firebase_admin.delete_app(firebase_admin.get_app())
