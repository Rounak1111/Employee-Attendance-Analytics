from pathlib import Path
import json

from app.main import db

base = Path(__file__).resolve().parent / "sample_data"

with open(base / "employees.json", "r", encoding="utf-8") as fp:
    employees = json.load(fp)
with open(base / "attendance_logs.json", "r", encoding="utf-8") as fp:
    attendance_logs = json.load(fp)

if employees:
    db.employees.delete_many({})
    db.employees.insert_many(employees)
if attendance_logs:
    db.attendance_logs.delete_many({})
    db.attendance_logs.insert_many(attendance_logs)

print("Seeded sample data.")
