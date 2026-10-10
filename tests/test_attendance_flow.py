from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone


UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def epoch_ms(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1000)


def make_employee(emp_code: str, shift_start: str = "09:30", shift_end: str = "18:30") -> dict:
    return {
        "emp_code": emp_code,
        "name": f"Employee {emp_code}",
        "email": f"{emp_code.lower()}@example.com",
        "department": "Phase3",
        "shift_start": shift_start,
        "shift_end": shift_end,
        "joined_on": "2026-01-01",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


def make_record(emp_code: str, punch_in: datetime, **overrides) -> dict:
    record = {
        "emp_code": emp_code,
        "date": "2026-07-06",
        "status": "PRESENT",
        "punch_in": punch_in.astimezone(UTC),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": 0,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    record.update(overrides)
    return record


def remove_employee_data(mongo_database, emp_code: str) -> None:
    mongo_database.attendance_logs.delete_many({"emp_code": emp_code})
    mongo_database.employees.delete_many({"emp_code": emp_code})


def test_overnight_punch_out_after_midnight(client, mongo_database):
    emp_code = "EMP9801"
    remove_employee_data(mongo_database, emp_code)
    mongo_database.employees.insert_one(make_employee(emp_code, "22:00", "06:00"))
    punch_in = datetime(2026, 7, 6, 21, 55, tzinfo=IST)
    mongo_database.attendance_logs.insert_one(make_record(emp_code, punch_in))
    punch_out = datetime(2026, 7, 7, 6, 40, tzinfo=IST)

    try:
        response = client.post(
            "/attendance/punch-out", json={"emp_code": emp_code, "punched_at": epoch_ms(punch_out)}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["date"] == "2026-07-06"
        assert result["punch_out"] == epoch_ms(punch_out)
        assert result["work_hours"] == 8.75
        assert result["overtime_minutes"] == 40
        assert result["half_day"] is False
        assert result["history"] == []
    finally:
        remove_employee_data(mongo_database, emp_code)


def test_punch_out_validation_and_already_closed_conflict(client, mongo_database):
    emp_code = "EMP9802"
    remove_employee_data(mongo_database, emp_code)
    mongo_database.employees.insert_one(make_employee(emp_code))
    punch_in = datetime(2026, 7, 6, 3, 30, tzinfo=UTC)
    mongo_database.attendance_logs.insert_one(make_record(emp_code, punch_in))
    same_time = epoch_ms(datetime(2026, 7, 6, 3, 30, tzinfo=UTC))
    too_late = epoch_ms(datetime(2026, 7, 7, 3, 31, tzinfo=UTC))
    valid_out = epoch_ms(datetime(2026, 7, 6, 13, 0, tzinfo=UTC))

    try:
        assert client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": same_time}).status_code == 422
        assert client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": too_late}).status_code == 422
        assert client.post("/attendance/punch-out", json={"emp_code": "EMP9999", "punched_at": valid_out}).status_code == 404
        assert client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": valid_out}).status_code == 200
        assert client.post("/attendance/punch-out", json={"emp_code": emp_code, "punched_at": valid_out}).status_code == 409
    finally:
        remove_employee_data(mongo_database, emp_code)


def test_regularization_clears_absent_times_and_rejects_invalid_requests(client, mongo_database):
    emp_code = "EMP9803"
    remove_employee_data(mongo_database, emp_code)
    mongo_database.employees.insert_one(make_employee(emp_code))
    punch_in = datetime(2026, 7, 6, 3, 30, tzinfo=UTC)
    mongo_database.attendance_logs.insert_one(
        make_record(emp_code, punch_in, late_minutes=0, work_hours=8.0, overtime_minutes=0, half_day=False)
    )
    url = f"/attendance/{emp_code}/2026-07-06"
    audit_fields = {"reason": "Approved absence", "regularized_by": "hr.admin"}

    try:
        no_op = client.patch(url, json=audit_fields)
        assert no_op.status_code == 422
        invalid_absent = client.patch(url, json={**audit_fields, "status": "ABSENT", "punch_in": epoch_ms(punch_in)})
        assert invalid_absent.status_code == 422
        response = client.patch(url, json={**audit_fields, "status": "ABSENT"})
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "ABSENT"
        assert result["punch_in"] is None
        assert result["punch_out"] is None
        assert result["work_hours"] is None
        assert result["late_minutes"] == 0
        assert result["overtime_minutes"] == 0
        assert result["half_day"] is False
        assert set(result["history"][0]["changes"]) == {"status", "punch_in", "work_hours"}
        assert "id" not in result and "_id" not in result
    finally:
        remove_employee_data(mongo_database, emp_code)


def test_regularization_recomputes_and_audits_only_actual_changes(client, mongo_database):
    emp_code = "EMP9804"
    remove_employee_data(mongo_database, emp_code)
    mongo_database.employees.insert_one(make_employee(emp_code))
    original_in = datetime(2026, 7, 6, 10, 5, tzinfo=IST)
    mongo_database.attendance_logs.insert_one(
        make_record(emp_code, original_in, late_minutes=35)
    )
    corrected_in = datetime(2026, 7, 6, 9, 28, tzinfo=IST)

    try:
        response = client.patch(
            f"/attendance/{emp_code}/2026-07-06",
            json={
                "punch_in": epoch_ms(corrected_in),
                "reason": "Biometric correction",
                "regularized_by": "hr.admin",
                "late_minutes": 999,
            },
        )
        assert response.status_code == 200
        result = response.json()
        assert result["punch_in"] == epoch_ms(corrected_in)
        assert result["late_minutes"] == 0
        assert len(result["history"]) == 1
        assert result["history"][0]["by"] == "hr.admin"
        assert result["history"][0]["reason"] == "Biometric correction"
        assert result["history"][0]["changes"] == {
            "punch_in": {"from": epoch_ms(original_in), "to": epoch_ms(corrected_in)},
            "late_minutes": {"from": 35, "to": 0},
        }
    finally:
        remove_employee_data(mongo_database, emp_code)


def test_simultaneous_punch_out_has_one_winner(client, mongo_database):
    emp_code = "EMP9805"
    remove_employee_data(mongo_database, emp_code)
    mongo_database.employees.insert_one(make_employee(emp_code))
    mongo_database.attendance_logs.insert_one(
        make_record(emp_code, datetime(2026, 7, 6, 3, 30, tzinfo=UTC))
    )
    request = {"emp_code": emp_code, "punched_at": epoch_ms(datetime(2026, 7, 6, 13, 0, tzinfo=UTC))}

    try:
        with ThreadPoolExecutor(max_workers=20) as pool:
            responses = list(pool.map(lambda _: client.post("/attendance/punch-out", json=request), range(20)))
        assert sum(response.status_code == 200 for response in responses) == 1
        assert all(response.status_code in (200, 409) for response in responses)
    finally:
        remove_employee_data(mongo_database, emp_code)


def test_simultaneous_regularizations_preserve_every_successful_history_entry(client, mongo_database):
    emp_code = "EMP9806"
    remove_employee_data(mongo_database, emp_code)
    mongo_database.employees.insert_one(make_employee(emp_code))
    mongo_database.attendance_logs.insert_one(
        make_record(emp_code, datetime(2026, 7, 6, 3, 0, tzinfo=UTC))
    )
    requests = [
        {
            "punch_in": epoch_ms(datetime(2026, 7, 6, 3, minute, tzinfo=UTC)),
            "reason": f"Concurrent correction {minute}",
            "regularized_by": f"hr{minute}",
        }
        for minute in range(1, 11)
    ]

    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(
                pool.map(
                    lambda payload: client.patch(f"/attendance/{emp_code}/2026-07-06", json=payload),
                    requests,
                )
            )
        successes = sum(response.status_code == 200 for response in responses)
        assert all(response.status_code in (200, 409) for response in responses)
        assert len(mongo_database.attendance_logs.find_one({"emp_code": emp_code})["history"]) == successes
    finally:
        remove_employee_data(mongo_database, emp_code)