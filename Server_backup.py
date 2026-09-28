from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, db

from datetime import datetime, timezone, timedelta
import math
import threading

# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

# ============================================================
# FIREBASE
# ============================================================

SERVICE_ACCOUNT = "serviceAccountKey.json"

DATABASE_URL = (
    "https://data-monitoring-gas-metana-default-rtdb."
    "asia-southeast1.firebasedatabase.app/"
)

cred = credentials.Certificate(SERVICE_ACCOUNT)

firebase_admin.initialize_app(
    cred,
    {
        "databaseURL": DATABASE_URL
    }
)

# ============================================================
# KONFIGURASI PIPELINE
# ============================================================

EXPECTED_INTERVAL_SECONDS = 5

# Data dianggap terlambat/missing setelah melewati
# interval 5 detik + toleransi.
MISSING_TOLERANCE_SECONDS = 2

# Maksimal data sebelumnya untuk BMA.
BMA_WINDOW = 4

# Parameter yang digunakan dalam estimasi dan agregasi.
ESTIMATION_PARAMETERS = [
    "ch4_ppm",
    "humidity",
    "temperature"
]

# Parameter mentah yang tetap disimpan.
ALL_PARAMETERS = [
    "ch4_ppm",
    "humidity",
    "temperature",
    "battery_v",
    "battery_pct"
]

# ============================================================
# HISTORICAL WINDOW
# ============================================================

WINDOWS = {
    "1min": 60,
    "10min": 600,
    "1hour": 3600
}

# ============================================================
# LOCK
# ============================================================

# Karena Flask dapat menerima request secara bersamaan,
# lock digunakan agar proses buffer/recompute tidak bertabrakan.

data_lock = threading.Lock()

# ============================================================
# IN-MEMORY DATA STORE
# ============================================================

# Struktur:
#
# raw_records = {
#     "timestamp ISO": {
#         ...
#     }
# }
#
# RAW 5 detik merupakan source of truth middleware.

raw_records = {}

# Data terbaru berdasarkan EVENT TIMESTAMP.
latest_data = None

# ============================================================
# UTILITY TIMESTAMP
# ============================================================

def parse_timestamp(timestamp_string):
    """
    Mengubah timestamp ISO menjadi datetime timezone-aware.
    """

    if not isinstance(timestamp_string, str):
        raise ValueError("Timestamp harus berupa string")

    value = timestamp_string.strip()

    # Mendukung format berakhiran Z
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        raise ValueError(
            "Timestamp harus memiliki informasi timezone"
        )

    return dt

def normalize_timestamp(dt):
    """
    Mengubah datetime menjadi ISO UTC.
    """

    return dt.astimezone(timezone.utc).isoformat()

def timestamp_key(timestamp_string):
    """
    Firebase key tidak boleh mengandung:
    . # $ [ ]

    Maka timestamp diubah menjadi key aman.
    """

    return (
        timestamp_string
        .replace(".", "_")
        .replace(":", "-")
        .replace("+", "_plus_")
    )

# ============================================================
# DATA VALIDATION
# ============================================================

def validate_data(data):

    required_fields = [
        "device_id",
        "timestamp",
        "ch4_ppm",
        "humidity",
        "temperature",
        "battery_v",
        "battery_pct",
        "relay_status"
    ]

    # --------------------------------------------------------
    # REQUIRED FIELD
    # --------------------------------------------------------

    for field in required_fields:

        if field not in data:

            return False, (
                f"Field '{field}' tidak ditemukan"
            )

        if data[field] is None:

            return False, (
                f"Field '{field}' bernilai null"
            )

    # --------------------------------------------------------
    # DEVICE ID
    # --------------------------------------------------------

    if not isinstance(data["device_id"], str):

        return False, (
            "Field 'device_id' harus berupa string"
        )

    # --------------------------------------------------------
    # NUMERIC FIELD
    # --------------------------------------------------------

    for field in ALL_PARAMETERS:

        try:

            value = float(data[field])

            if not math.isfinite(value):

                return False, (
                    f"Field '{field}' bukan angka valid"
                )

        except (ValueError, TypeError):

            return False, (
                f"Field '{field}' harus berupa angka"
            )

    # --------------------------------------------------------
    # RELAY STATUS
    # --------------------------------------------------------

    relay = data["relay_status"]

    if isinstance(relay, str):

        if relay.lower() not in [
            "true",
            "false"
        ]:

            return False, (
                "Field 'relay_status' harus berupa "
                "true atau false"
            )

    elif not isinstance(relay, bool):

        return False, (
            "Field 'relay_status' harus berupa "
            "boolean atau string true/false"
        )

    # --------------------------------------------------------
    # TIMESTAMP
    # --------------------------------------------------------

    try:

        parse_timestamp(
            data["timestamp"]
        )

    except Exception as e:

        return False, (
            f"Timestamp tidak valid: {str(e)}"
        )

    return True, "Data valid"

# ============================================================
# STRUCTURE ACTUAL DATA
# ============================================================

def create_actual_record(
    data,
    event_timestamp,
    ingestion_timestamp,
    latency_ms
):

    record = {

        "device_id":
            data["device_id"],

        "timestamp":
            normalize_timestamp(
                event_timestamp
            ),

        "ingestion_timestamp":
            normalize_timestamp(
                ingestion_timestamp
            ),

        "latency_ms":
            round(latency_ms, 3),

        "ch4_ppm":
            round(float(data["ch4_ppm"]), 3),

        "humidity":
            round(float(data["humidity"]), 3),

        "temperature":
            round(float(data["temperature"]), 3),

        "battery_v":
            round(float(data["battery_v"]), 3),

        "battery_pct":
            round(float(data["battery_pct"]), 3),

        "relay_status":
            data["relay_status"],

        "data_status":
            "actual",

        "validation_status":
            "valid"
    }

    return record

# ============================================================
# CREATE ESTIMATED RECORD
# ============================================================

def create_estimated_record(
    timestamp,
    previous_records
):

    if not previous_records:

        return None

    estimated_values = {}

    # --------------------------------------------------------
    # Setiap parameter dihitung terpisah.
    #
    # Maksimal menggunakan 4 data sebelumnya yang tersedia.
    #
    # Data estimasi juga boleh digunakan apabila memang
    # diperlukan untuk menjaga kontinuitas.
    # --------------------------------------------------------

    for parameter in ESTIMATION_PARAMETERS:

        values = []

        for record in previous_records:

            value = record.get(parameter)

            if value is None:
                continue

            try:

                value = float(value)

                if math.isfinite(value):

                    values.append(value)

            except (ValueError, TypeError):

                continue

        if values:

            estimated_values[parameter] = round(
                sum(values) / len(values),
                3
            )

        else:

            estimated_values[parameter] = None

    # --------------------------------------------------------
    # VRL tidak diestimasi.
    # --------------------------------------------------------

    record = {

        "device_id":
            previous_records[-1].get(
                "device_id",
                "unknown"
            ),

        "timestamp":
            timestamp,

        "ingestion_timestamp":
            None,

        "latency_ms":
            None,

        "vrl":
            None,

        "ch4_ppm":
            estimated_values["ch4_ppm"],

        "humidity":
            estimated_values["humidity"],

        "temperature":
            estimated_values["temperature"],

        "data_status":
            "estimated",

        "validation_status":
            "generated_by_middleware"

    }

    return record

# ============================================================
# SAVE RAW RECORD
# ============================================================

def save_raw_record(record):

    ts = record["timestamp"]

    raw_records[ts] = record

    key = timestamp_key(ts)

    db.reference(
        f"raw_5sec/{key}"
    ).set(record)

# ============================================================
# GET SORTED RAW RECORDS
# ============================================================

def get_sorted_records():

    return sorted(
        raw_records.values(),
        key=lambda x: x["timestamp"]
    )

# ============================================================
# GET PREVIOUS RECORDS
# ============================================================

def get_previous_records(
    target_timestamp,
    limit=BMA_WINDOW
):

    target_dt = parse_timestamp(
        target_timestamp
    )

    records = []

    for record in raw_records.values():

        try:

            record_dt = parse_timestamp(
                record["timestamp"]
            )

        except Exception:

            continue

        if record_dt < target_dt:

            records.append(record)

    records.sort(
        key=lambda x: x["timestamp"]
    )

    # Ambil maksimal 4 record sebelumnya.
    return records[-limit:]

# ============================================================
# DETECT MISSING TIMESTAMPS
# ============================================================

def generate_missing_timestamps():

    records = get_sorted_records()

    if len(records) < 2:

        return []

    timestamps = []

    for record in records:

        try:

            timestamps.append(
                parse_timestamp(
                    record["timestamp"]
                )
            )

        except Exception:

            continue

    timestamps.sort()

    missing = []

    for i in range(len(timestamps) - 1):

        current = timestamps[i]

        next_time = timestamps[i + 1]

        expected = (
            current +
            timedelta(
                seconds=EXPECTED_INTERVAL_SECONDS
            )
        )

        # Cari seluruh gap.
        while expected < next_time:

            difference = (
                next_time - expected
            ).total_seconds()

            # Kalau gap memang lebih besar
            # dari interval + toleransi,
            # buat missing record.

            if difference >= 0:

                missing.append(
                    normalize_timestamp(expected)
                )

            expected += timedelta(
                seconds=EXPECTED_INTERVAL_SECONDS
            )

    return missing

# ============================================================
# FILL MISSING DATA
# ============================================================

def fill_missing_data():

    missing_timestamps = (
        generate_missing_timestamps()
    )

    generated = []

    for timestamp in missing_timestamps:

        # Jangan overwrite actual.
        if timestamp in raw_records:

            continue

        previous_records = (
            get_previous_records(
                timestamp,
                BMA_WINDOW
            )
        )

        if not previous_records:

            continue

        estimated_record = (
            create_estimated_record(
                timestamp,
                previous_records
            )
        )

        if estimated_record is None:

            continue

        save_raw_record(
            estimated_record
        )

        generated.append(
            estimated_record
        )

    return generated

# ============================================================
# LATEST
# ============================================================

def update_latest():

    global latest_data

    records = get_sorted_records()

    if not records:

        return None

    # Timestamp terbesar = event terbaru.
    latest_data = records[-1]

    db.reference(
        "latest"
    ).set(latest_data)

    return latest_data

# ============================================================
# WINDOW BOUNDARY
# ============================================================

def get_window_start(
    timestamp,
    window_seconds
):

    dt = parse_timestamp(timestamp)

    epoch = int(
        dt.timestamp()
    )

    start_epoch = (
        epoch // window_seconds
    ) * window_seconds

    start_dt = datetime.fromtimestamp(
        start_epoch,
        tz=timezone.utc
    )

    return start_dt

# ============================================================
# GET RECORDS IN WINDOW
# ============================================================

def get_records_in_window(
    window_start,
    window_seconds
):

    window_end = (
        window_start +
        timedelta(
            seconds=window_seconds
        )
    )


    records = []


    for record in raw_records.values():

        try:

            dt = parse_timestamp(
                record["timestamp"]
            )

        except Exception:

            continue


        if (
            dt >= window_start
            and dt < window_end
        ):

            records.append(record)


    records.sort(
        key=lambda x: x["timestamp"]
    )


    return records


# ============================================================
# AGGREGATE WINDOW
# ============================================================

def aggregate_window(
    window_name,
    window_start,
    records
):

    if not records:

        return None


    window_seconds = WINDOWS[
        window_name
    ]


    window_end = (
        window_start +
        timedelta(
            seconds=window_seconds
        )
    )


    result = {

        "window": window_name,

        "window_start":
            normalize_timestamp(
                window_start
            ),

        "window_end":
            normalize_timestamp(
                window_end
            ),

        "sample_count":
            len(records),

        "actual_count":
            sum(
                1
                for r in records
                if r.get("data_status")
                == "actual"
            ),

        "estimated_count":
            sum(
                1
                for r in records
                if r.get("data_status")
                == "estimated"
            )

    }


    # --------------------------------------------------------
    # AVERAGING SETIAP PARAMETER
    # --------------------------------------------------------

    for parameter in ESTIMATION_PARAMETERS:

        values = []

        for record in records:

            value = record.get(
                parameter
            )

            if value is None:
                continue

            try:

                value = float(value)

                if math.isfinite(value):

                    values.append(value)

            except (ValueError, TypeError):

                continue


        if values:

            result[parameter] = round(
                sum(values) / len(values),
                3
            )

        else:

            result[parameter] = None


    return result


# ============================================================
# SAVE HISTORICAL
# ============================================================

def save_historical(
    window_name,
    aggregate
):

    if aggregate is None:

        return


    window_start = (
        aggregate["window_start"]
    )


    key = timestamp_key(
        window_start
    )


    db.reference(
        f"historical/{window_name}/{key}"
    ).set(aggregate)


# ============================================================
# RECOMPUTE HISTORICAL WINDOW
# ============================================================

def recompute_window(
    window_name,
    timestamp
):

    window_seconds = WINDOWS[
        window_name
    ]


    window_start = get_window_start(
        timestamp,
        window_seconds
    )


    records = get_records_in_window(
        window_start,
        window_seconds
    )


    aggregate = aggregate_window(
        window_name,
        window_start,
        records
    )


    if aggregate:

        save_historical(
            window_name,
            aggregate
        )


    return aggregate


# ============================================================
# RECOMPUTE ALL HISTORICAL WINDOWS
# ============================================================

def recompute_all_historical():

    records = get_sorted_records()

    if not records:

        return


    for window_name in WINDOWS:

        window_seconds = WINDOWS[
            window_name
        ]


        affected_windows = set()


        for record in records:

            window_start = (
                get_window_start(
                    record["timestamp"],
                    window_seconds
                )
            )


            affected_windows.add(
                normalize_timestamp(
                    window_start
                )
            )


        for window_start_string in affected_windows:

            aggregate = aggregate_window(

                window_name,

                parse_timestamp(
                    window_start_string
                ),

                get_records_in_window(

                    parse_timestamp(
                        window_start_string
                    ),

                    window_seconds
                )
            )


            if aggregate:

                save_historical(
                    window_name,
                    aggregate
                )


# ============================================================
# RECOMPUTE AFFECTED WINDOWS
# ============================================================

def recompute_affected_windows(
    timestamp
):

    results = {}


    for window_name in WINDOWS:

        aggregate = recompute_window(
            window_name,
            timestamp
        )

        results[
            window_name
        ] = aggregate


    return results


# ============================================================
# CENTERED SLIDING ESTIMATION
# ============================================================

def centered_estimate(
    target_timestamp
):

    target_dt = parse_timestamp(
        target_timestamp
    )


    records = get_sorted_records()


    before = []

    after = []


    for record in records:

        try:

            dt = parse_timestamp(
                record["timestamp"]
            )

        except Exception:

            continue


        if dt < target_dt:

            before.append(record)

        elif dt > target_dt:

            after.append(record)


    before.sort(
        key=lambda x: x["timestamp"]
    )

    after.sort(
        key=lambda x: x["timestamp"]
    )


    before = before[-BMA_WINDOW:]

    after = after[:BMA_WINDOW]


    # --------------------------------------------------------
    # Minimal requirement:
    #
    # Untuk centered reconstruction kita ingin
    # mendapatkan data setelah missing.
    #
    # Namun data estimated tetap boleh digunakan.
    # --------------------------------------------------------

    if not before:

        return None


    result = {

        "timestamp":
            normalize_timestamp(
                target_dt
            ),

        "data_status":
            "estimated",

        "estimation_method":
            "centered_sliding_window"

    }


    for parameter in ESTIMATION_PARAMETERS:

        values = []


        for record in before + after:

            value = record.get(
                parameter
            )

            if value is None:
                continue

            try:

                value = float(value)

                if math.isfinite(value):

                    values.append(value)

            except (ValueError, TypeError):

                continue


        if values:

            result[parameter] = round(
                sum(values) / len(values),
                3
            )

        else:

            result[parameter] = None


    return result


# ============================================================
# UPDATE ESTIMATION DENGAN CENTERED WINDOW
# ============================================================

def update_centered_estimation(
    timestamp
):

    if timestamp not in raw_records:

        return None


    record = raw_records[
        timestamp
    ]


    # Kalau sudah actual, tidak perlu estimasi.
    if record.get(
        "data_status"
    ) == "actual":

        return record


    centered = centered_estimate(
        timestamp
    )


    if centered is None:

        return record


    # Pertahankan metadata.
    record["ch4_ppm"] = (
        centered["ch4_ppm"]
    )

    record["humidity"] = (
        centered["humidity"]
    )

    record["temperature"] = (
        centered["temperature"]
    )

    record["estimation_method"] = (
        "centered_sliding_window"
    )


    save_raw_record(
        record
    )


    return record


# ============================================================
# HANDLE LATE ACTUAL DATA
# ============================================================

def handle_late_data(
    actual_record
):

    timestamp = (
        actual_record["timestamp"]
    )


    old_record = (
        raw_records.get(
            timestamp
        )
    )


    # --------------------------------------------------------
    # Belum pernah ada.
    # --------------------------------------------------------

    if old_record is None:

        save_raw_record(
            actual_record
        )

        return {
            "late": False,
            "replaced_estimation": False
        }


    # --------------------------------------------------------
    # Jika sebelumnya estimated,
    # actual menggantikannya.
    # --------------------------------------------------------

    if old_record.get(
        "data_status"
    ) == "estimated":

        save_raw_record(
            actual_record
        )


        affected = (
            recompute_affected_windows(
                timestamp
            )
        )


        return {

            "late": True,

            "replaced_estimation": True,

            "affected_windows":
                affected

        }


    # --------------------------------------------------------
    # Kalau sudah actual,
    # update saja jika data baru memang actual.
    # --------------------------------------------------------

    save_raw_record(
        actual_record
    )


    affected = (
        recompute_affected_windows(
            timestamp
        )
    )


    return {

        "late": True,

        "replaced_estimation": False,

        "affected_windows":
            affected

    }


# ============================================================
# INGESTION ROUTE
# ============================================================

@app.route(
    "/data",
    methods=["POST"]
)
@app.route(
    "/data",
    methods=["POST"]
)
def receive_data():

    global latest_data


    with data_lock:

        # ====================================================
        # 1. DATA INGESTION
        # ====================================================

        data = request.get_json(
            silent=True
        )


        if data is None:

            return jsonify({

                "status": "error",

                "message":
                    "Request harus menggunakan JSON"

            }), 400


        print("\n========================================")
        print("DATA MASUK")
        print("========================================")
        print(data)


        # ====================================================
        # 2. VALIDATION
        # ====================================================

        valid, message = (
            validate_data(data)
        )


        if not valid:

            print(
                "VALIDATION ERROR:",
                message
            )


            return jsonify({

                "status": "error",

                "message": message

            }), 400

        # ====================================================
        # 3. TIMESTAMP & INGESTION
        # ====================================================

        # Timestamp berasal dari ESP32
        event_timestamp = parse_timestamp(
         data["timestamp"]
        )

        # Timestamp saat data diterima middleware
        ingestion_timestamp = datetime.now(
         timezone.utc
        )
        
        # ====================================================
        # 4. LATENCY
        # ====================================================

        # Selisih waktu event dengan waktu diterima server
        latency_ms = (
        ingestion_timestamp -
        event_timestamp.astimezone(timezone.utc)
        ).total_seconds() * 1000

        # ====================================================
        # 5. CREATE ACTUAL RECORD
        # ====================================================

        actual_record = (
    create_actual_record(
        data,
        event_timestamp,
        ingestion_timestamp,
        latency_ms
    )
)

        timestamp = (
            actual_record["timestamp"]
        )
        # ====================================================
        # 6. CEK LATE DATA
        # ====================================================

        existing = (
            raw_records.get(
                timestamp
            )
        )


        if existing is not None:

            result = (
                handle_late_data(
                    actual_record
                )
            )


            # LATEST tetap berdasarkan event timestamp.
            update_latest()


            print(
                "\nLATE DATA TERDETEKSI"
            )


            print(
                result
            )


            return jsonify({

                "status": "success",

                "message":
                    "Late data berhasil diproses",

                "data":
                    actual_record,

                "late_data":
                    result,

                "latest":
                    latest_data

            })


        # ====================================================
        # 7. SIMPAN ACTUAL KE RAW
        # ====================================================

        save_raw_record(
            actual_record
        )


        # ====================================================
        # 8. DETEKSI DAN ISI MISSING
        # ====================================================

        generated_missing = (
            fill_missing_data()
        )


        if generated_missing:

            print(
                "\nMISSING DATA DIBUAT:"
            )

            for record in generated_missing:

                print(
                    record["timestamp"]
                )


        # ====================================================
        # 9. CENTERED ESTIMATION
        #
        # Setiap missing yang sekarang sudah memiliki
        # data setelahnya dapat diperbaiki menggunakan
        # centered sliding window.
        # ====================================================

        missing_timestamps = (
            generate_missing_timestamps()
        )


        centered_updated = []


        for missing_timestamp in (
            missing_timestamps
        ):

            updated = (
                update_centered_estimation(
                    missing_timestamp
                )
            )


            if updated:

                centered_updated.append(
                    updated
                )


        # ====================================================
        # 10. UPDATE LATEST
        # ====================================================

        update_latest()


        # ====================================================
        # 11. RECOMPUTE HISTORICAL
        #
        # Semua historical dihitung langsung dari
        # RAW 5 detik.
        # ====================================================

        affected_windows = (
            recompute_affected_windows(
                timestamp
            )
        )


        # ====================================================
        # 12. RESPONSE
        # ====================================================

        print(
            "\nDATA BERHASIL DIPROSES"
        )


        print(
            "Timestamp:",
            timestamp
        )


        print(
            "Latency:",
            round(
                latency_ms,
                3
            ),
            "ms"
        )


        return jsonify({

            "status": "success",

            "message":
                "Data berhasil diproses",

            "data":
                actual_record,

            "latest":
                latest_data,

            "missing_generated":
                generated_missing,

            "centered_updated":
                centered_updated,

            "historical":
                affected_windows

        })

# ============================================================
# HOME
# ============================================================

@app.route(
    "/",
    methods=["GET"]
)
def home():

    return jsonify({

        "status": "success",

        "message":
            "Flask IoT Data Pipeline aktif",

        "routes": [

            "POST /data",

            "GET /latest",

            "GET /historical/1min",

            "GET /historical/10min",

            "GET /historical/hourly",

            "GET /test-firebase",

            "POST /test/simulate"

        ]

    })


# ============================================================
# GET LATEST
# ============================================================

@app.route(
    "/latest",
    methods=["GET"]
)
def get_latest():

    return jsonify({

        "status": "success",

        "data":
            latest_data

    })


# ============================================================
# GET HISTORICAL
# ============================================================

def get_historical_endpoint(
    window_name
):

    ref = db.reference(
        f"historical/{window_name}"
    )


    data = ref.get()


    if data is None:

        data = {}


    return jsonify({

        "status": "success",

        "window":
            window_name,

        "data":
            data

    })


@app.route(
    "/historical/1min",
    methods=["GET"]
)
def historical_1min():

    return get_historical_endpoint(
        "1min"
    )


@app.route(
    "/historical/10min",
    methods=["GET"]
)
def historical_10min():

    return get_historical_endpoint(
        "10min"
    )


@app.route(
    "/historical/hourly",
    methods=["GET"]
)
def historical_hourly():

    return get_historical_endpoint(
        "1hour"
    )


# ============================================================
# TEST FIREBASE
# ============================================================

@app.route(
    "/test-firebase",
    methods=["GET"]
)
def test_firebase():

    timestamp = normalize_timestamp(
        datetime.now(
            timezone.utc
        )
    )


    db.reference(
        "system/test_connection"
    ).set({

        "status":
            "Firebase berhasil terhubung",

        "timestamp":
            timestamp

    })


    return jsonify({

        "status":
            "success",

        "message":
            "Firebase berhasil terhubung"

    })


# ============================================================
# SIMULATION
# ============================================================

@app.route(
    "/test/simulate",
    methods=["POST"]
)
def simulate_data():

    """
    Endpoint untuk testing.

    Contoh JSON:

    {
        "timestamp":
            "2026-08-18T22:00:15+07:00",

        "vrl": 1.247,

        "ch4_ppm": 124.91,

        "humidity": 72.4,

        "temperature": 28.6
    }
    """


    fake_data = (
        request.get_json(
            silent=True
        )
    )


    if fake_data is None:

        return jsonify({

            "status": "error",

            "message":
                "JSON tidak ditemukan"

        }), 400


    # Tambahkan device ID jika tidak ada.
    if "device_id" not in fake_data:

        fake_data["device_id"] = (
            "TEST_DEVICE"
        )


    # Memanggil endpoint processing secara internal
    # tidak dilakukan dengan HTTP.
    #
    # Jadi kita proses menggunakan fungsi yang sama.


    with data_lock:

        valid, message = (
            validate_data(
                fake_data
            )
        )


        if not valid:

            return jsonify({

                "status": "error",

                "message": message

            }), 400


        event_timestamp = (
            parse_timestamp(
                fake_data["timestamp"]
            )
        )


        ingestion_timestamp = (
            datetime.now(
                timezone.utc
            )
        )


        latency_ms = (
            ingestion_timestamp -
            event_timestamp.astimezone(
                timezone.utc
            )
        ).total_seconds() * 1000


        record = (
    create_actual_record(
        fake_data,
        event_timestamp,
        ingestion_timestamp,
        latency_ms
    )
)

        timestamp = (
            record["timestamp"]
        )


        existing = (
            raw_records.get(
                timestamp
            )
        )


        # Late data
        if existing is not None:

            result = (
                handle_late_data(
                    record
                )
            )

        else:

            save_raw_record(
                record
            )

            generated = (
                fill_missing_data()
            )

            missing_timestamps = (
                generate_missing_timestamps()
            )


            centered = []


            for missing_timestamp in (
                missing_timestamps
            ):

                updated = (
                    update_centered_estimation(
                        missing_timestamp
                    )
                )


                if updated:

                    centered.append(
                        updated
                    )


            result = {

                "late": False,

                "generated_missing":
                    generated,

                "centered_updated":
                    centered

            }


        update_latest()


        historical = (
            recompute_affected_windows(
                timestamp
            )
        )


    return jsonify({

        "status":
            "success",

        "simulation":
            True,

        "record":
            record,

        "result":
            result,

        "latest":
            latest_data,

        "historical":
            historical

    })


# ============================================================
# LOAD EXISTING RAW DATA
# ============================================================

def load_raw_data():

    global raw_records
    global latest_data


    try:

        data = db.reference(
            "raw_5sec"
        ).get()


        if not data:

            print(
                "RAW DATABASE KOSONG"
            )

            return


        loaded = 0


        for record in data.values():

            if not isinstance(
                record,
                dict
            ):

                continue


            timestamp = record.get(
                "timestamp"
            )


            if timestamp:

                raw_records[
                    timestamp
                ] = record

                loaded += 1


        print(
            f"{loaded} RAW RECORD berhasil dimuat"
        )


        update_latest()


    except Exception as e:

        print(
            "Gagal load RAW:",
            str(e)
        )


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    print(
        "\n========================================"
    )

    print(
        "MEMUAT DATA RAW DARI FIREBASE..."
    )

    print(
        "========================================"
    )


    load_raw_data()


    print(
        "\n========================================"
    )

    print(
        "ROUTE YANG TERDAFTAR"
    )

    print(
        "========================================"
    )


    print(
        app.url_map
    )


    print(
        "\n========================================"
    )

    print(
        "SERVER FLASK DIMULAI"
    )

    print(
        "========================================"
    )


    print(
        "\nLocal:"
    )

    print(
        "http://127.0.0.1:5000"
    )


    print(
        "\nLAN:"
    )

    print(
        "http://192.168.0.106:5000"
    )


    print(
        "\nEndpoint Joan:"
    )

    print(
        "POST http://192.168.0.106:5000/data"
    )


    print(
        "\n========================================\n"
    )


    app.run(

        host="0.0.0.0",

        port=5000,

        debug=True,

        threaded=True

    )
