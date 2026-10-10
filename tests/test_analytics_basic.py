from datetime import date, datetime, timezone

from app.main import (
    build_department_summary_pipeline,
    build_employee_monthly_pipeline,
    month_end,
    month_start,
    parse_month,
    working_days,
)


UTC = timezone.utc
MONTH = "2026-07"
EMPLOYEE_CODES = ["EMP9601", "EMP9602", "EMP9603", "EMP9604", "EMP9605", "EMP9606", "EMP9607"]


def analytics_employee(emp_code: str, department: str, joined_on: str) -> dict:
    return {
        "emp_code": emp_code,
        "name": f"Employee {emp_code}",
        "email": f"{emp_code.lower()}@example.com",
        "department": department,
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": joined_on,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }


def attendance_record(emp_code: str, day: str, status: str, **values) -> dict:
    record = {
        "emp_code": emp_code,
        "date": day,
        "status": status,
        "punch_in": datetime(2026, 7, 6, 4, 0, tzinfo=UTC),
        "punch_out": datetime(2026, 7, 6, 13, 0, tzinfo=UTC),
        "work_hours": None,
        "late_minutes": 0,
        "overtime_minutes": 0,
    }
    record.update(values)
    return record


def clear_analytics_data(mongo_database):
    mongo_database.attendance_logs.delete_many({"emp_code": {"$in": EMPLOYEE_CODES}})
    mongo_database.employees.delete_many({"emp_code": {"$in": EMPLOYEE_CODES}})


def test_month_helpers_and_pipeline_shapes():
    parsed = parse_month(MONTH)
    assert parsed == date(2026, 7, 1)
    assert month_start(parsed) == "2026-07-01"
    assert month_end(parsed) == "2026-07-31"
    assert working_days(parsed, "2026-01-01") == 23
    assert working_days(parsed, "2026-07-15") == 13
    assert working_days(parsed, "2026-08-01") == 0

    monthly_pipeline = build_employee_monthly_pipeline("EMP9602", parsed)
    assert monthly_pipeline[0]["$match"] == {
        "emp_code": "EMP9602",
        "date": {"$gte": "2026-07-01", "$lte": "2026-07-31"},
    }
    department_pipeline = build_department_summary_pipeline(parsed, "Engineering")
    assert department_pipeline[0]["$match"] == {
        "joined_on": {"$lte": "2026-07-31"},
        "department": "Engineering",
    }
    assert department_pipeline[1]["$lookup"]["from"] == "attendance_logs"
    assert department_pipeline[1]["$lookup"]["pipeline"][0]["$match"]["$expr"]["$and"]


def test_monthly_and_department_analytics_use_stored_values(client, mongo_database):
    clear_analytics_data(mongo_database)
    employees = [
        analytics_employee("EMP9601", "Engineering", "2026-01-01"),  # No logs, still in headcount.
        analytics_employee("EMP9602", "Engineering", "2026-07-15"),  # Mid-month joiner.
        analytics_employee("EMP9603", "LaterOnly", "2026-08-01"),  # Excluded by R9 for July.
        analytics_employee("EMP9604", "Engineering", "2025-03-01"),
        analytics_employee("EMP9605", "Engineering", "2025-03-01"),
        analytics_employee("EMP9606", "Engineering", "2025-03-01"),
        analytics_employee("EMP9607", "Engineering", "2025-03-01"),
    ]
    mongo_database.employees.insert_many(employees)
    mongo_database.attendance_logs.insert_many(
        [
            attendance_record(
                "EMP9602",
                "2026-07-15",
                "WFH",
                work_hours=4.49,
                half_day=True,
            ),
            attendance_record(
                "EMP9602",
                "2026-07-18",
                "PRESENT",
                work_hours=8.0,
                late_minutes=17,
                overtime_minutes=35,
                half_day=False,
            ),  # Weekend: excluded from present_days but included in late/overtime totals.
            attendance_record(
                "EMP9604", "2026-07-06", "ON_DUTY", work_hours=8.0, half_day=False
            ),
            attendance_record(
                "EMP9605",
                "2026-07-07",
                "LEAVE",
                punch_in=None,
                punch_out=None,
                work_hours=None,
            ),
            attendance_record(
                "EMP9606",
                "2026-07-08",
                "ABSENT",
                punch_in=None,
                punch_out=None,
                work_hours=None,
            ),
            attendance_record(
                "EMP9607",
                "2026-07-09",
                "WFH",
                work_hours=7.5,
            ),  # Legacy shape intentionally omits half_day and history.
        ]
    )

    try:
        monthly_response = client.get(
            "/analytics/employees/EMP9602/monthly", params={"month": MONTH}
        )
        assert monthly_response.status_code == 200
        monthly = monthly_response.json()
        # Jul 15-31 has 13 weekdays; the half-day contributes 0.5, so 0.5 / 13 * 100 = 3.85%.
        # The Saturday record still contributes its stored 17 late and 35 overtime minutes.
        assert monthly == {
            "emp_code": "EMP9602",
            "month": MONTH,
            "working_days": 13,
            "present_days": 0.5,
            "leave_days": 0,
            "late_count": 1,
            "total_late_minutes": 17,
            "total_overtime_minutes": 35,
            "attendance_pct": 3.85,
        }

        no_logs = client.get(
            "/analytics/employees/EMP9601/monthly", params={"month": MONTH}
        ).json()
        assert no_logs["working_days"] == 23
        assert no_logs["present_days"] == 0
        assert no_logs["attendance_pct"] == 0.0

        after_month = client.get(
            "/analytics/employees/EMP9603/monthly", params={"month": MONTH}
        ).json()
        assert after_month["working_days"] == 0
        assert after_month["attendance_pct"] is None

        summary_response = client.get("/analytics/departments/summary", params={"month": MONTH})
        assert summary_response.status_code == 200
        summary = summary_response.json()
        assert [item["department"] for item in summary["items"]] == ["Engineering"]
        # Six eligible employees count, including one with no logs; present weekdays total 0.5 + 1 + 1.
        # The four qualifying work-hour records average (4.49 + 8 + 8 + 7.5) / 4 = 7.00 half-up.
        assert summary["items"][0] == {
            "department": "Engineering",
            "headcount": 6,
            "present_days": 2.5,
            "avg_work_hours": 7.0,
            "late_count": 1,
            "total_late_minutes": 17,
            "leave_count": 1,
            "on_duty_count": 1,
        }

        filtered = client.get(
            "/analytics/departments/summary",
            params={"month": MONTH, "department": "LaterOnly"},
        ).json()
        assert filtered["items"] == []
        assert client.get(
            "/analytics/employees/EMP9999/monthly", params={"month": MONTH}
        ).status_code == 404
        assert client.get(
            "/analytics/departments/summary", params={"month": "2026-13"}
        ).status_code == 422
    finally:
        clear_analytics_data(mongo_database)


def test_late_leaderboard_and_department_trend_contract(client, mongo_database):
    clear_analytics_data(mongo_database)
    employees = [
        analytics_employee("EMP9701", "Engineering", "2026-01-01"),
        analytics_employee("EMP9702", "Engineering", "2026-01-01"),
        analytics_employee("EMP9703", "HR", "2026-01-01"),
    ]
    mongo_database.employees.insert_many(employees)
    mongo_database.attendance_logs.insert_many(
        [
            attendance_record("EMP9701", "2026-07-01", "PRESENT", late_minutes=15),
            attendance_record("EMP9701", "2026-07-02", "PRESENT", late_minutes=15),
            attendance_record("EMP9702", "2026-07-01", "PRESENT", late_minutes=10),
            attendance_record("EMP9703", "2026-07-01", "PRESENT", late_minutes=20),
        ]
    )

    leaderboard = client.get(
        "/analytics/leaderboard/late", params={"month": MONTH, "limit": 10}
    )
    assert leaderboard.status_code == 200
    assert leaderboard.json()["items"][0] == {
        "rank": 1,
        "emp_code": "EMP9701",
        "name": "Employee EMP9701",
        "department": "Engineering",
        "total_late_minutes": 30,
        "late_count": 2,
    }

    trend = client.get(
        "/analytics/departments/Engineering/trend",
        params={"from": "2026-07-01", "to": "2026-07-03"},
    )
    assert trend.status_code == 200
    assert trend.json()["department"] == "Engineering"
    assert len(trend.json()["items"]) == 3
    assert trend.json()["items"][0]["headcount"] == 2
    assert trend.json()["items"][0]["attendance_rate"] == 1.0

    clear_analytics_data(mongo_database)