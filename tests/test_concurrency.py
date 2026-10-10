from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone


UTC = timezone.utc


def test_twenty_simultaneous_punch_ins_create_one_record(client, mongo_database):
    emp_code = "EMP9701"
    mongo_database.attendance_logs.delete_many({"emp_code": emp_code})
    mongo_database.employees.delete_many({"emp_code": emp_code})
    mongo_database.employees.insert_one(
        {
            "emp_code": emp_code,
            "name": "Concurrency Employee",
            "email": "concurrency@example.com",
            "department": "Concurrency",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-01-01",
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        }
    )
    request = {"emp_code": emp_code, "punched_at": 1_783_311_000_000}

    try:
        with ThreadPoolExecutor(max_workers=20) as pool:
            responses = list(pool.map(lambda _: client.post("/attendance/punch-in", json=request), range(20)))
        assert sum(response.status_code == 201 for response in responses) == 1
        assert all(response.status_code in (201, 409) for response in responses)
        assert mongo_database.attendance_logs.count_documents({"emp_code": emp_code}) == 1
    finally:
        mongo_database.attendance_logs.delete_many({"emp_code": emp_code})
        mongo_database.employees.delete_many({"emp_code": emp_code})