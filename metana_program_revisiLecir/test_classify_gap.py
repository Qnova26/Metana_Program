"""
Regression test untuk fungsi classify_data_gap (self-contained).
Tidak membutuhkan Flask atau Firebase — fungsi di-copy langsung.
"""
from datetime import datetime, timedelta, timezone
import math

# ── Constants (sama dengan SERVER2.py) ──
NORMAL_INTERVAL_SECONDS  = 8 * 60
CYCLES_PER_BATCH         = 10

# ── Helpers ──
def safe_float(value):
    if value is None:
        return None
    try:
        v = float(value)
        if math.isfinite(v):
            return v
    except Exception:
        pass
    return None

# ── Fungsi classify_data_gap (copy dari SERVER2.py) ──
def classify_data_gap(previous_dt, current_dt, previous_record=None, current_record=None):
    gap_seconds = (current_dt - previous_dt).total_seconds()
    gap_minutes = gap_seconds / 60

    result = {
        "gap_minutes":         round(gap_minutes, 3),
        "status":              "needs_review",
        "generate_estimation": False,
        "missing_count":       0,
        "missing_timestamps":  [],
        "reason":              "Pola jeda belum dapat dipastikan",
        "restart_detected":    False
    }

    if gap_seconds <= 0:
        result["status"] = "out_of_order_or_duplicate"
        result["reason"] = "Timestamp tidak berurutan"
        return result

    previous_record = previous_record or {}
    current_record  = current_record  or {}

    previous_idx     = safe_float(previous_record.get("idx"))
    current_idx      = safe_float(current_record.get("idx"))
    previous_elapsed = safe_float(previous_record.get("elapsed_s"))
    current_elapsed  = safe_float(current_record.get("elapsed_s"))

    idx_available = (
        previous_idx is not None and current_idx is not None
        and previous_idx.is_integer() and current_idx.is_integer()
    )
    if idx_available:
        previous_idx = int(previous_idx)
        current_idx  = int(current_idx)

    elapsed_reset = (
        previous_elapsed is not None and current_elapsed is not None
        and current_elapsed < previous_elapsed
    )
    idx_reset = (idx_available and previous_idx > 0 and current_idx == 0)
    restart_detected = elapsed_reset or idx_reset
    result["restart_detected"] = restart_detected

    # ── 3. Long Outage > 45 menit ──
    if gap_seconds > 45 * 60:
        result["status"]              = "long_outage_or_shutdown"
        result["reason"]              = (
            f"Jeda {round(gap_minutes, 1)} menit melebihi 45 menit "
            "(perangkat mati/dimatikan). Tidak menghasilkan estimasi BMA."
        )
        result["generate_estimation"] = False
        return result

    # ── 4. Restart ──
    RESTART_MAX_NORMAL_SECONDS = 23 * 60
    if restart_detected:
        if gap_seconds > RESTART_MAX_NORMAL_SECONDS:
            raw_missing = max(1, int(gap_seconds / NORMAL_INTERVAL_SECONDS) - 2)
            missing_timestamps = []
            for step in range(1, raw_missing + 1):
                t_slot = previous_dt + timedelta(seconds=step * NORMAL_INTERVAL_SECONDS)
                if t_slot < current_dt:
                    missing_timestamps.append(t_slot)
            missing_count = len(missing_timestamps)
            result["status"]              = "restart_with_missing_data"
            result["generate_estimation"] = True
            result["missing_count"]       = missing_count
            result["missing_timestamps"]  = missing_timestamps
            result["restart_detected"]    = True
            result["reason"] = (
                f"Restart terdeteksi namun jeda {round(gap_minutes, 1)} menit "
                f"melebihi batas wajar restart ({RESTART_MAX_NORMAL_SECONDS // 60} menit). "
                f"Terdeteksi {missing_count} slot data hilang sebelum restart. "
                "BMA & CSW diaktifkan untuk slot-slot tersebut."
            )
        else:
            result["status"]              = "restart_normal"
            result["reason"] = (
                "Perangkat terdeteksi restart (elapsed_s atau idx reset). "
                f"Jeda {round(gap_minutes, 1)} menit masih dalam batas wajar restart. "
                "Siklus baru dimulai tanpa estimasi missing data."
            )
            result["generate_estimation"] = False
        return result

    # ── 5. Cleaning ~18 menit ──
    is_cleaning_time = (15.5 * 60 <= gap_seconds <= 20.5 * 60)
    is_consecutive_or_10th = (
        not idx_available
        or current_idx == previous_idx + 1
        or (previous_idx % CYCLES_PER_BATCH == CYCLES_PER_BATCH - 1)
    )
    if is_cleaning_time and is_consecutive_or_10th:
        result["status"]              = "planned_cleaning_interval"
        result["reason"] = (
            "Interval cleaning terjadwal ~18 menit "
            "(5m sensor off + 5m warmup + 8m siklus). Bukan data hilang."
        )
        result["generate_estimation"] = False
        return result

    # ── 6. Normal ~8 menit ──
    is_normal_time = (6.5 * 60 <= gap_seconds <= 9.8 * 60)
    if is_normal_time:
        result["status"] = "normal_interval"
        if idx_available and current_idx != previous_idx + 1:
            result["reason"] = (
                "Durasi jeda sesuai interval normal ~8 menit "
                "(terdapat lompatan idx perangkat)."
            )
        else:
            result["reason"] = "Durasi jeda sesuai interval pengiriman normal ~8 menit."
        result["generate_estimation"] = False
        return result

    # ── 7. Missing data ──
    if gap_seconds > 9.8 * 60:
        time_based_count = max(1, int(round(gap_seconds / NORMAL_INTERVAL_SECONDS)) - 1)

        elapsed_continuous = (
            previous_elapsed is not None and current_elapsed is not None
            and current_elapsed > previous_elapsed
        )
        use_idx_diff = (
            idx_available
            and current_idx > previous_idx + 1
            and elapsed_continuous
        )

        if use_idx_diff:
            idx_diff = current_idx - previous_idx - 1
            if idx_diff <= time_based_count * 3:
                missing_count = idx_diff
            else:
                missing_count = time_based_count  # idx_diff absurd
        else:
            missing_count = time_based_count

        step_seconds = gap_seconds / (missing_count + 1)
        missing_timestamps = []
        for step in range(1, missing_count + 1):
            t_slot = previous_dt + timedelta(seconds=step * NORMAL_INTERVAL_SECONDS)
            if t_slot >= current_dt:
                t_slot = previous_dt + timedelta(seconds=step * step_seconds)
            missing_timestamps.append(t_slot)

        result["status"]              = "missing_interval_detected"
        result["generate_estimation"] = True
        result["missing_count"]       = len(missing_timestamps)
        result["missing_timestamps"]  = missing_timestamps
        result["reason"] = (
            f"Terdeteksi {len(missing_timestamps)} slot data hilang "
            f"pada rentang jeda {round(gap_minutes, 1)} menit. BMA & CSW diaktifkan."
        )
        return result

    result["status"] = "needs_review"
    result["reason"] = f"Jeda {round(gap_minutes, 1)} menit tidak sesuai pola normal maupun cleaning."
    return result

# ══════════════════════════════════════════════════════════════
# TEST CASES
# ══════════════════════════════════════════════════════════════
PASS = 0
FAIL = 0

def run_test(name, prev_dt_s, curr_dt_s, prev_rec, curr_rec, expected_status, extra_checks=None):
    global PASS, FAIL
    TZ7 = timezone(timedelta(hours=7))
    result = classify_data_gap(
        datetime.fromisoformat(prev_dt_s).replace(tzinfo=TZ7),
        datetime.fromisoformat(curr_dt_s).replace(tzinfo=TZ7),
        previous_record=prev_rec,
        current_record=curr_rec
    )
    status = result["status"]
    ok = status == expected_status
    mark = "[PASS]" if ok else "[FAIL]"
    print(f"{mark} [{name}]")
    print(f"   Status   : {status}  (expected: {expected_status})")
    print(f"   Gap      : {result['gap_minutes']:.1f} menit")
    if result.get("missing_count"):
        print(f"   Missing  : {result['missing_count']} slot")
    print(f"   Reason   : {result['reason'][:95]}")
    extra_ok = True
    if extra_checks:
        for k, v in extra_checks.items():
            actual = result.get(k)
            chk_ok = actual == v
            if not chk_ok:
                extra_ok = False
            chk = "[OK]" if chk_ok else f"[FAIL] (got {actual!r}, want {v!r})"
            print(f"   Check [{k}]: {chk}")
    print()
    if ok and extra_ok:
        PASS += 1
    else:
        FAIL += 1

print("=" * 65)
print("REGRESSION TEST: classify_data_gap")
print("=" * 65)
print()

# 1. Normal ~8 menit
run_test("Normal 8 menit (idx berurutan)",
  "2026-10-13T18:25:58", "2026-10-13T18:34:04",
  {"idx": 0, "elapsed_s": 500}, {"idx": 1, "elapsed_s": 980},
  "normal_interval"
)

# 2. BUG 1: idx loncat besar (2->219) tapi waktu ~8 menit → normal
run_test("BUG1: idx loncat 2->219, waktu 8 menit → normal_interval",
  "2026-10-31T07:58:12", "2026-10-31T08:06:18",
  {"idx": 2, "elapsed_s": 1200}, {"idx": 219, "elapsed_s": 1680},
  "normal_interval"
)

# 3. idx loncat besar tapi elapsed_s reset (beda sesi), waktu 8 menit
#    elapsed turun → restart_detected, jeda < 23m → restart_normal
run_test("BUG1b: idx loncat beda sesi (elapsed reset), waktu 8 menit → restart_normal",
  "2026-10-31T07:58:12", "2026-10-31T08:06:18",
  {"idx": 219, "elapsed_s": 90000}, {"idx": 5, "elapsed_s": 1680},
  "restart_normal",
  extra_checks={"generate_estimation": False}
)

# 4. idx 312→315 waktu 24 menit → 2 slot missing (elapsed naik)
run_test("Missing: idx 312->315 waktu 24 menit → 2 slot missing",
  "2026-10-21T16:59:37", "2026-10-21T17:23:46",
  {"idx": 312, "elapsed_s": 150000}, {"idx": 315, "elapsed_s": 151440},
  "missing_interval_detected",
  extra_checks={"missing_count": 2, "generate_estimation": True}
)

# 5. Cleaning ~18 menit (idx berurutan)
run_test("Cleaning ~18 menit (idx berurutan)",
  "2026-10-13T18:25:58", "2026-10-13T18:43:58",
  {"idx": 79, "elapsed_s": 40000}, {"idx": 80, "elapsed_s": 41100},
  "planned_cleaning_interval"
)

# 6. BUG 2: Restart idx 307→0, jeda 31 menit → restart_with_missing_data
run_test("BUG2: Restart idx 307->0, jeda 31 menit → restart_with_missing_data",
  "2026-10-18T10:30:00", "2026-10-18T11:01:00",
  {"idx": 307, "elapsed_s": 168087}, {"idx": 0, "elapsed_s": 783},
  "restart_with_missing_data",
  extra_checks={"generate_estimation": True, "restart_detected": True}
)

# 7. Restart + jeda 18 menit → restart_normal (wajar)
run_test("BUG2b: Restart idx 307->0, jeda 18 menit → restart_normal",
  "2026-10-18T10:30:00", "2026-10-18T10:48:00",
  {"idx": 307, "elapsed_s": 168087}, {"idx": 0, "elapsed_s": 783},
  "restart_normal",
  extra_checks={"generate_estimation": False}
)

# 8. Long outage > 45 menit
run_test("Long outage > 45 menit",
  "2026-10-18T10:30:00", "2026-10-18T17:45:00",
  {"idx": 299, "elapsed_s": 150000}, {"idx": 0, "elapsed_s": 500},
  "long_outage_or_shutdown",
  extra_checks={"generate_estimation": False}
)

# 9. Timestamp terbalik
run_test("Timestamp terbalik (out_of_order)",
  "2026-10-18T11:00:00", "2026-10-18T10:55:00",
  {}, {}, "out_of_order_or_duplicate"
)

# 10. Missing idx 379→381 waktu 26 menit → 1 slot missing
run_test("Missing: idx 379->381 waktu 26 menit → 1 slot missing",
  "2026-10-24T09:00:00", "2026-10-24T09:26:00",
  {"idx": 379, "elapsed_s": 200000}, {"idx": 381, "elapsed_s": 201560},
  "missing_interval_detected",
  extra_checks={"missing_count": 1, "generate_estimation": True}
)

# 11. Absurd idx jump (2->500) waktu 26 menit → fallback ke time-based (2 slot)
run_test("Absurd idx jump (2->500) waktu 26 menit → time-based fallback",
  "2026-10-24T09:00:00", "2026-10-24T09:26:00",
  {"idx": 2, "elapsed_s": 200000}, {"idx": 500, "elapsed_s": 201560},
  "missing_interval_detected",
  extra_checks={"missing_count": 2, "generate_estimation": True}  # time-based: 2
)

print("=" * 65)
print(f"HASIL: {PASS} LULUS  |  {FAIL} GAGAL")
print("=" * 65)
import sys; sys.exit(0 if FAIL == 0 else 1)
