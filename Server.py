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

try:
    firebase_admin.get_app()
except ValueError:
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

# ESP32 diharapkan mengirim data setiap 5 detik
EXPECTED_INTERVAL_SECONDS = 5

# Toleransi timestamp sebelum dianggap sebagai missing
MISSING_TOLERANCE_SECONDS = 2


# ============================================================
# KONFIGURASI BMA
# ============================================================

# BMA menggunakan 4 actual-valid sebelumnya
BMA_WINDOW = 4


# ============================================================
# KONFIGURASI CSW
# ============================================================

# CSW menggunakan:
# 2 actual-valid terdekat sebelum
# +
# 2 actual-valid terdekat sesudah

CSW_BEFORE = 2
CSW_AFTER = 2


# ============================================================
# PARAMETER UTAMA PENELITIAN
# ============================================================

ESTIMATION_PARAMETERS = [
    "ch4_ppm",
    "humidity",
    "temperature"
]


# ============================================================
# SEMUA PARAMETER NUMERIK
# ============================================================

ALL_PARAMETERS = [
    "ch4_ppm",
    "humidity",
    "temperature",
    "battery_v",
    "battery_pct"
]


# ============================================================
# PARAMETER METADATA WAJIB
# ============================================================

REQUIRED_METADATA_FIELDS = [
    "device_id",
    "timestamp",
    "battery_v",
    "battery_pct",
    "relay_status"
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

data_lock = threading.Lock()


# ============================================================
# IN-MEMORY DATA STORE
# ============================================================

# raw_records:
# HANYA actual-valid / actual sensor data
#
# processed_records:
# actual + estimated
#
# Dengan pemisahan ini:
# raw_5sec tetap menjadi sumber data aktual,
# sedangkan processed_5sec berisi timeline hasil middleware.

raw_records = {}
processed_records = {}

latest_data = None


# ============================================================
# UTILITY TIMESTAMP
# ============================================================

def parse_timestamp(timestamp_string):
    """
    Mengubah timestamp ESP32 menjadi datetime timezone-aware.

    Format yang diterima:

    2026-09-15 16:40:00
    2026-09-15T16:40:00+07:00
    2026-09-15T09:40:00Z

    Timestamp tanpa timezone dianggap WIB (UTC+7).
    """

    if not isinstance(timestamp_string, str):
        raise ValueError(
            "Timestamp harus berupa string"
        )

    value = timestamp_string.strip()

    if not value:
        raise ValueError(
            "Timestamp tidak boleh kosong"
        )

    # --------------------------------------------------------
    # FORMAT Z
    # --------------------------------------------------------

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    # --------------------------------------------------------
    # FORMAT TANPA TIMEZONE
    # --------------------------------------------------------

    if (
        "+" not in value[10:]
        and "-" not in value[10:]
    ):
        try:

            dt = datetime.strptime(
                value,
                "%Y-%m-%d %H:%M:%S"
            )

            WIB = timezone(
                timedelta(hours=7)
            )

            return dt.replace(
                tzinfo=WIB
            )

        except ValueError:
            pass

    # --------------------------------------------------------
    # FORMAT ISO
    # --------------------------------------------------------

    try:

        dt = datetime.fromisoformat(
            value
        )

    except ValueError:

        raise ValueError(
            "Format timestamp tidak valid"
        )

    if dt.tzinfo is None:

        WIB = timezone(
            timedelta(hours=7)
        )

        dt = dt.replace(
            tzinfo=WIB
        )

    return dt


# ============================================================
# NORMALIZE TIMESTAMP
# ============================================================

def normalize_timestamp(dt):
    """
    Mengubah datetime menjadi ISO UTC.
    """

    return dt.astimezone(
        timezone.utc
    ).isoformat()


# ============================================================
# FIREBASE TIMESTAMP KEY
# ============================================================

def timestamp_key(timestamp_string):
    """
    Membuat timestamp aman digunakan sebagai Firebase key.
    """

    return (
        timestamp_string
        .replace(".", "_")
        .replace(":", "-")
        .replace("+", "_plus_")
    )


# ============================================================
# RELAY STATUS
# ============================================================

def parse_relay_status(value):
    """
    Menerima:

    true
    false

    maupun:

    "true"
    "false"
    """

    if isinstance(value, bool):
        return value

    if isinstance(value, str):

        value = value.strip().lower()

        if value == "true":
            return True

        if value == "false":
            return False

    raise ValueError(
        "Field 'relay_status' harus berupa true/false"
    )


# ============================================================
# CEK ANGKA VALID
# ============================================================

def is_finite_number(value):

    try:

        number = float(value)

        return math.isfinite(number)

    except (
        ValueError,
        TypeError
    ):

        return False


# ============================================================
# CEK ACTUAL-VALID
# ============================================================

def is_actual_valid(record):
    """
    Hanya record dengan:

    data_status = actual
    validation_status = valid

    yang boleh digunakan sebagai referensi
    BMA dan CSW.
    """

    return (
        record.get("data_status") == "actual"
        and
        record.get("validation_status") == "valid"
    )


# ============================================================
# VALIDASI DASAR
# ============================================================

def validate_basic_data(data):

    if not isinstance(data, dict):

        return False, (
            "Data harus berupa JSON object"
        )

    # --------------------------------------------------------
    # REQUIRED METADATA
    # --------------------------------------------------------

    for field in REQUIRED_METADATA_FIELDS:

        if field not in data:

            return False, (
                f"Field '{field}' tidak ditemukan"
            )

    # --------------------------------------------------------
    # DEVICE ID
    # --------------------------------------------------------

    if not isinstance(
        data["device_id"],
        str
    ):

        return False, (
            "Field 'device_id' harus berupa string"
        )

    if not data["device_id"].strip():

        return False, (
            "Field 'device_id' tidak boleh kosong"
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

    # --------------------------------------------------------
    # BATTERY
    # --------------------------------------------------------

    if not is_finite_number(
        data["battery_v"]
    ):

        return False, (
            "Field 'battery_v' harus berupa angka valid"
        )

    if not is_finite_number(
        data["battery_pct"]
    ):

        return False, (
            "Field 'battery_pct' harus berupa angka valid"
        )

    # --------------------------------------------------------
    # RELAY
    # --------------------------------------------------------

    try:

        parse_relay_status(
            data["relay_status"]
        )

    except Exception as e:

        return False, str(e)

    return True, "Data dasar valid"


# ============================================================
# DETEKSI NULL
# ============================================================

def detect_null_parameters(data):

    null_parameters = []

    for parameter in ALL_PARAMETERS:

        if data.get(parameter) is None:

            null_parameters.append(
                parameter
            )

    return null_parameters


# ============================================================
# DETEKSI NON-NUMERIC
# ============================================================

def detect_invalid_numeric_parameters(data):

    invalid_parameters = []

    for parameter in ALL_PARAMETERS:

        value = data.get(parameter)

        if value is None:
            continue

        if not is_finite_number(value):

            invalid_parameters.append(
                parameter
            )

    return invalid_parameters


# ============================================================
# GET ACTUAL VALID VALUES
# ============================================================

def get_actual_valid_values(
    parameter,
    exclude_timestamp=None,
    limit=None
):

    values = []

    for record in raw_records.values():

        if not is_actual_valid(record):
            continue

        if (
            exclude_timestamp is not None
            and
            record.get("timestamp")
            == exclude_timestamp
        ):
            continue

        value = record.get(parameter)

        if is_finite_number(value):

            values.append(
                (
                    record["timestamp"],
                    float(value)
                )
            )

    values.sort(
        key=lambda x:
            parse_timestamp(x[0])
    )

    if limit is not None:
        values = values[-limit:]

    return [
        value
        for _, value in values
    ]

# ============================================================
# DETEKSI ANOMALY
# ============================================================

def detect_anomalies(
    data,
    timestamp
):

    anomalies = []

    for parameter in ESTIMATION_PARAMETERS:

        value = data.get(parameter)

        if value is None:
            continue

        if not is_finite_number(value):
            continue

        # ----------------------------------------------------
        # Ambil seluruh actual-valid history
        # untuk mengecek apakah detector sudah siap
        # ----------------------------------------------------

        all_historical_values = (
            get_actual_valid_values(
                parameter,
                exclude_timestamp=timestamp
            )
        )

        # ----------------------------------------------------
        # Belum cukup data → jangan deteksi anomaly
        # ----------------------------------------------------

        if len(all_historical_values) < MIN_ANOMALY_SAMPLES:
            continue

        # ----------------------------------------------------
        # Setelah cukup → gunakan moving baseline
        # ----------------------------------------------------

        historical_values = (
            all_historical_values[
                -ANOMALY_BASELINE_WINDOW:
            ]
        )

        if is_anomaly_iqr(
            value,
            historical_values
        ):

            anomalies.append(parameter)

    return anomalies

# ============================================================
# PERCENTILE
# ============================================================

def percentile(
    values,
    percentile_value
):

    if not values:
        return None

    values = sorted(
        float(v)
        for v in values
    )

    if len(values) == 1:
        return values[0]

    position = (
        (len(values) - 1)
        *
        percentile_value
    )

    lower = int(
        math.floor(position)
    )

    upper = int(
        math.ceil(position)
    )

    if lower == upper:
        return values[lower]

    weight = position - lower

    return (
        values[lower]
        +
        (
            values[upper]
            -
            values[lower]
        )
        *
        weight
    )

# ============================================================
# ANOMALY DETECTION CONFIGURATION
# ============================================================

# Jumlah actual-valid record sebelumnya yang digunakan
# untuk menghitung baseline.
ANOMALY_BASELINE_WINDOW = 10

MIN_ANOMALY_SAMPLES = 20

# Threshold perubahan nilai.
# Threshold berlaku untuk:
#   1. deviasi terhadap baseline
#   2. perubahan terhadap actual-valid terakhir
ANOMALY_THRESHOLD = {
    "ch4_ppm": 20.0,
    "humidity": 10.0,
    "temperature": 2.0
}

# Nilai 0 dianggap anomaly sesuai aturan sistem.
ZERO_IS_ANOMALY = True

# Sentinel error dari perangkat.
# Isi sesuai protokol hardware jika sudah ditentukan.
#
# Contoh:
# "ch4_ppm": {-999},
#
# Untuk sementara dikosongkan agar tidak menganggap
# angka tertentu sebagai error tanpa dasar dari hardware.
INVALID_SENTINEL_VALUES = {
    "ch4_ppm": set(),
    "humidity": set(),
    "temperature": set()
}

# ============================================================
# IQR ANOMALY DETECTION
# ============================================================

def is_anomaly_iqr(
    value,
    historical_values
):

    if not is_finite_number(value):
        return False

    # Minimal data agar IQR dapat dihitung
    if len(historical_values) < 4:
        return False

    q1 = percentile(
        historical_values,
        0.25
    )

    q3 = percentile(
        historical_values,
        0.75
    )

    if q1 is None or q3 is None:
        return False

    iqr = q3 - q1

    # Jika seluruh data memiliki nilai sama
    if iqr == 0:

        return (
            float(value) != q1
        )

    lower_bound = (
        q1
        -
        1.5 * iqr
    )

    upper_bound = (
        q3
        +
        1.5 * iqr
    )

    return (
        float(value) < lower_bound
        or
        float(value) > upper_bound
    )

# ============================================================
# CREATE ACTUAL RECORD
# ============================================================

def create_actual_record(
    data,
    ingestion_timestamp,
    latency_ms
):

    event_dt = parse_timestamp(
        data["timestamp"]
    )

    event_timestamp = (
        normalize_timestamp(
            event_dt
        )
    )

    relay_status = (
        parse_relay_status(
            data["relay_status"]
        )
    )

    record = {

        "device_id":
            data["device_id"],

        "timestamp":
            event_timestamp,

        "ingestion_timestamp":
            normalize_timestamp(
                ingestion_timestamp
            ),

        "latency_ms":
            round(
                latency_ms,
                3
            ),

        "ch4_ppm":
            None,

        "humidity":
            None,

        "temperature":
            None,

        "battery_v":
            None,

        "battery_pct":
            None,

        "relay_status":
            relay_status,

        "data_status":
            "actual",

        "validation_status":
            "valid"

    }

    for parameter in ALL_PARAMETERS:

        value = data.get(
            parameter
        )

        if value is None:
            continue

        if not is_finite_number(value):
            continue

        record[parameter] = round(
            float(value),
            3
        )

    return record


# ============================================================
# CREATE BMA ESTIMATED RECORD
# ============================================================

def create_bma_estimated_record(
    timestamp,
    previous_records,
    source_reason="missing"
):

    # BMA harus memiliki 4 actual-valid
    if len(previous_records) < BMA_WINDOW:
        return None

    estimated_values = {}

    bma_values = {}

    # --------------------------------------------------------
    # BMA
    # --------------------------------------------------------

    for parameter in ESTIMATION_PARAMETERS:

        values = []

        for record in previous_records:

            if not is_actual_valid(record):
                continue

            value = record.get(
                parameter
            )

            if is_finite_number(value):

                values.append(
                    float(value)
                )

        if len(values) < BMA_WINDOW:

            return None

        estimated_value = (
            sum(values)
            /
            len(values)
        )

        estimated_values[parameter] = round(
            estimated_value,
            3
        )

        bma_values[parameter] = round(
            estimated_value,
            3
        )

    # --------------------------------------------------------
    # DEVICE ID
    # --------------------------------------------------------

    device_id = (
        previous_records[-1].get(
            "device_id",
            "unknown"
        )
    )

    # --------------------------------------------------------
    # RECORD
    # --------------------------------------------------------

    record = {

        "device_id":
            device_id,

        "timestamp":
            timestamp,

        "ingestion_timestamp":
            None,

        "latency_ms":
            None,

        "ch4_ppm":
            estimated_values[
                "ch4_ppm"
            ],

        "humidity":
            estimated_values[
                "humidity"
            ],

        "temperature":
            estimated_values[
                "temperature"
            ],

        "battery_v":
            None,

        "battery_pct":
            None,

        "relay_status":
            None,

        "data_status":
            "estimated",

        "validation_status":
            "generated_by_middleware",

        "estimation_method":
            "BMA",

        "bma_window":
            BMA_WINDOW,

        "estimation_reason":
            source_reason,

        "bma_ch4_ppm":
            bma_values["ch4_ppm"],

        "bma_humidity":
            bma_values["humidity"],

        "bma_temperature":
            bma_values["temperature"]

    }

    return record


# ============================================================
# GET SORTED ACTUAL RECORDS
# ============================================================

def get_sorted_actual_records():

    return sorted(
        raw_records.values(),
        key=lambda x:
            parse_timestamp(
                x["timestamp"]
            )
    )


# ============================================================
# GET SORTED PROCESSED RECORDS
# ============================================================

def get_sorted_processed_records():

    return sorted(
        processed_records.values(),
        key=lambda x:
            parse_timestamp(
                x["timestamp"]
            )
    )


# ============================================================
# GET ACTUAL VALID SEBELUM TARGET
# ============================================================

def get_previous_actual_valid_records(
    target_timestamp,
    limit=BMA_WINDOW
):

    target_dt = parse_timestamp(
        target_timestamp
    )

    records = []

    for record in raw_records.values():

        if not is_actual_valid(record):
            continue

        try:

            record_dt = parse_timestamp(
                record["timestamp"]
            )

        except Exception:

            continue

        if record_dt < target_dt:

            records.append(record)

    records.sort(
        key=lambda x:
            parse_timestamp(
                x["timestamp"]
            )
    )

    return records[-limit:]


# ============================================================
# GET ACTUAL VALID TERDEKAT SEBELUM TARGET
# ============================================================

def get_actual_valid_before(
    target_timestamp,
    count=CSW_BEFORE
):

    target_dt = parse_timestamp(
        target_timestamp
    )

    records = []

    for record in raw_records.values():

        if not is_actual_valid(record):
            continue

        try:

            record_dt = parse_timestamp(
                record["timestamp"]
            )

        except Exception:

            continue

        if record_dt < target_dt:

            records.append(record)

    records.sort(
        key=lambda x:
            parse_timestamp(
                x["timestamp"]
            ),
        reverse=True
    )

    return records[:count]


# ============================================================
# GET ACTUAL VALID TERDEKAT SESUDAH TARGET
# ============================================================

def get_actual_valid_after(
    target_timestamp,
    count=CSW_AFTER
):

    target_dt = parse_timestamp(
        target_timestamp
    )

    records = []

    for record in raw_records.values():

        if not is_actual_valid(record):
            continue

        try:

            record_dt = parse_timestamp(
                record["timestamp"]
            )

        except Exception:

            continue

        if record_dt > target_dt:

            records.append(record)

    records.sort(
        key=lambda x:
            parse_timestamp(
                x["timestamp"]
            )
    )

    return records[:count]


# ============================================================
# SAVE ACTUAL RECORD
# ============================================================

def save_actual_record(record):

    timestamp = record["timestamp"]

    raw_records[timestamp] = record
    processed_records[timestamp] = record

    key = timestamp_key(
        timestamp
    )

    # --------------------------------------------------------
    # RAW DATA
    # --------------------------------------------------------

    db.reference(
        f"raw_5sec/{key}"
    ).set(record)

    # --------------------------------------------------------
    # PROCESSED DATA
    # --------------------------------------------------------

    db.reference(
        f"processed_5sec/{key}"
    ).set(record)


# ============================================================
# SAVE ESTIMATED RECORD
# ============================================================

def save_estimated_record(record):

    timestamp = record["timestamp"]

    processed_records[timestamp] = record

    key = timestamp_key(
        timestamp
    )

    db.reference(
        f"processed_5sec/{key}"
    ).set(record)


# ============================================================
# SAVE PROBLEMATIC INPUT
# ============================================================

def save_problematic_input(
    data,
    timestamp,
    problematic_parameters,
    reason
):

    record = {

        "device_id":
            data.get(
                "device_id"
            ),

        "timestamp":
            timestamp,

        "problematic_parameters":
            problematic_parameters,

        "reason":
            reason,

        "received_at":
            normalize_timestamp(
                datetime.now(
                    timezone.utc
                )
            )

    }

    for parameter in ESTIMATION_PARAMETERS:

        value = data.get(parameter)

        if value is None:

            record[
                f"original_{parameter}"
            ] = None

        elif is_finite_number(value):

            record[
                f"original_{parameter}"
            ] = round(
                float(value),
                3
            )

        else:

            record[
                f"original_{parameter}"
            ] = str(value)

    key = timestamp_key(
        timestamp
    )

    db.reference(
        f"problematic_5sec/{key}"
    ).set(record)


# ============================================================
# DETECT MISSING TIMESTAMPS
# ============================================================

def generate_missing_timestamps():

    records = get_sorted_actual_records()

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

    for i in range(
        len(timestamps) - 1
    ):

        current = timestamps[i]
        next_time = timestamps[i + 1]

        expected = (
            current
            +
            timedelta(
                seconds=
                EXPECTED_INTERVAL_SECONDS
            )
        )

        while (
            expected < next_time
            and
            (
                next_time
                -
                expected
            ).total_seconds()
            >
            MISSING_TOLERANCE_SECONDS
        ):

            missing.append(
                normalize_timestamp(
                    expected
                )
            )

            expected += timedelta(
                seconds=
                EXPECTED_INTERVAL_SECONDS
            )

    return list(
        dict.fromkeys(
            missing
        )
    )


# ============================================================
# FILL MISSING DATA DENGAN BMA
# ============================================================

def fill_missing_data():

    missing_timestamps = (
        generate_missing_timestamps()
    )

    generated = []

    for timestamp in missing_timestamps:

        # Jika sudah ada di processed,
        # tidak perlu dibuat ulang.
        if timestamp in processed_records:
            continue

        # ----------------------------------------------------
        # Ambil 4 actual-valid sebelumnya
        # ----------------------------------------------------

        previous_records = (
            get_previous_actual_valid_records(
                timestamp,
                BMA_WINDOW
            )
        )

        # ----------------------------------------------------
        # BMA membutuhkan 4 actual-valid
        # ----------------------------------------------------

        if len(previous_records) < BMA_WINDOW:
            continue

        # ----------------------------------------------------
        # Buat estimasi
        # ----------------------------------------------------

        estimated_record = (
            create_bma_estimated_record(
                timestamp,
                previous_records,
                source_reason="missing"
            )
        )

        if estimated_record is None:
            continue

        # ----------------------------------------------------
        # Simpan sebagai processed
        # BUKAN raw
        # ----------------------------------------------------

        save_estimated_record(
            estimated_record
        )

        generated.append(
            estimated_record
        )

    return generated


# ============================================================
# APPLY BMA PADA NULL / ANOMALY
# ============================================================

def apply_bma_to_problematic_record(
    record,
    problematic_parameters,
    reason
):

    timestamp = record["timestamp"]

    previous_records = (
        get_previous_actual_valid_records(
            timestamp,
            BMA_WINDOW
        )
    )

    # --------------------------------------------------------
    # Harus tersedia 4 actual-valid
    # --------------------------------------------------------

    if len(previous_records) < BMA_WINDOW:

        return None

    # --------------------------------------------------------
    # Hitung BMA
    # --------------------------------------------------------

    bma_record = (
        create_bma_estimated_record(
            timestamp,
            previous_records,
            source_reason=reason
        )
    )

    if bma_record is None:
        return None

    # --------------------------------------------------------
    # Hanya parameter bermasalah yang diganti
    # --------------------------------------------------------

    for parameter in problematic_parameters:

        record[parameter] = (
            bma_record.get(parameter)
        )

    # --------------------------------------------------------
    # Simpan nilai BMA awal
    # --------------------------------------------------------

    record["bma_ch4_ppm"] = (
        bma_record["bma_ch4_ppm"]
    )

    record["bma_humidity"] = (
        bma_record["bma_humidity"]
    )

    record["bma_temperature"] = (
        bma_record["bma_temperature"]
    )

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    record["data_status"] = "estimated"

    record["validation_status"] = (
        "generated_by_middleware"
    )

    record["estimation_method"] = "BMA"

    record["bma_window"] = (
        BMA_WINDOW
    )

    record["estimation_reason"] = (
        reason
    )

    record["problematic_parameters"] = (
        problematic_parameters
    )

    return record


# ============================================================
# CSW ESTIMATION
# ============================================================

def csw_estimate(
    target_timestamp
):

    # --------------------------------------------------------
    # 2 actual-valid terdekat sebelum
    # --------------------------------------------------------

    before = get_actual_valid_before(
        target_timestamp,
        CSW_BEFORE
    )

    # --------------------------------------------------------
    # 2 actual-valid terdekat sesudah
    # --------------------------------------------------------

    after = get_actual_valid_after(
        target_timestamp,
        CSW_AFTER
    )

    # --------------------------------------------------------
    # Kedua sisi harus lengkap
    # --------------------------------------------------------

    if (
        len(before) < CSW_BEFORE
        or
        len(after) < CSW_AFTER
    ):

        return None

    result = {

        "timestamp":
            normalize_timestamp(
                parse_timestamp(
                    target_timestamp
                )
            ),

        "data_status":
            "estimated",

        "validation_status":
            "generated_by_middleware",

        "estimation_method":
            "CSW",

        "csw_before_count":
            len(before),

        "csw_after_count":
            len(after)

    }

    # --------------------------------------------------------
    # CSW:
    #
    # [2 actual-valid sebelum
    #  +
    #  2 actual-valid sesudah] / 4
    # --------------------------------------------------------

    for parameter in ESTIMATION_PARAMETERS:

        values = []

        # ----------------------------------------------------
        # BEFORE
        # ----------------------------------------------------

        for record in before:

            value = record.get(
                parameter
            )

            if is_finite_number(value):

                values.append(
                    float(value)
                )

        # ----------------------------------------------------
        # AFTER
        # ----------------------------------------------------

        for record in after:

            value = record.get(
                parameter
            )

            if is_finite_number(value):

                values.append(
                    float(value)
                )

        # ----------------------------------------------------
        # Harus lengkap 4 nilai
        # ----------------------------------------------------

        if len(values) < (
            CSW_BEFORE + CSW_AFTER
        ):

            result[parameter] = None

        else:

            result[parameter] = round(
                sum(values)
                /
                len(values),
                3
            )

    return result


# ============================================================
# UPDATE ESTIMASI DENGAN CSW
# ============================================================

def update_csw_estimation(estimated_record):
    """
    Mengganti hasil BMA dengan CSW hanya pada parameter
    yang sebelumnya bermasalah.

    processed_records menggunakan dictionary:
        {
            timestamp: record
        }

    CSW menggunakan:
    - 2 actual-valid terdekat sebelum
    - 2 actual-valid terdekat sesudah

    Parameter yang sudah actual-valid tidak diubah.
    """
    if estimated_record.get( "csw_applied" ) is True:
        return False
    
    timestamp = estimated_record.get("timestamp")

    if not timestamp:
        return False

    # =========================================================
    # AMBIL PARAMETER YANG MEMANG BERMASALAH
    # =========================================================

    problematic_parameters = estimated_record.get(
        "problematic_parameters",
        []
    )

    # Jika tidak ada parameter bermasalah,
    # tidak ada yang perlu di-CSW.
    #
    # Untuk missing record, problematic_parameters
    # biasanya belum ada. Missing akan ditangani
    # sebagai seluruh parameter utama.
    if not problematic_parameters:
        problematic_parameters = (
            ESTIMATION_PARAMETERS.copy()
        )

    # =========================================================
    # CARI ACTUAL-VALID SEBELUM DAN SESUDAH
    # =========================================================

    before_records = get_actual_valid_before(
        timestamp,
        CSW_BEFORE
    )

    after_records = get_actual_valid_after(
        timestamp,
        CSW_AFTER
    )

    # CSW membutuhkan kedua sisi lengkap
    if (
        len(before_records) < CSW_BEFORE
        or
        len(after_records) < CSW_AFTER
    ):
        return False

    # =========================================================
    # COPY RECORD
    # =========================================================

    updated_record = estimated_record.copy()

    csw_updated = False
    applied_parameters = []

    # =========================================================
    # HITUNG CSW
    # =========================================================

    for param in problematic_parameters:

        # Hanya parameter penelitian yang boleh
        # diestimasi menggunakan CSW.
        if param not in ESTIMATION_PARAMETERS:
            continue

        values = []

        # -----------------------------------------------------
        # 2 ACTUAL-VALID SEBELUM
        # -----------------------------------------------------

        for record in before_records:

            value = record.get(param)

            if (
                is_actual_valid(record)
                and
                is_valid_numeric(value)
            ):

                values.append(
                    float(value)
                )

        # -----------------------------------------------------
        # 2 ACTUAL-VALID SESUDAH
        # -----------------------------------------------------

        for record in after_records:

            value = record.get(param)

            if (
                is_actual_valid(record)
                and
                is_valid_numeric(value)
            ):

                values.append(
                    float(value)
                )

        # -----------------------------------------------------
        # HARUS LENGKAP 4 NILAI
        # -----------------------------------------------------

        if len(values) < (
            CSW_BEFORE + CSW_AFTER
        ):
            continue

        # -----------------------------------------------------
        # RUMUS CSW
        # -----------------------------------------------------

        csw_value = round(
            sum(values) / len(values),
            3
        )

        # -----------------------------------------------------
        # GANTI NILAI PARAMETER
        # -----------------------------------------------------

        updated_record[param] = csw_value

        updated_record[
            f"csw_{param}"
        ] = csw_value

        csw_updated = True

        applied_parameters.append(param)

    # =========================================================
    # JIKA ADA CSW YANG BERHASIL
    # =========================================================

    if not csw_updated:
        return False

    # =========================================================
    # UPDATE METADATA ESTIMASI
    # =========================================================

    updated_record["estimation_method"] = "CSW"

    updated_record["csw_applied"] = True

    updated_record[
        "csw_applied_timestamp"
    ] = normalize_timestamp(
        datetime.now(timezone.utc)
    )

    updated_record[
        "csw_applied_parameters"
    ] = applied_parameters

    updated_record[
        "csw_before_count"
    ] = len(before_records)

    updated_record[
        "csw_after_count"
    ] = len(after_records)

    # --------------------------------------------------------
    # Simpan hasil CSW
    # --------------------------------------------------------

    save_estimated_record(
        updated_record
    )

    return True

# ============================================================
# UPDATE SEMUA ESTIMASI YANG SUDAH BISA CSW
# ============================================================

def update_all_possible_csw():

    global processed_records

    updated_records = []

    # --------------------------------------------------------
    # Ambil snapshot semua estimated record
    #
    # processed_records berbentuk dictionary:
    # {
    #     timestamp: record
    # }
    #
    # Gunakan list() / copy agar aman ketika
    # update_csw_estimation() mengubah processed_records.
    # --------------------------------------------------------

    estimated_records = [
        record.copy()
        for record in processed_records.values()
        if record.get("data_status") == "estimated"
    ]

    # --------------------------------------------------------
    # Urutkan berdasarkan event timestamp
    # --------------------------------------------------------

    estimated_records.sort(
        key=lambda record: parse_timestamp(
            record["timestamp"]
        )
    )

    # --------------------------------------------------------
    # Coba lakukan CSW pada setiap estimated record
    # --------------------------------------------------------

    for estimated_record in estimated_records:

        timestamp = estimated_record.get("timestamp")

        if timestamp is None:
            continue

        # update_csw_estimation() menerima RECORD,
        # bukan timestamp.
        #
        # Return:
        # True  = CSW berhasil diterapkan
        # False = CSW belum dapat dilakukan
        csw_updated = update_csw_estimation(
            estimated_record
        )

        # ----------------------------------------------------
        # Jika CSW berhasil, ambil record terbaru
        # dari processed_records
        # ----------------------------------------------------

        if csw_updated:

            updated_record = processed_records.get(
                timestamp
            )

            if updated_record is not None:

                updated_records.append(
                    updated_record.copy()
                )

    # --------------------------------------------------------
    # Return semua record yang berhasil diperbarui CSW
    # --------------------------------------------------------

    return updated_records

# ============================================================
# EVALUASI ESTIMASI TERHADAP LATE ACTUAL
# ============================================================

def calculate_estimation_errors(
    estimated_record,
    actual_record
):

    evaluation = {}

    for parameter in ESTIMATION_PARAMETERS:

        actual_value = (
            actual_record.get(
                parameter
            )
        )

        if not is_finite_number(
            actual_value
        ):

            continue

        actual_value = float(
            actual_value
        )

        # ----------------------------------------------------
        # Error BMA
        # ----------------------------------------------------

        bma_value = (
            estimated_record.get(
                f"bma_{parameter}"
            )
        )

        if is_finite_number(
            bma_value
        ):

            bma_value = float(
                bma_value
            )

            evaluation[
                f"bma_{parameter}_ae"
            ] = round(
                abs(
                    actual_value
                    -
                    bma_value
                ),
                3
            )

        # ----------------------------------------------------
        # Error CSW
        # ----------------------------------------------------

        csw_value = (
            estimated_record.get(
                f"csw_{parameter}"
            )
        )

        if is_finite_number(
            csw_value
        ):

            csw_value = float(
                csw_value
            )

            evaluation[
                f"csw_{parameter}_ae"
            ] = round(
                abs(
                    actual_value
                    -
                    csw_value
                ),
                3
            )

    return evaluation


# ============================================================
# HANDLE LATE DATA
# ============================================================

def is_valid_numeric(value):
    """
    Mengecek apakah value merupakan angka numerik yang valid.
    """

    if value is None:
        return False

    try:
        value = float(value)

        if math.isnan(value):
            return False

        if math.isinf(value):
            return False

        return True

    except (TypeError, ValueError):
        return False

# ============================================================
# RECOMPUTE BMA AKIBAT LATE ACTUAL
# ============================================================

def recompute_bma_affected_by_late_actual(
    late_actual_record
):
    """
    Menghitung ulang BMA pada estimated records yang
    dapat terpengaruh oleh masuknya late actual.

    processed_records selalu berbentuk dictionary:
        {
            timestamp: record
        }

    Late actual hanya dapat memengaruhi estimated record
    yang timestamp-nya berada setelah late actual.

    Referensi BMA:
    - hanya actual-valid
    - berasal dari raw_records
    - mengambil 4 actual-valid terdekat sebelum target
    """

    late_dt = parse_timestamp(
        late_actual_record["timestamp"]
    )

    # =========================================================
    # CARI ESTIMATED RECORD YANG TERDAMPAK
    # =========================================================

    affected_records = []

    for record in processed_records.values():

        if record.get("data_status") != "estimated":
            continue

        try:

            record_dt = parse_timestamp(
                record["timestamp"]
            )

        except Exception:

            continue

        # Hanya estimated record setelah
        # late actual.
        if record_dt <= late_dt:
            continue

        affected_records.append(
            record.copy()
        )

    # =========================================================
    # URUTKAN BERDASARKAN TIMESTAMP
    # =========================================================

    affected_records.sort(
        key=lambda r:
            parse_timestamp(
                r["timestamp"]
            )
    )

    recomputed_count = 0

    # =========================================================
    # HITUNG ULANG SATU PER SATU
    # =========================================================

    for estimated_record in affected_records:

        timestamp = estimated_record["timestamp"]

        # -----------------------------------------------------
        # AMBIL 4 ACTUAL-VALID SEBELUM TARGET
        # -----------------------------------------------------

        previous_actual_valid = (
            get_previous_actual_valid_records(
                timestamp,
                BMA_WINDOW
            )
        )

        if len(previous_actual_valid) < BMA_WINDOW:
            continue

        # -----------------------------------------------------
        # PARAMETER YANG SEBELUMNYA BERMASALAH
        # -----------------------------------------------------

        problematic_parameters = (
            estimated_record.get(
                "problematic_parameters",
                []
            )
        )

        # -----------------------------------------------------
        # JIKA MISSING
        #
        # Missing tidak memiliki problematic_parameters,
        # sehingga seluruh parameter utama dihitung ulang.
        # -----------------------------------------------------

        if not problematic_parameters:

            problematic_parameters = (
                ESTIMATION_PARAMETERS.copy()
            )

        updated_record = (
            estimated_record.copy()
        )

        recomputed_parameters = []

        # =====================================================
        # HITUNG ULANG BMA
        # =====================================================

        for param in problematic_parameters:

            if param not in ESTIMATION_PARAMETERS:
                continue

            values = []

            for ref_record in previous_actual_valid:

                value = ref_record.get(param)

                if (
                    is_actual_valid(ref_record)
                    and
                    is_valid_numeric(value)
                ):

                    values.append(
                        float(value)
                    )

            # Harus lengkap 4 actual-valid
            if len(values) < BMA_WINDOW:
                continue

            bma_value = round(
                sum(values) / len(values),
                3
            )

            # -------------------------------------------------
            # SIMPAN BMA TERBARU
            # -------------------------------------------------

            updated_record[
                f"bma_{param}"
            ] = bma_value

            # Hasil utama kembali ke BMA.
            updated_record[param] = bma_value

            recomputed_parameters.append(
                param
            )

        # =====================================================
        # JIKA TIDAK ADA PARAMETER YANG BERHASIL
        # =====================================================

        if not recomputed_parameters:
            continue

        # =====================================================
        # UPDATE METADATA
        # =====================================================

        updated_record[
            "estimation_method"
        ] = "BMA"

        updated_record[
            "bma_recomputed"
        ] = True

        updated_record[
            "bma_recomputed_timestamp"
        ] = normalize_timestamp(
            datetime.now(timezone.utc)
        )

        updated_record[
            "bma_recomputed_parameters"
        ] = recomputed_parameters

        # -----------------------------------------------------
        # CSW LAMA JANGAN LANGSUNG DIANGGAP MASIH BERLAKU
        # -----------------------------------------------------

        updated_record[
            "csw_applied"
        ] = False

        updated_record.pop(
            "csw_applied_parameters",
            None
        )

        updated_record.pop(
            "csw_applied_timestamp",
            None
        )

        updated_record.pop(
            "csw_before_count",
            None
        )

        updated_record.pop(
            "csw_after_count",
            None
        )

        # ----------------------------------------------------
        # Simpan hasil BMA recompute
        # ke processed_records + Firebase
        # ----------------------------------------------------
        for param in ESTIMATION_PARAMETERS:
            updated_record.pop(
                f"csw_{param}",
                None 
                )
        # ----------------------------------------------------
        # Simpan hasil BMA recompute
        # ke processed_records + Firebase
        # ----------------------------------------------------

        save_estimated_record(
            updated_record
        )


        recomputed_count += 1

    return recomputed_count

# ============================================================
# HANDLE LATE DATA
# ============================================================

def handle_late_data(actual_record):

    event_timestamp = actual_record["timestamp"]

    event_dt = parse_timestamp(
        event_timestamp
    )

    # --------------------------------------------------------
    # Cari actual-valid terbaru
    # Sumber: raw_records karena isinya actual saja
    # --------------------------------------------------------

    actual_records = [
        record
        for record
        in raw_records.values()
        if is_actual_valid(record)
    ]

    latest_actual_dt = None

    if actual_records:

        latest_actual_dt = max(
            parse_timestamp(
                record["timestamp"]
            )
            for record
            in actual_records
        )

    # --------------------------------------------------------
    # Cek apakah timestamp sudah ada
    # --------------------------------------------------------

    old_record = (
        processed_records.get(
            event_timestamp
        )
    )

    # --------------------------------------------------------
    # Deteksi out-of-order
    # --------------------------------------------------------

    is_out_of_order = (

        latest_actual_dt is not None

        and

        event_dt < latest_actual_dt

    )

    # --------------------------------------------------------
    # Cek apakah menggantikan estimated
    # --------------------------------------------------------

    replaces_estimated = (

        old_record is not None

        and

        old_record.get(
            "data_status"
        ) == "estimated"

    )

    # --------------------------------------------------------
    # Tentukan late data
    # --------------------------------------------------------

    is_late = (

        is_out_of_order

        or

        replaces_estimated

    )

    # --------------------------------------------------------
    # Bukan late
    # --------------------------------------------------------

    if not is_late:

        return {

            "late":
                False,

            "replaced_estimated":
                False,

            "evaluated_record":
                None,

            "bma_recomputed":
                0,

            "csw_updated":
                []

        }

    # --------------------------------------------------------
    # Evaluasi estimated yang digantikan actual
    # --------------------------------------------------------

    evaluated_record = None

    if replaces_estimated:

        estimated_record = (
            old_record.copy()
        )

        evaluated_record = (
            calculate_estimation_errors(
                estimated_record,
                actual_record
            )
        )

        processed_records.pop(
            event_timestamp,
            None
        )

    # --------------------------------------------------------
    # SIMPAN ACTUAL LATE
    #
    # Masuk ke:
    # raw_records
    # processed_records
    # Firebase raw_5sec
    # Firebase processed_5sec
    # --------------------------------------------------------

    save_actual_record(
        actual_record
    )

    # --------------------------------------------------------
    # RECOMPUTE BMA
    # --------------------------------------------------------

    bma_recomputed = (
        recompute_bma_affected_by_late_actual(
            actual_record
        )
    )

    # --------------------------------------------------------
    # RETURN
    # --------------------------------------------------------

    return {

        "late":
            True,

        "replaced_estimated":
            replaces_estimated,

        "evaluated_record":
            evaluated_record,

        "bma_recomputed":
            bma_recomputed,

        "csw_updated":
            []

    }
# ============================================================
# LATEST / DATA TERKINI
# ============================================================

def update_latest():

    global latest_data

    records = (
        get_sorted_processed_records()
    )

    if not records:

        latest_data = None

        return None

    # Data dengan event timestamp terbaru
    latest_data = records[-1]

    # --------------------------------------------------------
    # Firebase node: data_terkini
    # --------------------------------------------------------

    db.reference(
        "data_terkini"
    ).set(latest_data)

    return latest_data


# ============================================================
# WINDOW START
# ============================================================

def get_window_start(
    timestamp,
    window_seconds
):

    dt = parse_timestamp(
        timestamp
    )

    epoch = int(
        dt.timestamp()
    )

    start_epoch = (
        epoch
        //
        window_seconds
    ) * window_seconds

    return datetime.fromtimestamp(
        start_epoch,
        tz=timezone.utc
    )


# ============================================================
# GET RECORDS IN WINDOW
# ============================================================

def get_records_in_window(
    window_start,
    window_seconds
):

    window_end = (
        window_start
        +
        timedelta(
            seconds=
            window_seconds
        )
    )

    records = []

    for record in (
        processed_records.values()
    ):

        try:

            dt = parse_timestamp(
                record["timestamp"]
            )

        except Exception:

            continue

        if (
            dt >= window_start
            and
            dt < window_end
        ):

            records.append(record)

    records.sort(
        key=lambda x:
            parse_timestamp(
                x["timestamp"]
            )
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

    window_seconds = (
        WINDOWS[
            window_name
        ]
    )

    window_end = (
        window_start
        +
        timedelta(
            seconds=
            window_seconds
        )
    )

    actual_count = sum(
        1
        for r in records
        if is_actual_valid(r)
    )

    estimated_count = sum(
        1
        for r in records
        if r.get(
            "data_status"
        ) == "estimated"
    )

    result = {

        "window":
            window_name,

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
            actual_count,

        "estimated_count":
            estimated_count

    }

    # --------------------------------------------------------
    # PERSENTASE ACTUAL
    # --------------------------------------------------------

    if len(records) > 0:

        result[
            "actual_percentage"
        ] = round(
            (
                actual_count
                /
                len(records)
            )
            *
            100,
            2
        )

    else:

        result[
            "actual_percentage"
        ] = 0.0

    # --------------------------------------------------------
    # RATA-RATA PARAMETER
    # --------------------------------------------------------

    for parameter in (
        ESTIMATION_PARAMETERS
    ):

        values = []

        for record in records:

            value = record.get(
                parameter
            )

            if is_finite_number(value):

                values.append(
                    float(value)
                )

        if values:

            result[parameter] = round(
                sum(values)
                /
                len(values),
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
        aggregate[
            "window_start"
        ]
    )

    key = timestamp_key(
        window_start
    )

    db.reference(
        f"historical/{window_name}/{key}"
    ).set(aggregate)


# ============================================================
# RECOMPUTE WINDOW
# ============================================================

def recompute_window(
    window_name,
    timestamp
):

    window_seconds = (
        WINDOWS[
            window_name
        ]
    )

    window_start = (
        get_window_start(
            timestamp,
            window_seconds
        )
    )

    records = (
        get_records_in_window(
            window_start,
            window_seconds
        )
    )

    aggregate = (
        aggregate_window(
            window_name,
            window_start,
            records
        )
    )

    if aggregate:

        save_historical(
            window_name,
            aggregate
        )

    return aggregate


# ============================================================
# RECOMPUTE AFFECTED WINDOWS
# ============================================================

def recompute_affected_windows(
    timestamp
):

    results = {}

    for window_name in WINDOWS:

        aggregate = (
            recompute_window(
                window_name,
                timestamp
            )
        )

        results[
            window_name
        ] = aggregate

    return results


# ============================================================
# RECOMPUTE MULTIPLE AFFECTED WINDOWS
# ============================================================

def recompute_multiple_timestamps(
    timestamps
):

    results = {}

    unique_timestamps = list(
        dict.fromkeys(
            timestamps
        )
    )

    for timestamp in unique_timestamps:

        results[
            timestamp
        ] = recompute_affected_windows(
            timestamp
        )

    return results


# ============================================================
# RECOMPUTE ALL HISTORICAL
# ============================================================

def recompute_all_historical():

    records = (
        get_sorted_processed_records()
    )

    if not records:
        return

    for window_name in WINDOWS:

        window_seconds = (
            WINDOWS[
                window_name
            ]
        )

        affected_windows = set()

        for record in records:

            try:

                window_start = (
                    get_window_start(
                        record["timestamp"],
                        window_seconds
                    )
                )

            except Exception:

                continue

            affected_windows.add(
                normalize_timestamp(
                    window_start
                )
            )

        for window_start_string in (
            affected_windows
        ):

            window_start = (
                parse_timestamp(
                    window_start_string
                )
            )

            records_window = (
                get_records_in_window(
                    window_start,
                    window_seconds
                )
            )

            aggregate = (
                aggregate_window(
                    window_name,
                    window_start,
                    records_window
                )
            )

            if aggregate:

                save_historical(
                    window_name,
                    aggregate
                )


# ============================================================
# PROCESS DATA
# ============================================================

def process_incoming_data(data):

    # --------------------------------------------------------
    # 1. VALIDASI DASAR
    # --------------------------------------------------------

    valid, message = (
        validate_basic_data(data)
    )

    if not valid:

        return {

            "success": False,

            "message": message

        }

    # --------------------------------------------------------
    # 2. EVENT TIMESTAMP
    # --------------------------------------------------------

    try:

        event_timestamp = (
            parse_timestamp(
                data["timestamp"]
            )
        )

    except Exception as e:

        return {

            "success": False,

            "message": str(e)

        }

    event_timestamp_normalized = (
        normalize_timestamp(
            event_timestamp
        )
    )

    # --------------------------------------------------------
    # 3. INGESTION TIMESTAMP
    # --------------------------------------------------------

    ingestion_timestamp = (
        datetime.now(
            timezone.utc
        )
    )

    # --------------------------------------------------------
    # 4. LATENCY
    # --------------------------------------------------------

    latency_ms = (
        ingestion_timestamp
        -
        event_timestamp.astimezone(
            timezone.utc
        )
    ).total_seconds() * 1000

    if latency_ms < 0:

        latency_ms = 0.0

    # --------------------------------------------------------
    # 5. DETEKSI NULL
    # --------------------------------------------------------

    null_parameters = (
        detect_null_parameters(data)
    )

    # --------------------------------------------------------
    # 6. DETEKSI INVALID NUMERIC
    # --------------------------------------------------------

    invalid_numeric_parameters = (
        detect_invalid_numeric_parameters(
            data
        )
    )

    # --------------------------------------------------------
    # 7. DETEKSI ANOMALY
    # --------------------------------------------------------

    anomaly_parameters = (
        detect_anomalies(
            data,
            event_timestamp_normalized
        )
    )

    # --------------------------------------------------------
    # 8. GABUNGKAN PARAMETER BERMASALAH
    # --------------------------------------------------------

    problematic_parameters = list(
        dict.fromkeys(

            null_parameters
            +
            invalid_numeric_parameters
            +
            anomaly_parameters

        )
    )

    problematic_main_parameters = [

        parameter

        for parameter
        in problematic_parameters

        if parameter
        in ESTIMATION_PARAMETERS

    ]

    # --------------------------------------------------------
    # 9. DATA NORMAL
    # --------------------------------------------------------
    if not problematic_main_parameters:

        # ----------------------------------------------------
        # Buat actual record
        # ----------------------------------------------------

        record = create_actual_record(
            data,
            ingestion_timestamp,
            latency_ms
        )

        late_result = (
            handle_late_data(
                record
            )
        )

        if not late_result["late"]:

            save_actual_record(
                record
            )

        # ----------------------------------------------------
        # Missing
        # ----------------------------------------------------

        generated_missing = (
            fill_missing_data()
        )

        # ----------------------------------------------------
        # CSW
        # ----------------------------------------------------

        csw_updated = (
            update_all_possible_csw()
        )

        # ----------------------------------------------------
        # Update latest
        # ----------------------------------------------------

        update_latest()

        # ----------------------------------------------------
        # Historical
        # ----------------------------------------------------

        affected_timestamps = [
            event_timestamp_normalized
        ]

        affected_timestamps.extend(
            item["timestamp"]
            for item
            in generated_missing
        )

        affected_timestamps.extend(
            item["timestamp"]
            for item
            in csw_updated
        )

        historical = (
            recompute_multiple_timestamps(
                affected_timestamps
            )
        )

        return {

            "success": True,

            "record":
                processed_records.get(
                    event_timestamp_normalized,
                    record
                ),

            "late_data":
                late_result,

            "missing_generated":
                generated_missing,

            "csw_updated":
                csw_updated,

            "latest":
                latest_data,

            "historical":
                historical,

            "problematic_parameters":
                []

        }

    # --------------------------------------------------------
    # 10. DATA BERMASALAH
    # --------------------------------------------------------

    reason_parts = []

    if null_parameters:
        reason_parts.append(
            "null"
        )

    if invalid_numeric_parameters:
        reason_parts.append(
            "invalid_numeric"
        )

    if anomaly_parameters:
        reason_parts.append(
            "anomaly"
        )

    reason = "+".join(
        reason_parts
    )

    # --------------------------------------------------------
    # Simpan informasi input bermasalah
    # --------------------------------------------------------

    save_problematic_input(
        data,
        event_timestamp_normalized,
        problematic_main_parameters,
        reason
    )

    # --------------------------------------------------------
    # Buat record dasar
    # --------------------------------------------------------

    record = create_actual_record(
        data,
        ingestion_timestamp,
        latency_ms
    )

    # --------------------------------------------------------
    # BMA
    # --------------------------------------------------------

    estimated_record = (
        apply_bma_to_problematic_record(
            record,
            problematic_main_parameters,
            reason
        )
    )

    if estimated_record is None:

        return {

            "success": False,

            "message": (
                "Data bermasalah terdeteksi, "
                "tetapi BMA belum dapat dilakukan "
                "karena belum tersedia 4 actual-valid "
                "sebelumnya."
            ),

            "problematic_parameters":
                problematic_main_parameters

        }

    record = estimated_record

    # --------------------------------------------------------
    # Jika timestamp sudah memiliki actual,
    # jangan menimpa actual dengan estimated.
    # --------------------------------------------------------

    existing = (
        processed_records.get(
            event_timestamp_normalized
        )
    )

    if existing is not None:

        if is_actual_valid(existing):

            return {

                "success": False,

                "message": (
                    "Timestamp tersebut sudah memiliki "
                    "actual-valid data."
                ),

                "existing_record":
                    existing,

                "problematic_parameters":
                    problematic_main_parameters

            }

    # --------------------------------------------------------
    # Simpan estimated ke processed
    # --------------------------------------------------------

    save_estimated_record(
        record
    )

    # --------------------------------------------------------
    # Missing
    # --------------------------------------------------------

    generated_missing = (
        fill_missing_data()
    )

    # --------------------------------------------------------
    # CSW
    # --------------------------------------------------------

    csw_updated = (
        update_all_possible_csw()
    )

    # --------------------------------------------------------
    # Update latest
    # --------------------------------------------------------

    update_latest()

    # --------------------------------------------------------
    # Historical
    # --------------------------------------------------------

    affected_timestamps = [
        event_timestamp_normalized
    ]

    affected_timestamps.extend(
        item["timestamp"]
        for item
        in generated_missing
    )

    affected_timestamps.extend(
        item["timestamp"]
        for item
        in csw_updated
    )

    historical = (
        recompute_multiple_timestamps(
            affected_timestamps
        )
    )

    return {

        "success": True,

        "record":
            processed_records.get(
                event_timestamp_normalized,
                record
            ),

        "late_data": {

            "late":
                False,

            "replaced_estimation":
                False

        },

        "missing_generated":
            generated_missing,

        "csw_updated":
            csw_updated,

        "latest":
            latest_data,

        "historical":
            historical,

        "problematic_parameters":
            problematic_main_parameters

    }


# ============================================================
# ROUTE /DATA
# ============================================================

@app.route(
    "/data",
    methods=["POST"]
)
def receive_data():

    data = request.get_json(
        silent=True
    )

    print(
        "\n========================================"
    )

    print(
        "DATA DITERIMA DARI JOAN / ESP32"
    )

    print(
        "========================================"
    )

    print(data)

    print(
        "========================================"
    )

    if data is None:

        return jsonify({

            "status":
                "error",

            "message":
                "Request harus menggunakan JSON"

        }), 400

    with data_lock:

        result = process_incoming_data(
            data
        )

    if not result["success"]:

        print(
            "PROCESSING ERROR:",
            result["message"]
        )

        return jsonify({

            "status":
                "error",

            "message":
                result["message"],

            "problematic_parameters":
                result.get(
                    "problematic_parameters",
                    []
                )

        }), 400

    record = result["record"]

    print(
        "\nDATA BERHASIL DIPROSES"
    )

    print(
        "Timestamp:",
        record["timestamp"]
    )

    print(
        "Data status:",
        record["data_status"]
    )

    print(
        "Validation:",
        record["validation_status"]
    )

    print(
        "CH4:",
        record["ch4_ppm"],
        "ppm"
    )

    print(
        "Humidity:",
        record["humidity"],
        "%"
    )

    print(
        "Temperature:",
        record["temperature"],
        "C"
    )

    print(
        "Latency:",
        record["latency_ms"],
        "ms"
    )

    if record.get(
        "estimation_method"
    ):

        print(
            "Estimation:",
            record[
                "estimation_method"
            ]
        )

    return jsonify({

        "status":
            "success",

        "message":
            "Data berhasil diproses",

        "data":
            record,

        "late_data":
            result[
                "late_data"
            ],

        "missing_generated":
            result[
                "missing_generated"
            ],

        "csw_updated":
            result[
                "csw_updated"
            ],

        "data_terkini":
            result[
                "latest"
            ],

        "historical":
            result[
                "historical"
            ]

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

        "status":
            "success",

        "message":
            "Flask IoT Data Pipeline aktif",

        "routes": [

            "POST /data",

            "GET /data_terkini",

            "GET /latest",

            "GET /historical/1min",

            "GET /historical/10min",

            "GET /historical/hourly",

            "GET /test-firebase",

            "POST /test/simulate"

        ]

    })


# ============================================================
# DATA TERKINI
# ============================================================

@app.route(
    "/data_terkini",
    methods=["GET"]
)
def get_current_data():

    return jsonify({

        "status":
            "success",

        "data":
            latest_data

    })


# ============================================================
# ALIAS LATEST
# ============================================================

@app.route(
    "/latest",
    methods=["GET"]
)
def get_latest():

    return jsonify({

        "status":
            "success",

        "data":
            latest_data

    })


# ============================================================
# HISTORICAL ENDPOINT
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

        "status":
            "success",

        "window":
            window_name,

        "data":
            data

    })


# ============================================================
# HISTORICAL 1 MIN
# ============================================================

@app.route(
    "/historical/1min",
    methods=["GET"]
)
def historical_1min():

    return get_historical_endpoint(
        "1min"
    )


# ============================================================
# HISTORICAL 10 MIN
# ============================================================

@app.route(
    "/historical/10min",
    methods=["GET"]
)
def historical_10min():

    return get_historical_endpoint(
        "10min"
    )


# ============================================================
# HISTORICAL HOURLY
# ============================================================

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

    fake_data = request.get_json(
        silent=True
    )

    if fake_data is None:

        return jsonify({

            "status":
                "error",

            "message":
                "JSON tidak ditemukan"

        }), 400

    if "device_id" not in fake_data:

        fake_data["device_id"] = (
            "TEST_DEVICE"
        )

    with data_lock:

        result = process_incoming_data(
            fake_data
        )

    if not result["success"]:

        return jsonify({

            "status":
                "error",

            "message":
                result["message"],

            "problematic_parameters":
                result.get(
                    "problematic_parameters",
                    []
                )

        }), 400

    return jsonify({

        "status":
            "success",

        "simulation":
            True,

        "record":
            result["record"],

        "result": {

            "late_data":
                result[
                    "late_data"
                ],

            "generated_missing":
                result[
                    "missing_generated"
                ],

            "csw_updated":
                result[
                    "csw_updated"
                ]

        },

        "data_terkini":
            result[
                "latest"
            ],

        "historical":
            result[
                "historical"
            ]

    })


# ============================================================
# LOAD EXISTING DATA
# ============================================================

def load_existing_data():

    global raw_records
    global processed_records
    global latest_data

    # --------------------------------------------------------
    # LOAD RAW
    # --------------------------------------------------------

    try:

        raw_data = db.reference(
            "raw_5sec"
        ).get()

        if raw_data:

            loaded_raw = 0

            for record in raw_data.values():

                if not isinstance(
                    record,
                    dict
                ):

                    continue

                timestamp = record.get(
                    "timestamp"
                )

                if not timestamp:
                    continue

                # Hanya actual-valid masuk raw_records
                if is_actual_valid(record):

                    raw_records[
                        timestamp
                    ] = record

                    loaded_raw += 1

            print(
                f"{loaded_raw} ACTUAL RAW RECORD berhasil dimuat"
            )

        else:

            print(
                "RAW DATABASE KOSONG"
            )

    except Exception as e:

        print(
            "Gagal load RAW:",
            str(e)
        )

    # --------------------------------------------------------
    # LOAD PROCESSED
    # --------------------------------------------------------

    try:

        processed_data = (
            db.reference(
                "processed_5sec"
            ).get()
        )

        if processed_data:

            loaded_processed = 0

            for record in (
                processed_data.values()
            ):

                if not isinstance(
                    record,
                    dict
                ):

                    continue

                timestamp = record.get(
                    "timestamp"
                )

                if not timestamp:
                    continue

                processed_records[
                    timestamp
                ] = record

                loaded_processed += 1

            print(
                f"{loaded_processed} PROCESSED RECORD berhasil dimuat"
            )

        else:

            # ------------------------------------------------
            # Jika processed_5sec belum ada,
            # gunakan raw sebagai processed awal.
            # ------------------------------------------------

            for timestamp, record in (
                raw_records.items()
            ):

                processed_records[
                    timestamp
                ] = record

            print(
                "PROCESSED DATABASE KOSONG - "
                "RAW digunakan sebagai processed awal"
            )

    except Exception as e:

        print(
            "Gagal load PROCESSED:",
            str(e)
        )

    # --------------------------------------------------------
    # UPDATE DATA TERKINI
    # --------------------------------------------------------

    if processed_records:

        update_latest()

    else:

        latest_data = None


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    print(
        "\n========================================"
    )

    print(
        "MEMUAT DATA DARI FIREBASE..."
    )

    print(
        "========================================"
    )

    load_existing_data()

    print(
        "\n========================================"
    )

    print(
        "KONFIGURASI PIPELINE"
    )

    print(
        "========================================"
    )

    print(
        "Expected interval:",
        EXPECTED_INTERVAL_SECONDS,
        "detik"
    )

    print(
        "Missing tolerance:",
        MISSING_TOLERANCE_SECONDS,
        "detik"
    )

    print(
        "BMA window:",
        BMA_WINDOW
    )

    print(
        "CSW before:",
        CSW_BEFORE
    )

    print(
        "CSW after:",
        CSW_AFTER
    )

    print(
        "Parameter utama:",
        ESTIMATION_PARAMETERS
    )

    print(
        "\n========================================"
    )

    print(
        "STRUKTUR DATA"
    )

    print(
        "========================================"
    )

    print(
        "raw_5sec       = actual sensor data"
    )

    print(
        "processed_5sec = actual + estimated"
    )

    print(
        "data_terkini   = data terbaru"
    )

    print(
        "historical     = data agregasi"
    )

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
        "\nVPS:"
    )

    print(
        "http://10.33.102.153:5000"
    )

    print(
        "\nEndpoint Joan:"
    )

    print(
        "POST http://10.33.102.153:5000/data"
    )

    print(
        "\nData terkini:"
    )

    print(
        "GET http://10.33.102.153:5000/data_terkini"
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