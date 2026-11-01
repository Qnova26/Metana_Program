@'
import json
import re
from pathlib import Path
import firebase_admin
from firebase_admin import credentials, db

key_file = Path("output-ch4-SERVER KE 2-firebase-adminsdk-fbsvc-144899e93c.json")
server_file = Path("SERVER2.py")

if not key_file.exists() or not server_file.exists():
    raise SystemExit("DIBATALKAN: file kredensial atau SERVER2.py tidak ditemukan.")

with key_file.open(encoding="utf-8") as f:
    key_data = json.load(f)

if key_data.get("project_id") != "output-ch4-server2":
    raise SystemExit(
        "DIBATALKAN: project_id kredensial bukan output-ch4-server2. "
        "Project terdeteksi: " + str(key_data.get("project_id"))
    )

server_text = server_file.read_text(encoding="utf-8")
urls = re.findall(r'https?://[^\\s"\']+', server_text)
target_urls = [
    u.rstrip("),;")
    for u in urls
    if "output-ch4-server2-default-rtdb" in u
    and ("firebasedatabase.app" in u or "firebaseio.com" in u)
]

if not target_urls:
    raise SystemExit(
        "DIBATALKAN: URL database target tidak ditemukan di SERVER2.py. "
        "Tidak ada data yang dihapus."
    )

database_url = target_urls[0]
print("Project kredensial :", key_data["project_id"])
print("Database target    :", database_url)
print("Node yang dihapus  : processed_12min")

app = firebase_admin.initialize_app(
    credentials.Certificate(str(key_file)),
    {"databaseURL": database_url},
    name="cleanup_processed_12min"
)

try:
    ref = db.reference("processed_12min", app=app)
    ref.delete()
    print("SELESAI: node processed_12min sudah dihapus/dikosongkan.")
finally:
    firebase_admin.delete_app(app)
'@ | python -
