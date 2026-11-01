from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, db
from datetime import datetime, timedelta, timezone
import math
import statistics
import os
import time
import threading

# ============================================================
# CONFIGURATION
# ============================================================

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SERVICE_ACCOUNT_FILE = os.path.join(
    BASE_DIR,
    "data-monitoring-gas-metana-firebase-adminsdk-fbsvc-1b4eba3e8a.json"
)

DATABASE_URL = (
    "https://data-monitoring-gas-metana-default-rtdb.asia-southeast1.firebasedatabase.app"
)

# ============================================================
# INTERVAL DAN SIKLUS ESP32
# ============================================================

# Interval pengiriman normal
NORMAL_INTERVAL_SECONDS = 8 * 60
NORMAL_TOLERANCE_SECONDS = 2 * 60

# Interval total setelah cleaning
# 18 menit sudah mencakup tahapan cleaning dan warm-up ulang.
CLEANING_INTERVAL_SECONDS = 18 * 60
CLEANING_TOLERANCE_SECONDS = 1 * 60

# Cleaning dilakukan setiap 10 kali pengiriman
CYCLES_PER_BATCH = 10

# Batas interval normal
NORMAL_MIN_SECONDS = (
    NORMAL_INTERVAL_SECONDS - NORMAL_TOLERANCE_SECONDS
)

NORMAL_MAX_SECONDS = (
    NORMAL_INTERVAL_SECONDS + NORMAL_TOLERANCE_SECONDS
)

# Batas interval cleaning
CLEANING_MIN_SECONDS = (
    CLEANING_INTERVAL_SECONDS - CLEANING_TOLERANCE_SECONDS
)

CLEANING_MAX_SECONDS = (
    CLEANING_INTERVAL_SECONDS + CLEANING_TOLERANCE_SECONDS
)

# BMA dan CSW
BMA_WINDOW = 2
CSW_BEFORE = 1
CSW_AFTER = 1

# Pemeriksaan jadwal pengiriman setiap 10 detik
GAP_CHECK_INTERVAL_SECONDS = 10

# Batas keterlambatan sebelum satu slot dinyatakan hilang.
# Nilai ini mengikuti toleransi interval normal.
# Untuk cleaning, digunakan toleransi cleaning tersendiri.
MAX_CSW_GAP_SECONDS = 60 * 60

# Mencegah scheduler memproses data replay yang timestamp
# peristiwanya jauh lebih lama daripada waktu penerimaan Flask.
MAX_LIVE_EVENT_INGESTION_LAG_MS = 15 * 60 * 1000

# Historical
HISTORICAL_WINDOW_MINUTES = 60

# IQR anomaly
MIN_ANOMALY_SAMPLES = 20
IQR_MULTIPLIER = 1.5


# Parameter yang dapat diestimasi
ESTIMATION_PARAMETERS = [
    "ch4_ppm",
    "humidity_pct",
    "temperature_c"
]

# Field minimum yang wajib ada
REQUIRED_METADATA_FIELDS = [
    "device_id",
    "timestamp",
    "battery_v",
    "battery_pct"
]

# ============================================================
# FIREBASE INITIALIZATION
# ============================================================

if not firebase_admin._apps:
    cred = credentials.Certificate(SERVICE_ACCOUNT_FILE)

    firebase_admin.initialize_app(
        cred,
        {
            "databaseURL": DATABASE_URL
        }
    )

print("Firebase berhasil terhubung.")


# ============================================================
# HELPER
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def parse_timestamp(timestamp):
    """
    Parse timestamp ISO-8601.
    Contoh:
    2026-10-07T10:00:00+07:00
    """

    if not isinstance(timestamp, str):
        raise ValueError("timestamp harus berupa string")

    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        raise ValueError(
            "timestamp harus menggunakan format ISO-8601"
        )


def timestamp_key(dt):
    """
    Membuat key Firebase yang aman.
    """

    return dt.strftime("%Y-%m-%dT%H-%M-%S")


def is_valid_number(value):
    """
    Mengecek angka valid dan finite.
    """

    if value is None:
        return False

    if isinstance(value, bool):
        return False

    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def safe_float(value):
    if value is None:
        return None

    try:
        value = float(value)

        if math.isfinite(value):
            return value

    except Exception:
        pass

    return None

# ============================================================
# VALIDATION
# ============================================================

def validate_payload(data):

    errors = []
    warnings = []

    # --------------------------------------------------------
    # Required metadata
    # --------------------------------------------------------

    for field in REQUIRED_METADATA_FIELDS:

        if field not in data:
            errors.append(
                f"Field wajib tidak ditemukan: {field}"
            )

    # --------------------------------------------------------
    # device_id
    # --------------------------------------------------------

    if "device_id" in data:

        if not isinstance(data["device_id"], str):
            errors.append(
                "device_id harus berupa string"
            )

        elif not data["device_id"].strip():
            errors.append(
                "device_id tidak boleh kosong"
            )

    # --------------------------------------------------------
    # timestamp
    # --------------------------------------------------------

    event_dt = None

    if "timestamp" in data:

        try:
            event_dt = parse_timestamp(data["timestamp"])

        except ValueError as e:
            errors.append(str(e))

    # --------------------------------------------------------
    # battery
    # --------------------------------------------------------

    for field in ["battery_v", "battery_pct"]:

        if field in data:

            if not is_valid_number(data[field]):
                errors.append(
                    f"{field} harus berupa angka valid"
                )

    # --------------------------------------------------------
    # Parameter estimasi
    # --------------------------------------------------------

    for field in ESTIMATION_PARAMETERS:

        if field not in data:

            warnings.append(
                f"{field} tidak tersedia"
            )

        elif data[field] is None:

            warnings.append(
                f"{field} bernilai null"
            )

        elif not is_valid_number(data[field]):

            warnings.append(
                f"{field} tidak valid dan dapat diproses sebagai missing"
            )

    # --------------------------------------------------------
    # Field hardware tambahan
    # --------------------------------------------------------

    optional_numeric_fields = [
        "idx",
        "elapsed_s",
        "temperature_c",
        "humidity_pct",
        "absolute_humidity_gm3",
        "battery_v",
        "battery_pct",
        "adc_raw",
        "module_vout_v",
        "rs_filtered_kohm",
        "rs_median_kohm",
        "r0_kohm",
        "ch4_ppm",
        "ch4_ppm_instant",
        "ch4_ppm_median",
        "ch4_ppm_mean",
        "ch4_ppm_min",
        "ch4_ppm_max",
        "ch4_ppm_mad",
        "ch4_ppm_mad_percent",
        "block_size",
        "block_count",
        "h_percent",
        "env_comp_factor",
        "rs_raw_median_kohm",
        "rs_corrected_kohm",
        "ch4_ppm_raw"
    ]

    for field in optional_numeric_fields:

        if field in data and data[field] is not None:

            if not is_valid_number(data[field]):

                warnings.append(
                    f"{field} bukan angka valid"
                )

    return errors, warnings, event_dt


# ============================================================
# ACTUAL RECORD
# ============================================================



def create_actual_record(
    data,
    event_dt,
    ingestion_dt,
    warnings=None
):
    record = dict(data)

    # Waktu Flask menerima payload
    record["ingestion_timestamp"] = ingestion_dt.isoformat()

    # Selisih waktu event pada perangkat dengan penerimaan Flask.
    # Ini bukan network latency murni, terutama untuk data replay.
    event_to_ingestion_ms = (
        ingestion_dt - event_dt
    ).total_seconds() * 1000

    record["event_to_ingestion_ms"] = round(
        event_to_ingestion_ms, 3
    )

    # Pertahankan field lama agar kompatibel dengan kode lama.
    record["latency_ms"] = round(
        event_to_ingestion_ms, 3
    )

    record["data_status"] = "actual"
    record["validation_status"] = "valid"

    parameter_status = {}

    for parameter in ESTIMATION_PARAMETERS:
        value = safe_float(data.get(parameter))

        if value is None:
            parameter_status[parameter] = "missing"
            record[parameter] = None
        else:
            parameter_status[parameter] = "valid"
            record[parameter] = value

    record["PARAMETER"] = {
        "CH4": record.get("ch4_ppm"),
        "Temp": record.get("temperature_c"),
        "RH": record.get("humidity_pct")
    }

    record["parameter_status"] = parameter_status

    if any(
        status == "missing"
        for status in parameter_status.values()
    ):
        record["data_quality_status"] = "incomplete"
    else:
        record["data_quality_status"] = "complete"

    if warnings:
        record["validation_warnings"] = warnings

    return record

# ============================================================
# FIREBASE ACCESS
# ============================================================

def get_ref(path):
    return db.reference(path)


def get_node(path):

    value = get_ref(path).get()

    if value is None:
        return {}

    return value

# ============================================================
# UPDATE DATA TERKINI
# ============================================================

def update_data_terkini_if_newer(record):

    if not isinstance(record, dict):
        return False

    target_timestamp = record.get("timestamp")

    if not target_timestamp:
        return False

    try:
        target_dt = parse_timestamp(target_timestamp)
    except Exception:
        return False

    latest_ref = get_ref("data_terkini")
    existing_latest = latest_ref.get()

    if isinstance(existing_latest, dict):

        existing_timestamp = existing_latest.get("timestamp")

        if existing_timestamp:

            try:
                existing_dt = parse_timestamp(
                    existing_timestamp
                )

                # Jangan menimpa data dengan timestamp lebih baru.
                if target_dt < existing_dt:
                    return False

                # Jika timestamp sama, data actual lebih
                # diprioritaskan daripada estimated.
                if target_dt == existing_dt:

                    existing_status = existing_latest.get(
                        "data_status"
                    )

                    new_status = record.get("data_status")

                    if (
                        existing_status == "actual"
                        and new_status == "estimated"
                    ):
                        return False

            except Exception:
                pass

    updated_record = dict(record)

    updated_record["latest_update"] = (
        now_utc().isoformat()
    )

    latest_ref.set(updated_record)

    return True

# ============================================================
# ACTUAL VALID RECORDS
# ============================================================

def get_actual_valid_records():

    processed = get_node("processed_12min")

    records = []

    for key, record in processed.items():

        if not isinstance(record, dict):
            continue

        if (
            record.get("data_status") == "actual"
            and
            record.get("validation_status") == "valid"
        ):

            try:

                dt = parse_timestamp(
                    record["timestamp"]
                )

                records.append(
                    (dt, key, record)
                )

            except Exception:
                continue

    records.sort(
        key=lambda x: x[0]
    )

    return records


# ============================================================
# BMA
# ============================================================

def calculate_bma(records):

    result = {}
      
    for parameter in ESTIMATION_PARAMETERS:
        values = [
            safe_float(record.get(parameter))
            for record in records
        ]

        values = [
            value for value in values
            if value is not None
        ]

        if len(values) >= BMA_WINDOW:
            used_values = values[-BMA_WINDOW:]
            result[parameter] = {
                "value": sum(used_values) / len(used_values),
                "sample_count": len(used_values)
            }
        else:
            result[parameter] = {
                "value": None,
                "sample_count": len(values)
            }

    return result


def build_bma_estimated_record(
    target_dt,
    previous_records
):

    bma_values = calculate_bma(
    [item[2] for item in previous_records]
    )

    record = {
        "device_id": (
            previous_records[-1][2].get("device_id", "ESP32_GAS_NODE")
            if previous_records and isinstance(previous_records[-1][2], dict)
            else "ESP32_GAS_NODE"
        ),
        "timestamp": target_dt.isoformat(),
        "data_status": "estimated",
        "estimation_method": "BMA",
        "estimation_status": "temporary",
        "reconstruction_status": "waiting_future_data",
        "validation_status": "estimated",
        "bma_window": BMA_WINDOW,
        "source_timestamps": [
            item[2].get("timestamp")
            for item in previous_records
        ],
        "source_count_by_parameter": {
            parameter: bma_values[parameter]["sample_count"]
            for parameter in ESTIMATION_PARAMETERS
        },
        "bma_values": {
            parameter: (
                round(bma_values[parameter]["value"], 4)
                if bma_values[parameter]["value"] is not None
                else None
            )
            for parameter in ESTIMATION_PARAMETERS
        },
        "bma_ch4_ppm": (
            round(bma_values["ch4_ppm"]["value"], 4)
            if bma_values["ch4_ppm"]["value"] is not None
            else None
        ),
        "bma_temperature_c": (
            round(bma_values["temperature_c"]["value"], 4)
            if bma_values["temperature_c"]["value"] is not None
            else None
        ),
        "bma_humidity_pct": (
            round(bma_values["humidity_pct"]["value"], 4)
            if bma_values["humidity_pct"]["value"] is not None
            else None
        ),
        "csw_values": None,
        "csw_ch4_ppm": None,
        "csw_temperature_c": None,
        "csw_humidity_pct": None,
    }

    for parameter in ESTIMATION_PARAMETERS:
        value = bma_values[parameter]["value"]

        record[parameter] = (
            round(value, 6)
            if value is not None
            else None
        )

    record["PARAMETER"] = {
        "CH4": record.get("ch4_ppm"),
        "Temp": record.get("temperature_c"),
        "RH": record.get("humidity_pct")
    }
        
    return record

# ============================================================
# CSW
# ============================================================

def calculate_csw(
    previous_record,
    next_record,
    target_dt
):
    result = {}

    previous_dt = parse_timestamp(
        previous_record["timestamp"]
    )
    next_dt = parse_timestamp(
        next_record["timestamp"]
    )

    total_seconds = (
        next_dt - previous_dt
    ).total_seconds()

    if total_seconds <= 0:
        return {
            parameter: None
            for parameter in ESTIMATION_PARAMETERS
        }

    weight = (
        (target_dt - previous_dt).total_seconds()
        / total_seconds
    )

    for parameter in ESTIMATION_PARAMETERS:
        prev_value = safe_float(
            previous_record.get(parameter)
        )
        next_value = safe_float(
            next_record.get(parameter)
        )

        if prev_value is None or next_value is None:
            result[parameter] = None
        else:
            result[parameter] = (
                prev_value
                + weight * (next_value - prev_value)
            )

    return result



def apply_csw(
    estimated_record,
    previous_record,
    next_record
):
    # Pertahankan metadata BMA dan field estimasi yang sudah ada.
    record = dict(estimated_record)

    record["device_id"] = (
        previous_record.get("device_id")
        or next_record.get("device_id")
    )

    target_dt = parse_timestamp(
        estimated_record["timestamp"]
    )

    csw_values = calculate_csw(
        previous_record,
        next_record,
        target_dt
    )

    for parameter in ESTIMATION_PARAMETERS:
        value = csw_values.get(parameter)
        record[parameter] = (
            round(value, 6)
            if value is not None
            else None
        )

    # Catat hasil CSW secara eksplisit
    csw_val_dict = {
        parameter: (
            round(csw_values.get(parameter), 4)
            if csw_values.get(parameter) is not None
            else None
        )
        for parameter in ESTIMATION_PARAMETERS
    }
    record["csw_values"] = csw_val_dict
    record["csw_ch4_ppm"] = csw_val_dict.get("ch4_ppm")
    record["csw_temperature_c"] = csw_val_dict.get("temperature_c")
    record["csw_humidity_pct"] = csw_val_dict.get("humidity_pct")

    # Pastikan bma_values tetap ada (fallback dari nilai sebelum ditimpa jika ada)
    if "bma_values" not in record or not record["bma_values"]:
        record["bma_values"] = {
            parameter: (
                round(estimated_record.get(parameter), 4)
                if estimated_record.get(parameter) is not None
                else None
            )
            for parameter in ESTIMATION_PARAMETERS
        }
        record["bma_ch4_ppm"] = record["bma_values"].get("ch4_ppm")
        record["bma_temperature_c"] = record["bma_values"].get("temperature_c")
        record["bma_humidity_pct"] = record["bma_values"].get("humidity_pct")

    # Rekam perbandingan BMA realtime vs CSW hasil rekonstruksi
    bma_vals = record.get("bma_values") or {}
    record["bma_vs_csw_comparison"] = {
        parameter: {
            "bma_realtime": bma_vals.get(parameter),
            "csw_reconstructed": csw_val_dict.get(parameter),
            "difference": (
                round(csw_val_dict.get(parameter) - bma_vals.get(parameter), 4)
                if csw_val_dict.get(parameter) is not None and bma_vals.get(parameter) is not None
                else None
            )
        }
        for parameter in ESTIMATION_PARAMETERS
    }

    record["data_status"] = "estimated"
    record["estimation_method"] = "CSW"
    record["estimation_status"] = "reconstructed"
    record["reconstruction_status"] = "completed"
    record["validation_status"] = "estimated"

    record["csw_before"] = CSW_BEFORE
    record["csw_after"] = CSW_AFTER

    record["csw_previous_timestamp"] = (
        previous_record.get("timestamp")
    )
    record["csw_next_timestamp"] = (
        next_record.get("timestamp")
    )

    record["parameter_status"] = {
        parameter: (
            "estimated"
            if record.get(parameter) is not None
            else "missing"
        )
        for parameter in ESTIMATION_PARAMETERS
    }

    record["data_quality_status"] = (
        "complete"
        if all(
            record.get(parameter) is not None
            for parameter in ESTIMATION_PARAMETERS
        )
        else "incomplete"
    )

    # Perbarui PARAMETER setelah nilai CSW dihitung
    record["PARAMETER"] = {
        "CH4": record.get("ch4_ppm"),
        "Temp": record.get("temperature_c"),
        "RH": record.get("humidity_pct")
    }
    return record

# ============================================================
# IQR ANOMALY DETECTION
# ============================================================

def calculate_iqr_bounds(values):

    if len(values) < MIN_ANOMALY_SAMPLES:
        return None, None

    sorted_values = sorted(values)

    q1 = statistics.quantiles(
        sorted_values,
        n=4,
        method="inclusive"
    )[0]

    q3 = statistics.quantiles(
        sorted_values,
        n=4,
        method="inclusive"
    )[2]

    iqr = q3 - q1

    lower = q1 - (
        IQR_MULTIPLIER * iqr
    )

    upper = q3 + (
        IQR_MULTIPLIER * iqr
    )

    return lower, upper


def detect_anomaly():

    processed_records = get_actual_valid_records()

    result = {}

    for parameter in ESTIMATION_PARAMETERS:

        values = []

        for _, _, record in processed_records:

            value = safe_float(
                record.get(parameter)
            )

            if value is not None:
                values.append(value)

        lower, upper = calculate_iqr_bounds(
            values
        )

        result[parameter] = {
            "sample_count": len(values),
            "lower_bound": lower,
            "upper_bound": upper
        }

    return result


# ============================================================
# MISSING DATA PROCESSING
# ============================================================


def classify_data_gap(
    previous_dt,
    current_dt,
    previous_record=None,
    current_record=None
):
    """
    Mengklasifikasikan jeda pengiriman berdasarkan:
    1. Jarak waktu antar-data (Time-Gap).
    2. Urutan idx ESP32 (jika tersedia).
    3. Perubahan elapsed_s sebagai indikator restart.

    Status:
    - normal_interval
    - planned_cleaning_interval
    - missing_interval_detected
    - restart_normal
    - long_outage_or_shutdown
    - out_of_order_or_duplicate
    - needs_review
    """

    gap_seconds = (
        current_dt - previous_dt
    ).total_seconds()

    gap_minutes = gap_seconds / 60

    result = {
        "gap_minutes": round(gap_minutes, 3),
        "status": "needs_review",
        "generate_estimation": False,
        "missing_count": 0,
        "missing_timestamps": [],
        "reason": "Pola jeda belum dapat dipastikan",
        "restart_detected": False
    }

    # --------------------------------------------------------
    # 1. Periksa urutan timestamp
    # --------------------------------------------------------
    if gap_seconds <= 0:
        result["status"] = "out_of_order_or_duplicate"
        result["reason"] = "Timestamp tidak berurutan"
        return result

    # --------------------------------------------------------
    # 2. Ambil idx dan elapsed_s dari ESP32
    # --------------------------------------------------------
    previous_record = previous_record or {}
    current_record = current_record or {}

    previous_idx = safe_float(previous_record.get("idx"))
    current_idx = safe_float(current_record.get("idx"))
    previous_elapsed = safe_float(previous_record.get("elapsed_s"))
    current_elapsed = safe_float(current_record.get("elapsed_s"))

    idx_available = (
        previous_idx is not None
        and current_idx is not None
        and previous_idx.is_integer()
        and current_idx.is_integer()
    )

    if idx_available:
        previous_idx = int(previous_idx)
        current_idx = int(current_idx)

    # Indikator restart ESP32
    elapsed_reset = (
        previous_elapsed is not None
        and current_elapsed is not None
        and current_elapsed < previous_elapsed
    )
    idx_reset = (
        idx_available
        and previous_idx > 0
        and current_idx == 0
    )
    restart_detected = elapsed_reset or idx_reset
    result["restart_detected"] = restart_detected

    # --------------------------------------------------------
    # 3. Long Outage / Device Shutdown (> 45 menit)
    # --------------------------------------------------------
    if gap_seconds > 45 * 60:
        result["status"] = "long_outage_or_shutdown"
        result["reason"] = (
            f"Jeda {round(gap_minutes, 1)} menit melebihi 45 menit "
            "(perangkat mati/dimatikan). Tidak menghasilkan estimasi BMA."
        )
        result["generate_estimation"] = False
        return result

    # --------------------------------------------------------
    # 4. Restart Terdeteksi (Jeda moderat <= 45 menit)
    # --------------------------------------------------------
    if restart_detected:
        result["status"] = "restart_normal"
        result["reason"] = (
            "Perangkat terdeteksi restart (elapsed_s atau idx reset). "
            "Siklus baru dimulai tanpa estimasi missing data."
        )
        result["generate_estimation"] = False
        return result

    # --------------------------------------------------------
    # 5. Deteksi Siklus Cleaning Terjadwal (~18 menit)
    # --------------------------------------------------------
    # Durasi cleaning: 5m off + 5m warmup + 3m purge + 5m acq = 18 menit (1080s)
    # Toleransi: 15.5m - 20.5m (930s - 1230s)
    is_cleaning_time = (15.5 * 60 <= gap_seconds <= 20.5 * 60)
    is_consecutive_or_10th = (
        not idx_available
        or current_idx == previous_idx + 1
        or (previous_idx % CYCLES_PER_BATCH == CYCLES_PER_BATCH - 1)
    )

    if is_cleaning_time and is_consecutive_or_10th:
        result["status"] = "planned_cleaning_interval"
        result["reason"] = (
            "Interval cleaning terjadwal ~18 menit "
            "(5m sensor off + 5m warmup + 8m siklus). Bukan data hilang."
        )
        result["generate_estimation"] = False
        return result

    # --------------------------------------------------------
    # 6. Deteksi Interval Normal (~8 menit)
    # --------------------------------------------------------
    # Durasi normal: 3m purge + 5m acq = 8 menit (480s)
    # Toleransi: 6.5m - 9.8m (390s - 590s)
    is_normal_time = (6.5 * 60 <= gap_seconds <= 9.8 * 60)

    if is_normal_time:
        result["status"] = "normal_interval"
        if idx_available and current_idx != previous_idx + 1:
            result["reason"] = (
                "Durasi jeda sesuai interval normal ~8 menit "
                "(terdapat lompatan idx perangkat)."
            )
        else:
            result["reason"] = (
                "Durasi jeda sesuai interval pengiriman normal ~8 menit."
            )
        result["generate_estimation"] = False
        return result

    # --------------------------------------------------------
    # 7. Deteksi Data Hilang / Packet Loss (Missing Data Sejati)
    # --------------------------------------------------------
    # Terjadi jika waktu > 9.8 menit, tidak ada restart, dan <= 45 menit
    if gap_seconds > 9.8 * 60:
        missing_count = 0
        missing_timestamps = []

        # Sub-kasus A: idx melompat (misal 312 -> 315)
        if idx_available and current_idx > previous_idx + 1:
            idx_diff = current_idx - previous_idx - 1
            missing_count = idx_diff
        else:
            # Sub-kasus B: Berdasarkan kelipatan waktu ~8 menit
            missing_count = max(1, int(round(gap_seconds / NORMAL_INTERVAL_SECONDS)) - 1)

        # Buat timestamp untuk slot-slot yang hilang
        step_seconds = gap_seconds / (missing_count + 1)
        for step in range(1, missing_count + 1):
            t_slot = previous_dt + timedelta(seconds=step * NORMAL_INTERVAL_SECONDS)
            if t_slot >= current_dt:
                t_slot = previous_dt + timedelta(seconds=step * step_seconds)
            missing_timestamps.append(t_slot)

        result["status"] = "missing_interval_detected"
        result["generate_estimation"] = True
        result["missing_count"] = len(missing_timestamps)
        result["missing_timestamps"] = missing_timestamps
        result["reason"] = (
            f"Terdeteksi {len(missing_timestamps)} slot data hilang "
            f"pada rentang jeda {round(gap_minutes, 1)} menit. BMA & CSW diaktifkan."
        )
        return result

    # --------------------------------------------------------
    # 8. Jeda Lain (Needs Review)
    # --------------------------------------------------------
    result["status"] = "needs_review"
    result["reason"] = (
        f"Jeda {round(gap_minutes, 1)} menit tidak sesuai pola normal maupun cleaning."
    )
    return result

# ============================================================
# CSW GAP ELIGIBILITY
# ============================================================

def check_csw_gap_eligibility(
    previous_dt,
    next_dt,
    previous_record,
    next_record
):
    """
    Memutuskan apakah CSW boleh menginterpolasi rentang
    antara dua data aktual.

    CSW hanya diizinkan jika gap terklasifikasi sebagai:
    - normal_interval
    - missing_interval_detected

    Gap cleaning, gap mencurigakan, dan gap yang tidak
    dapat diklasifikasikan dengan aman akan ditahan.
    """

    gap_info = classify_data_gap(
        previous_dt,
        next_dt,
        previous_record=previous_record,
        current_record=next_record
    )

    # --------------------------------------------------------
    # BATAS MAKSIMUM RENTANG INTERPOLASI CSW
    # --------------------------------------------------------

    gap_seconds = (
        next_dt - previous_dt
    ).total_seconds()

    if gap_seconds > MAX_CSW_GAP_SECONDS:

        return {
            "allowed": False,
            "status": "gap_exceeds_csw_limit",
            "reason": (
                "Rentang waktu antara data aktual sebelum "
                "dan sesudah estimasi melebihi batas "
                "maksimum interpolasi CSW"
            ),
            "gap_minutes": round(
                gap_seconds / 60,
                3
            )
        }

    allowed_statuses = {
        "normal_interval",
        "missing_interval_detected",
        "restart_with_missing_interval"
    }

    allowed = (
        gap_info["status"] in allowed_statuses
    )

    if not allowed:
        return {
            "allowed": False,
            "status": gap_info["status"],
            "reason": gap_info["reason"],
            "gap_minutes": gap_info["gap_minutes"]
        }

    return {
        "allowed": True,
        "status": gap_info["status"],
        "reason": gap_info["reason"],
        "gap_minutes": gap_info["gap_minutes"]
    }

def process_missing_timestamp(missing_dt):
    key = timestamp_key(missing_dt)

    existing = get_ref(
        f"processed_12min/{key}"
    ).get()

    # Data aktual selalu lebih diutamakan
    if (
        isinstance(existing, dict)
        and existing.get("data_status") == "actual"
    ):
        return {
            "status": "skipped",
            "reason": "actual_record_exists",
            "timestamp": missing_dt.isoformat()
        }

    # Jangan menimpa estimasi yang sudah ada.
    # Estimasi sementara tetap boleh diproses oleh CSW
    # melalui reprocess_csw(), bukan dibuat ulang di sini.
    if (
        isinstance(existing, dict)
        and existing.get("data_status") == "estimated"
    ):
        return {
            "status": "skipped",
            "reason": "estimated_record_exists",
            "timestamp": missing_dt.isoformat()
        }

    # Lanjutkan proses BMA yang sudah ada di sini

    t = time.time()
    actual_records = get_actual_valid_records()
    print("GET RECORD TIME:", round(time.time()-t,3))

    previous_records = [
        item
        for item in actual_records
        if item[0] < missing_dt
    ]

    previous_records = previous_records[
        -BMA_WINDOW:
    ]

    
    if len(previous_records) >= BMA_WINDOW:

        estimated_record = build_bma_estimated_record(
            missing_dt,
            previous_records
        )

        estimable_parameters = [
            parameter
            for parameter in ESTIMATION_PARAMETERS
            if estimated_record.get(parameter) is not None
        ]

        key = timestamp_key(missing_dt)

        # Jangan menyimpan estimasi jika semua parameter
        # tidak memiliki nilai hasil BMA.
        if not estimable_parameters:
            problematic_record = {
                "timestamp": missing_dt.isoformat(),
                "data_status": "problematic",
                "problem_type": "insufficient_parameter_data",
                "reason": (
                    "Tidak ada parameter yang memiliki "
                    "dua nilai valid untuk BMA"
                )
            }

            get_ref(
                f"problematic_12min/{key}"
            ).set(problematic_record)

            return {
                "status": "problematic",
                "timestamp": missing_dt.isoformat()
            }

        estimated_record["parameter_status"] = {
            parameter: (
                "estimated"
                if estimated_record.get(parameter) is not None
                else "missing"
            )
            for parameter in ESTIMATION_PARAMETERS
        }

        estimated_record["data_quality_status"] = (
            "complete"
            if len(estimable_parameters) == len(
                ESTIMATION_PARAMETERS
            )
            else "incomplete"
        )

        get_ref(
            f"processed_12min/{key}"
        ).set(estimated_record)

        return {
            "status": "estimated",
            "method": "BMA",
            "timestamp": missing_dt.isoformat(),
            "estimated_parameters": estimable_parameters
        }

    else:

        key = timestamp_key(
            missing_dt
        )

        problematic_record = {
            "timestamp": missing_dt.isoformat(),
            "data_status": "problematic",
            "problem_type": "missing_data",
            "reason": (
                "Tidak cukup data aktual-valid "
                "untuk melakukan BMA"
            )
        }

        get_ref(
            f"problematic_12min/{key}"
        ).set(
            problematic_record
        )

        return {
            "status": "problematic",
            "timestamp": missing_dt.isoformat()
        }

# ============================================================
# SCHEDULED GAP DETECTION
# ============================================================

def get_expected_next_interval(last_record):

    idx = safe_float(last_record.get("idx"))

    # Jika idx tersedia, periksa apakah pengiriman terakhir
    # merupakan pengiriman ke-10 dalam satu batch.
    if idx is not None and idx.is_integer():

        idx = int(idx)

        # Setelah pengiriman ke-10, interval berikutnya
        # mengikuti jadwal cleaning sekitar 18 menit.
        if idx % CYCLES_PER_BATCH == CYCLES_PER_BATCH - 1:

            return (
                CLEANING_INTERVAL_SECONDS,
                CLEANING_TOLERANCE_SECONDS,
                "planned_cleaning_interval"
            )

    # Selain itu, gunakan interval normal 8 menit.
    return (
        NORMAL_INTERVAL_SECONDS,
        NORMAL_TOLERANCE_SECONDS,
        "normal_interval"
    )


def run_gap_scheduler_once():

    actual_records = get_actual_valid_records()

    if not actual_records:
        return

    # Data aktual terbaru berdasarkan timestamp perangkat.
    latest_dt, latest_key, latest_record = actual_records[-1]

    # Scheduler hanya digunakan untuk data yang berasal dari
    # penerimaan langsung, bukan replay historis yang terlambat.
    event_ingestion_lag = safe_float(
        latest_record.get("event_to_ingestion_ms")
    )

    if event_ingestion_lag is None:
        return

    if abs(event_ingestion_lag) > MAX_LIVE_EVENT_INGESTION_LAG_MS:
        return

    # Tentukan jadwal pengiriman berikutnya.
    expected_interval, tolerance, interval_type = (
        get_expected_next_interval(latest_record)
    )

    expected_dt = latest_dt + timedelta(
        seconds=expected_interval
    )

    deadline_dt = expected_dt + timedelta(
        seconds=tolerance
    )

    current_dt = now_utc()

    # Belum melewati batas keterlambatan.
    if current_dt <= deadline_dt:
        return

    # Satu slot pengiriman sudah dinyatakan terlambat.
    print("\n======================================")
    print("SCHEDULED GAP DETECTION")
    print("======================================")

    print(
        "Data aktual terakhir:",
        latest_dt.isoformat()
    )

    print(
        "Jadwal pengiriman berikutnya:",
        expected_dt.isoformat()
    )

    print(
        "Batas toleransi:",
        deadline_dt.isoformat()
    )

    print(
        "Jenis interval:",
        interval_type
    )

    print(
        "Slot dinyatakan hilang:",
        expected_dt.isoformat()
    )

    # BMA dijalankan tanpa menunggu data aktual berikutnya.
    bma_result = process_missing_timestamp(expected_dt)

    print("SCHEDULED BMA RESULT:", bma_result)

    # Jika estimasi berhasil disimpan, tampilkan di data_terkini.
    if bma_result.get("status") == "estimated":

        estimated_key = timestamp_key(expected_dt)

        estimated_record = get_ref(
            f"processed_12min/{estimated_key}"
        ).get()

        if isinstance(estimated_record, dict):

            update_data_terkini_if_newer(
                estimated_record
            )

    # Perbarui historical setelah proses estimasi.
    create_hourly_historical()


def gap_scheduler_loop():

    print("BMA GAP SCHEDULER AKTIF")

    while True:

        try:
            run_gap_scheduler_once()

        except Exception as e:
            print(
                "GAP SCHEDULER ERROR:",
                str(e)
            )

        time.sleep(
            GAP_CHECK_INTERVAL_SECONDS
        )

# ============================================================
# CSW REPROCESSING
# ============================================================

def reprocess_csw():
    """
    Merekonstruksi estimasi BMA menjadi CSW jika:
    1. Tersedia data aktual-valid sebelum dan sesudah target.
    2. Gap memenuhi aturan klasifikasi.
    3. Gap tidak melebihi batas keras CSW.
    """

    processed = get_node("processed_12min")
    actual_records = get_actual_valid_records()

    actual_map = {
        item[0]: item[2]
        for item in actual_records
    }

    timestamps = sorted(actual_map.keys())

    updated_count = 0
    held_count = 0

    for firebase_key, record in processed.items():

        if not isinstance(record, dict):
            continue

        if record.get("data_status") != "estimated":
            continue

        # Hanya estimasi BMA sementara yang diproses ulang.
        if record.get("estimation_method") != "BMA":
            continue

        try:
            target_dt = parse_timestamp(record["timestamp"])
        except (ValueError, TypeError, KeyError):
            continue

        previous_candidates = [
            dt for dt in timestamps
            if dt < target_dt
        ]

        next_candidates = [
            dt for dt in timestamps
            if dt > target_dt
        ]

        if (
            len(previous_candidates) < CSW_BEFORE
            or len(next_candidates) < CSW_AFTER
        ):
            continue

        previous_dt = previous_candidates[-1]
        next_dt = next_candidates[0]

        # FIX: ambil kedua record dari actual_map.
        previous_record = actual_map[previous_dt]
        next_record = actual_map[next_dt]

        # Periksa kelayakan gap sebelum interpolasi.
        gap_check = check_csw_gap_eligibility(
            previous_dt,
            next_dt,
            previous_record,
            next_record
        )

        if not gap_check["allowed"]:
            held_count += 1

            print(
                "CSW DITAHAN:",
                gap_check["status"],
                "| gap:",
                gap_check["gap_minutes"],
                "menit",
                "| alasan:",
                gap_check["reason"]
            )
            continue

        gap_minutes = gap_check["gap_minutes"]

        # Rekonstruksi dilakukan satu kali.
        updated = apply_csw(
            record,
            previous_record,
            next_record
        )

        get_ref(
            f"processed_12min/{firebase_key}"
        ).set(updated)

        updated_count += 1

        print(
            "CSW BERHASIL:",
            target_dt.isoformat(),
            "| gap:",
            round(gap_minutes, 3),
            "menit"
        )

    print(
        "CSW SUMMARY:",
        f"updated={updated_count}, held={held_count}"
    )

    return {
        "updated_count": updated_count,
        "held_count": held_count
    }
        
# ============================================================
# ACTUAL VS ESTIMATE
# ============================================================

def calculate_estimation_error(
    estimated_record,
    actual_record
):

    comparison = {}

    for parameter in ESTIMATION_PARAMETERS:

        estimated = safe_float(
            estimated_record.get(parameter)
        )

        actual = safe_float(
            actual_record.get(parameter)
        )

        if (
            estimated is None
            or actual is None
        ):
            comparison[parameter] = None
            continue

        error = actual - estimated
        absolute_error = abs(error)

        if actual != 0:

            ape = (
                absolute_error
                / abs(actual)
            ) * 100

        else:

            ape = None

        comparison[parameter] = {
            "estimated": estimated,
            "actual": actual,
            "error": round(error, 6),
            "absolute_error": round(
                absolute_error,
                6
            ),
            "ape_percent": (
                round(ape, 6)
                if ape is not None
                else None
            )
        }

    return comparison


# ============================================================
# HISTORICAL 1 HOUR
# ============================================================

def create_hourly_historical():

    processed = get_node(
        "processed_12min"
    )

    records = []

    for key, record in processed.items():

        if not isinstance(record, dict):
            continue

        try:
            dt = parse_timestamp(
                record["timestamp"]
            )

        except Exception:
            continue

        records.append(
            (dt, key, record)
        )

    records.sort(
        key=lambda x: x[0]
    )

    if not records:
        return None

    latest_dt = records[-1][0]

    window_start = (
        latest_dt
        - timedelta(
            minutes=HISTORICAL_WINDOW_MINUTES
        )
    )

    selected = [
        item
        for item in records
        if window_start <= item[0] <= latest_dt
    ]

    if not selected:
        return None

    result = {
        "window_start": window_start.isoformat(),
        "window_end": latest_dt.isoformat(),
        "window_minutes": HISTORICAL_WINDOW_MINUTES,
        "record_count": len(selected),
        "actual_count": 0,
        "estimated_count": 0,
        "problematic_count": 0
    }
    for _, _, record in selected:

        if record.get("data_status") == "actual":
            result["actual_count"] += 1

        elif record.get("data_status") == "estimated":
            result["estimated_count"] += 1

        elif record.get("data_status") == "problematic":
            result["problematic_count"] += 1
    
    for parameter in ESTIMATION_PARAMETERS:

        values = []

        for _, _, record in selected:

            value = safe_float(
                record.get(parameter)
            )

            if value is not None:
                values.append(value)

        if values:

            result[f"{parameter}_mean"] = round(
                sum(values) / len(values),
                6
            )

            result[f"{parameter}_min"] = round(
                min(values),
                6
            )

            result[f"{parameter}_max"] = round(
                max(values),
                6
            )

        else:

            result[f"{parameter}_mean"] = None
            result[f"{parameter}_min"] = None
            result[f"{parameter}_max"] = None

    key = latest_dt.strftime(
        "%Y-%m-%dT%H"
    )

    get_ref(
        f"historical_1hour/{key}"
    ).set(result)

    return result


# ============================================================
# MAIN DATA ENDPOINT
# ============================================================


@app.route("/data", methods=["POST"])
def receive_data():
    processing_start = time.perf_counter()
    ingestion_dt = now_utc()

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        processing_ms = (
            time.perf_counter() - processing_start
        ) * 1000

        print(
            "TOTAL PROCESS TIME:",
            round(processing_ms / 1000, 3),
            "seconds"
        )

        return jsonify({
            "status": "error",
            "message": "JSON payload tidak valid"
        }), 400

    print("\n======================================")
    print("DATA MASUK DARI HARDWARE")
    print("======================================")
    print(f"Timestamp : {data.get('timestamp')}")
    print(data)

    # 1. Validasi payload
    errors, warnings, event_dt = validate_payload(data)

    if errors:
        print("VALIDATION ERROR:", errors)

        problematic_key = (
            f"{ingestion_dt.strftime('%Y-%m-%dT%H-%M-%S')}"
            "-problematic"
        )

        problematic_record = dict(data)
        problematic_record["ingestion_timestamp"] = (
            ingestion_dt.isoformat()
        )
        problematic_record["data_status"] = "problematic"
        problematic_record["validation_status"] = "invalid"
        problematic_record["validation_errors"] = errors

        get_ref(
            f"problematic_12min/{problematic_key}"
        ).set(problematic_record)

        return jsonify({
            "status": "problematic",
            "message": "Data gagal validasi",
            "errors": errors
        }), 400

    # 2. Buat record aktual
    actual_record = create_actual_record(
        data,
        event_dt,
        ingestion_dt,
        warnings
    )

    key = timestamp_key(event_dt)

    # 3. Periksa apakah timestamp ini sebelumnya berisi estimasi
    previous_processed = get_ref(
        f"processed_12min/{key}"
    ).get()

    estimation_comparison = None

    if (
        isinstance(previous_processed, dict)
        and previous_processed.get("data_status") == "estimated"
    ):
        estimation_comparison = calculate_estimation_error(
            previous_processed,
            actual_record
        )
        actual_record["estimation_comparison"] = (
            estimation_comparison
        )

    # 4. Simpan data aktual
    get_ref(
        f"processed_12min/{key}"
    ).set(actual_record)

    # 5. Deteksi gap berdasarkan data aktual-valid
    t = time.perf_counter()
    actual_records = get_actual_valid_records()

    print(
        "GET RECORD TIME:",
        round(time.perf_counter() - t, 3)
    )

    gap_info = None
    bma_results = []

    previous_candidates = [
        item
        for item in actual_records
        if item[0] < event_dt
    ]

    if previous_candidates:
        previous_dt = previous_candidates[-1][0]

        
        gap_info = classify_data_gap(
            previous_dt,
            event_dt,
            previous_record=previous_candidates[-1][2],
            current_record=actual_record
        )

        print("\n======================================")
        print("DATA GAP INFO")
        print("======================================")
        print(
            "Timestamp sebelumnya:",
            previous_dt.isoformat()
        )
        print(
            "Timestamp sekarang:",
            event_dt.isoformat()
        )
        print(
            "Jeda aktual:",
            gap_info["gap_minutes"],
            "menit"
        )
        print("Status gap:", gap_info["status"])
        print(
            "Jumlah slot hilang:",
            gap_info["missing_count"]
        )

        if gap_info["generate_estimation"]:
            for missing_dt in gap_info["missing_timestamps"]:
                print(
                    "\n[BMA] Memproses slot hilang:",
                    missing_dt.isoformat()
                )

                bma_result = process_missing_timestamp(
                    missing_dt
                )
                bma_results.append(bma_result)

                print("[BMA] Hasil:", bma_result)

    elif not previous_candidates:
        print(
            "[DATA GAP] Belum ada data aktual sebelumnya "
            "untuk dibandingkan."
        )

    else:
        print(
            "[DATA GAP] Deteksi dilewati: timestamp yang "
            "masuk bukan data aktual paling akhir."
        )

    # 6. Jalankan CSW setelah BMA dibuat.
    # CSW baru dapat berjalan jika data aktual sebelum dan
    # sesudah slot estimasi tersedia.
    t = time.perf_counter()

    reprocess_csw()

    print(
        "CSW TIME:",
        round(time.perf_counter() - t, 3),
        "seconds"
    )

    # 7. Perbarui ringkasan historical
    t = time.perf_counter()
    historical = create_hourly_historical()

    print(
        "HISTORICAL TIME:",
        round(time.perf_counter() - t, 3),
        "seconds"
    )

    # 8. Ukur waktu pemrosesan middleware.
    # Ini berbeda dari event_to_ingestion_ms.
    processing_latency_ms = (
        time.perf_counter() - processing_start
    ) * 1000

    actual_record["processing_latency_actual_ms"] = round(
        processing_latency_ms,
        3
    )

    get_ref(
        f"processed_12min/{key}"
    ).update({
        "processing_latency_actual_ms": (
            actual_record["processing_latency_actual_ms"]
        )
    })

       # 9. Perbarui data_terkini menggunakan data aktual.
    # Fungsi akan mencegah data lama menimpa data terbaru.

    latest_updated = update_data_terkini_if_newer(
        actual_record
    )

    if latest_updated:
        print(
            "DATA TERKINI DIPERBARUI:",
            actual_record.get("timestamp"),
            "| status:",
            actual_record.get("data_status")
        )

    else:
        print(
            "DATA TERKINI TIDAK DIUBAH:",
            "timestamp lebih lama atau prioritas status lebih rendah"
        )

    # 10. Respons
    print("DATA BERHASIL DIPROSES")
    print("Timestamp:", data.get("timestamp"))
    print("Device ID:", data.get("device_id"))
    print("CH4:", data.get("ch4_ppm"))
    print("Temp:", data.get("temperature_c"))
    print("RH:", data.get("humidity_pct"))
    print(
        "Processing Latency:",
        f"{processing_latency_ms:.3f} ms"
    )
    print(
        "TOTAL PROCESS TIME:",
        round(
            time.perf_counter() - processing_start,
            3
        ),
        "seconds"
    )
    print("======================================\n")

    return jsonify({
        "status": "success",
        "message": "Data berhasil diterima dan diproses",
        "device_id": data.get("device_id"),
        "timestamp": data.get("timestamp"),
        "latency_ms": actual_record["latency_ms"],
        "event_to_ingestion_ms": (
            actual_record["event_to_ingestion_ms"]
        ),
        "processing_latency_actual_ms": (
            actual_record["processing_latency_actual_ms"]
        ),
        "data_status": "actual",
        "validation_status": "valid",
        "gap_info": (
            {
                "status": gap_info["status"],
                "missing_count": gap_info["missing_count"],
                "gap_minutes": gap_info["gap_minutes"]
            }
            if gap_info is not None
            else None
        ),
        "bma_results": bma_results,
        "estimation_comparison": estimation_comparison,
        "historical_updated": historical is not None
    }), 200
            
# ============================================================
# LATEST
# ============================================================

@app.route("/data_terkini", methods=["GET"])
@app.route("/latest", methods=["GET"])
def latest_data():

    data = get_ref(
        "data_terkini"
    ).get()

    if data is None:

        return jsonify({
            "status": "empty",
            "message": "Belum ada data"
        }), 404

    return jsonify(data), 200


# ============================================================
# HISTORICAL
# ============================================================

@app.route(
    "/historical/hourly",
    methods=["GET"]
)
def historical_hourly():

    data = get_node(
        "historical_1hour"
    )

    return jsonify(data), 200


# ============================================================
# IQR
# ============================================================

@app.route(
    "/anomaly",
    methods=["GET"]
)
def anomaly():

    result = detect_anomaly()

    return jsonify(result), 200


# ============================================================
# FIREBASE TEST
# ============================================================

@app.route(
    "/test-firebase",
    methods=["GET"]
)
def test_firebase():

    try:

        test_ref = get_ref(
            "_middleware_test"
        )

        test_ref.set({
            "status": "connected",
            "timestamp": now_utc().isoformat()
        })

        return jsonify({
            "status": "success",
            "message": "Firebase berhasil terhubung"
        }), 200

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# SIMULATOR
# ============================================================

@app.route(
    "/test/simulate",
    methods=["GET"]
)
def test_simulate():

    event_dt = now_utc()

    simulated_data = {
        "device_id": "ESP32_GAS_NODE",
        "idx": 5,
        "elapsed_s": 3600,
        "timestamp": event_dt.isoformat(),

        "temperature_c": 28.10,
        "humidity_pct": 57.70,
        "absolute_humidity_gm3": 16.42,

        "battery_v": 7.85,
        "battery_pct": 77,

        "adc_raw": 1192.0,
        "module_vout_v": 1.1735,

        "rs_filtered_kohm": 49.920,
        "rs_median_kohm": 58.559,
        "r0_kohm": None,

        "ch4_ppm": 20.38,
        "ch4_ppm_instant": 20.33,
        "ch4_ppm_median": 20.38,
        "ch4_ppm_mean": 20.39,
        "ch4_ppm_min": 20.33,
        "ch4_ppm_max": 20.45,

        "ch4_ppm_mad": 0.045,
        "ch4_ppm_mad_percent": 0.22,

        "block_size": 5,
        "block_count": 20,

        "ch4_ppm_m1": 20.45,
        "ch4_ppm_m2": 20.42,
        "ch4_ppm_m3": 20.38,
        "ch4_ppm_m4": 20.35,
        "ch4_ppm_m5": 20.33,

        "t_c_m1": 28.50,
        "t_c_m2": 28.30,
        "t_c_m3": 28.10,
        "t_c_m4": 28.00,
        "t_c_m5": 27.90,

        "rh_m1": 58.50,
        "rh_m2": 58.00,
        "rh_m3": 57.70,
        "rh_m4": 57.50,
        "rh_m5": 57.30,

        "qc_status": "QC_STABLE",
        "pump_control": "DIGITAL_ON_OFF",
        "flow_status": "FLOW_NOT_MEASURED",

        "h_percent": 1.9732,
        "env_comp_factor": 0.7160,
        "env_comp_status": "OK",

        "rs_raw_median_kohm": 35.742,
        "rs_corrected_kohm": 49.920,

        "ch4_ppm_raw": 17.85,

        "system_status": "SEND"
    }

    with app.test_request_context(
        "/data",
        method="POST",
        json=simulated_data
    ):

        return receive_data()


# ============================================================
# ROOT
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return jsonify({
        "service": "IoT Methane Data Pipeline Middleware",
        "status": "running",
        "input_endpoint": "/data",
        "latest_endpoint": "/data_terkini",
        "historical_endpoint": "/historical/hourly",
        "anomaly_endpoint": "/anomaly",
        "firebase_test": "/test-firebase",
        "simulation_endpoint": "/test/simulate",

        "normal_interval_seconds": NORMAL_INTERVAL_SECONDS,
        "normal_tolerance_seconds": NORMAL_TOLERANCE_SECONDS,

        "cleaning_interval_seconds": CLEANING_INTERVAL_SECONDS,
        "cleaning_tolerance_seconds": CLEANING_TOLERANCE_SECONDS,

        "cycles_per_batch": CYCLES_PER_BATCH,

        "bma_window": BMA_WINDOW,
        "csw_before": CSW_BEFORE,
        "csw_after": CSW_AFTER

    }), 200


# ============================================================
# RUN SERVER
# ============================================================

if __name__ == "__main__":

    print("======================================")
    print("IoT METHANE DATA PIPELINE MIDDLEWARE")
    print("======================================")

    print(
        f"Interval normal       : "
        f"{NORMAL_INTERVAL_SECONDS / 60:.1f} menit"
    )

    print(
        f"Toleransi normal      : "
        f"+/- {NORMAL_TOLERANCE_SECONDS / 60:.1f} menit"
    )

    print(
        f"Interval cleaning    : "
        f"{CLEANING_INTERVAL_SECONDS / 60:.1f} menit"
    )

    print(
        f"Toleransi cleaning   : "
        f"+/- {CLEANING_TOLERANCE_SECONDS / 60:.1f} menit"
    )

    print(
        f"Siklus per batch     : "
        f"{CYCLES_PER_BATCH} pengiriman"
    )

    print(
        f"BMA                  : "
        f"{BMA_WINDOW} data sebelumnya"
    )

    print(
        f"CSW                  : "
        f"{CSW_BEFORE} sebelum + {CSW_AFTER} sesudah"
    )

    # START SCHEDULER
    scheduler_thread = threading.Thread(
        target=gap_scheduler_loop,
        daemon=True
    )

    scheduler_thread.start()

    print("Input                : POST /data")
    print("Latest               : GET /data_terkini")
    print("BMA Scheduler        : ACTIVE")
    print("======================================")

    app.run(
        host="0.0.0.0",
        port=5500,
        debug=False
    )