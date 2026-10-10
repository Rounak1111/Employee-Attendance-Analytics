from datetime import datetime, timezone

from app.main import to_epoch_ms


UTC = timezone.utc


def employee_document(emp_code: str, department: str, shift_start: str = "09:30", shift_end: str = "18:30"):
    return {
        "emp_code": emp_code,
        "name": f"Employee {emp_code}",
        "email": f"{emp_code.lower()}@example.com",
        "department": department,
        "shift_start": shift_start,
        "shift_end": shift_end,
        "joined_on": "2026-01-01",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


def test_health_pings_mongo(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_employee_serializes_and_duplicate_conflicts(client):
    payload = employee_document("EMP9901", "Phase2")
    payload.pop("created_at")
    response = client.post("/employees", json=payload)

    assert response.status_code == 201
    result = response.json()
    assert isinstance(result["created_at"], int)
    assert "id" not in result
    assert "_id" not in result
    assert client.post("/employees", json=payload).status_code == 409


def test_employee_list_filters_sorts_and_paginates(client, mongo_database):
    codes = ["EMP9902", "EMP9903", "EMP9904"]
    mongo_database.employees.delete_many({"emp_code": {"$in": codes}})
    mongo_database.employees.insert_many([employee_document(code, "Phase2") for code in codes])

    try:
        response = client.get("/employees", params={"department": "Phase2", "page": 2, "page_size": 1})
        assert response.status_code == 200
        result = response.json()
        assert result["total"] == 3
        assert [item["emp_code"] for item in result["items"]] == ["EMP9903"]
        assert result["items"][0]["created_at"] == to_epoch_ms(datetime(2026, 1, 1, tzinfo=UTC))
        assert "_id" not in result["items"][0]
    finally:
        mongo_database.employees.delete_many({"emp_code": {"$in": codes}})


def test_punch_in_uses_ist_rules_and_unique_index(client, mongo_database):
    emp_code = "EMP9905"
    mongo_database.employees.delete_many({"emp_code": emp_code})
    mongo_database.attendance_logs.delete_many({"emp_code": emp_code})
    mongo_database.employees.insert_one(employee_document(emp_code, "Phase2"))
    punched_at = 1_783_311_001_000  # 2026-07-06 09:40:01 IST

    try:
        response = client.post(
            "/attendance/punch-in",
            json={"emp_code": emp_code, "punched_at": punched_at, "status": "WFH", "work_hours": 999},
        )
        assert response.status_code == 201
        result = response.json()
        assert result["date"] == "2026-07-06"
        assert result["status"] == "WFH"
        assert result["late_minutes"] == 10
        assert result["punch_in"] == 1_783_311_000_000
        assert "id" not in result and "_id" not in result
        assert client.post("/attendance/punch-in", json={"emp_code": emp_code, "punched_at": punched_at}).status_code == 409
    finally:
        mongo_database.attendance_logs.delete_many({"emp_code": emp_code})
        mongo_database.employees.delete_many({"emp_code": emp_code})


def test_punch_in_unknown_employee_and_bad_timestamp_return_client_errors(client):
    assert client.post("/attendance/punch-in", json={"emp_code": "EMP9999"}).status_code == 404
    assert client.post(
        "/attendance/punch-in", json={"emp_code": "EMP0001", "punched_at": "1783312500000"}
    ).status_code == 422


def test_attendance_list_filters_sorts_and_normalizes_legacy_records(client, mongo_database):
    emp_code = "EMP9906"
    mongo_database.attendance_logs.delete_many({"emp_code": emp_code})
    mongo_database.attendance_logs.insert_many(
        [
            {"emp_code": emp_code, "date": "2026-07-06", "status": "PRESENT", "punch_in": None, "punch_out": None, "work_hours": None},
            {"emp_code": emp_code, "date": "2026-07-07", "status": "LEAVE", "punch_in": None, "punch_out": None, "work_hours": None},
        ]
    )

    try:
        response = client.get("/attendance", params={"emp_code": emp_code, "page": 1, "page_size": 1})
        assert response.status_code == 200
        result = response.json()
        assert result["total"] == 2
        assert result["items"][0]["date"] == "2026-07-07"
        assert result["items"][0]["history"] == []
        assert result["items"][0]["half_day"] is False
        assert result["items"][0]["late_minutes"] == 0
        assert "id" not in result["items"][0] and "_id" not in result["items"][0]
        assert client.get(
            "/attendance", params={"date_from": "2026-07-08", "date_to": "2026-07-06"}
        ).status_code == 422
    finally:
        mongo_database.attendance_logs.delete_many({"emp_code": emp_code})