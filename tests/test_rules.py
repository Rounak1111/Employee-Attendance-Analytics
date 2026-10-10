from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.main import (
    EmployeeIn,
    PunchInIn,
    RegularizeIn,
    attendance_date,
    from_epoch_ms,
    half_day,
    late_minutes,
    overtime_minutes,
    serialize_attendance,
    to_epoch_ms,
    truncate_to_seconds,
    work_hours,
)


IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


def ist_time(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, minute, second, tzinfo=IST).astimezone(UTC)


def test_epoch_conversion_validates_range_and_truncates():
    instant = datetime(2026, 7, 6, 4, 10, 0, 900000, tzinfo=UTC)

    assert truncate_to_seconds(instant).microsecond == 0
    assert to_epoch_ms(instant) == int(datetime(2026, 7, 6, 4, 10, tzinfo=UTC).timestamp() * 1000)
    assert from_epoch_ms(to_epoch_ms(instant)) == datetime(2026, 7, 6, 4, 10, tzinfo=UTC)

    for invalid in (99_999_999_999, 4_102_444_800_001, 1.0, "1783312500000", True):
        with pytest.raises(ValueError):
            from_epoch_ms(invalid)

    for invalid_datetime in (datetime(1960, 1, 1, tzinfo=UTC), datetime(2200, 1, 1, tzinfo=UTC)):
        with pytest.raises(ValueError):
            to_epoch_ms(invalid_datetime)


def test_attendance_date_handles_overnight_shift():
    assert attendance_date(ist_time(6, 21, 55), "22:00", "06:00") == "2026-07-06"
    assert attendance_date(ist_time(7, 3, 0), "22:00", "06:00") == "2026-07-06"


def test_late_minutes_obeys_grace_boundary_and_floors():
    assert late_minutes(ist_time(6, 9, 40, 0), "09:30", "2026-07-06") == 0
    assert late_minutes(ist_time(6, 9, 40, 1), "09:30", "2026-07-06") == 10
    assert late_minutes(ist_time(6, 10, 15, 59), "09:30", "2026-07-06") == 45


def test_overnight_lateness_uses_attendance_day_shift_start():
    # 03:00 IST on July 7 belongs to July 6's 22:00 shift, so it is 300 minutes late.
    punch_in = ist_time(7, 3, 0)
    attendance_day = attendance_date(punch_in, "22:00", "06:00")
    assert late_minutes(punch_in, "22:00", attendance_day) == 300


def test_overtime_requires_thirty_minutes_and_handles_overnight():
    assert overtime_minutes(ist_time(6, 18, 59), "09:30", "18:30", "2026-07-06") == 0
    assert overtime_minutes(ist_time(6, 19, 0), "09:30", "18:30", "2026-07-06") == 30
    assert overtime_minutes(ist_time(7, 6, 40), "22:00", "06:00", "2026-07-06") == 40


def test_work_hours_rounds_half_up_and_half_day_uses_rounded_hours():
    punch_in = datetime(2026, 7, 6, 0, 0, tzinfo=UTC)
    punch_out = punch_in + timedelta(hours=4, minutes=40, seconds=30)

    assert work_hours(punch_in, punch_out) == 4.68
    assert half_day(4.49) is True
    assert half_day(4.50) is False

    just_under_half_day = work_hours(punch_in, punch_in + timedelta(seconds=16_186))
    assert just_under_half_day == 4.50
    assert half_day(just_under_half_day) is False


def test_employee_model_validates_contract_fields_and_real_date():
    valid = {
        "emp_code": "EMP0001",
        "name": "Asha Rao",
        "email": "asha@example.com",
        "department": "Engineering",
        "joined_on": "2026-07-06",
    }
    assert EmployeeIn(**valid).shift_start == "09:30"

    invalid_payloads = [
        {**valid, "emp_code": "EMP1"},
        {**valid, "name": ""},
        {**valid, "email": "not-an-email"},
        {**valid, "shift_start": "24:00"},
        {**valid, "shift_start": "09:30", "shift_end": "09:30"},
        {**valid, "joined_on": "2026-02-30"},
    ]
    for payload in invalid_payloads:
        with pytest.raises(ValidationError):
            EmployeeIn(**payload)


def test_punch_model_rejects_invalid_timestamp_and_ignores_derived_fields():
    valid = PunchInIn(emp_code="EMP0001", punched_at=1_783_312_500_000, late_minutes=999)
    assert not hasattr(valid, "late_minutes")
    assert valid.status == "PRESENT"

    for invalid_timestamp in (1_783_312_500, 1_783_312_500.0, "1783312500000", 99_999_999_999):
        with pytest.raises(ValidationError):
            PunchInIn(emp_code="EMP0001", punched_at=invalid_timestamp)

    with pytest.raises(ValidationError):
        PunchInIn(emp_code="EMP0001", status="ABSENT")


def test_attendance_serializer_converts_history_times_and_legacy_defaults():
    instant = datetime(2026, 7, 6, 4, 10, tzinfo=UTC)
    document = {
        "emp_code": "EMP0001",
        "date": "2026-07-06",
        "status": "PRESENT",
        "punch_in": instant,
        "punch_out": None,
        "work_hours": None,
        "history": [
            {
                "at": instant,
                "by": "hr.admin",
                "reason": "Correction",
                "changes": {"punch_in": {"from": instant, "to": None}},
            }
        ],
    }

    result = serialize_attendance(document)

    assert result["punch_in"] == to_epoch_ms(instant)
    assert result["history"][0]["at"] == to_epoch_ms(instant)
    assert result["history"][0]["changes"]["punch_in"] == {
        "from": to_epoch_ms(instant),
        "to": None,
    }
    assert result["half_day"] is False
    assert result["late_minutes"] == 0
    assert result["overtime_minutes"] == 0
    assert result["history"][0]["by"] == "hr.admin"


def test_regularization_model_rejects_invalid_times_and_ignores_derived_fields():
    valid = {
        "punch_in": 1_783_311_000_000,
        "reason": "Correct punch time",
        "regularized_by": "hr.admin",
        "late_minutes": 999,
    }
    request = RegularizeIn(**valid)

    assert request.punch_in == 1_783_311_000_000
    assert not hasattr(request, "late_minutes")
    for changes in (
        {"punch_in": "1783311000000"},
        {"punch_in": 1_783_311_000_000.0},
        {"punch_in": None},
        {"status": None},
    ):
        with pytest.raises(ValidationError):
            RegularizeIn(**{**valid, **changes})