
import json
from collections import defaultdict
from pathlib import Path

FILE_JSON = Path("output-ch4-server2-default-rtdb-export.json")

NODES = [
    "processed_12min",
    "problematic_12min",
    "historical_1hour",
]

with FILE_JSON.open("r", encoding="utf-8") as f:
    database = json.load(f)

for node_name in NODES:
    node = database.get(node_name) or {}

    # Kelompokkan record berdasarkan timestamp
    by_timestamp = defaultdict(list)

    for firebase_key, record in node.items():
        if not isinstance(record, dict):
            continue

        timestamp = record.get("timestamp")

        if timestamp:
            by_timestamp[timestamp].append({
                "firebase_key": firebase_key,
                "device_id": record.get("device_id"),
                "idx": record.get("idx"),
                "data_status": record.get("data_status"),
            })

    duplicates = {
        ts: records
        for ts, records in by_timestamp.items()
        if len(records) > 1
    }

    print(f"\n===== {node_name} =====")
    print(f"Jumlah record: {len(node)}")
    print(f"Timestamp unik: {len(by_timestamp)}")
    print(f"Timestamp berulang: {len(duplicates)}")

    for timestamp, records in duplicates.items():
        print(f"\nTimestamp: {timestamp}")
        for record in records:
            print(" ", record)

print("\nPemeriksaan selesai.")
