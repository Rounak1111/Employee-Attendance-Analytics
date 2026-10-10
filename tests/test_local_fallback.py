from pathlib import Path

from fastapi.testclient import TestClient

from app.main import LocalDatabase, app, client, db


def test_local_fallback_without_mongodb(tmp_path):
    local_db = LocalDatabase(tmp_path / "attendance_store.json")
    original_db = db
    original_client = client
    try:
        import app.main as main_module

        main_module.db = local_db
        main_module.client = None

        with TestClient(app) as test_client:
            response = test_client.get("/health")
            assert response.status_code == 200
            assert response.json() == {"status": "ok"}

            employee_response = test_client.post(
                "/employees",
                json={
                    "emp_code": "EMP9999",
                    "name": "Local Fallback",
                    "email": "fallback@example.com",
                    "department": "Engineering",
                    "shift_start": "09:30",
                    "shift_end": "18:30",
                    "joined_on": "2024-01-01",
                },
            )
            assert employee_response.status_code == 201
            assert employee_response.json()["emp_code"] == "EMP9999"
            assert local_db.employees.count_documents({}) == 1
    finally:
        import app.main as main_module

        main_module.db = original_db
        main_module.client = original_client
