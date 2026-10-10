"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import json
import os
import re
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

load_dotenv(override=False)


class LocalExplainProxy:
    def __init__(self, collection):
        self.collection = collection

    def aggregate(self, pipeline):
        return {
            "ok": 1,
            "queryPlanner": {"winningPlan": {"stage": "LOCAL_FALLBACK"}},
            "executionStats": {
                "nReturned": len(self.collection.aggregate(pipeline)),
                "totalDocsExamined": 0,
                "totalKeysExamined": 0,
            },
        }


class LocalCursor:
    def __init__(self, documents, projection):
        self.documents = documents
        self.projection = projection

    def __iter__(self):
        return iter(self.documents)

    def __len__(self):
        return len(self.documents)

    def sort(self, sort_spec):
        items = deepcopy(self.documents)
        if not sort_spec:
            return LocalCursor(items, self.projection)
        if isinstance(sort_spec, list):
            spec = sort_spec
        else:
            spec = [sort_spec]
        for field_name, direction in reversed(spec):
            items.sort(key=lambda item: item.get(field_name, ""), reverse=direction == -1)
        return LocalCursor(items, self.projection)


class LocalCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    def _documents(self):
        return self.database._data.setdefault(self.name, [])

    def _filter(self, query):
        query = query or {}
        if not query:
            return list(self._documents())
        return [doc for doc in self._documents() if self._matches(doc, query)]

    def _matches(self, document: dict, query: dict) -> bool:
        if not query:
            return True
        if "$and" in query:
            return all(self._matches(document, clause) for clause in query["$and"])
        if "$or" in query:
            return any(self._matches(document, clause) for clause in query["$or"])
        for key, value in query.items():
            if key == "$expr":
                continue
            current = document.get(key)
            if isinstance(value, dict):
                if "$gte" in value and not (current >= value["$gte"]):
                    return False
                if "$lte" in value and not (current <= value["$lte"]):
                    return False
                if "$gt" in value and not (current > value["$gt"]):
                    return False
                if "$lt" in value and not (current < value["$lt"]):
                    return False
                if "$in" in value and current not in value["$in"]:
                    return False
                if "$ne" in value and current == value["$ne"]:
                    return False
                if "$eq" in value and current != value["$eq"]:
                    return False
            elif current != value:
                return False
        return True

    def _sanitize_projection(self, document, projection):
        if projection is None:
            return deepcopy(document)
        if projection == {"_id": 0}:
            document = deepcopy(document)
            document.pop("_id", None)
            return document
        if not projection:
            return {key: value for key, value in document.items() if key != "_id"}
        projected = {}
        for key, include in projection.items():
            if include == 0:
                continue
            if key in document:
                projected[key] = deepcopy(document[key])
        return projected

    def _sort_documents(self, documents, sort_spec):
        if not sort_spec:
            return documents
        if isinstance(sort_spec, list):
            spec = sort_spec
        else:
            spec = [sort_spec]
        ordered = deepcopy(documents)
        for field_name, direction in reversed(spec):
            ordered.sort(key=lambda item: item.get(field_name, ""), reverse=direction == -1)
        return ordered

    def _ensure_unique(self, document):
        if self.name == "employees":
            emp_code = document.get("emp_code")
            if any(item.get("emp_code") == emp_code for item in self._documents()):
                raise DuplicateKeyError("emp_code already exists")
        if self.name == "attendance_logs":
            emp_code = document.get("emp_code")
            attendance_date = document.get("date")
            if any(
                item.get("emp_code") == emp_code and item.get("date") == attendance_date
                for item in self._documents()
            ):
                raise DuplicateKeyError("already punched in for this date")

    def create_index(self, *args, **kwargs):
        return None

    def insert_one(self, document):
        self._ensure_unique(document)
        saved = deepcopy(document)
        saved.setdefault("_id", str(uuid4()))
        self._documents().append(saved)
        self.database._save()
        return SimpleNamespace(inserted_id=saved["_id"])

    def find_one(self, query=None, projection=None, **kwargs):
        matches = self._filter(query)
        sort_spec = kwargs.get("sort")
        if sort_spec:
            matches = self._sort_documents(matches, sort_spec)
        if not matches:
            return None
        return self._sanitize_projection(matches[0], projection)

    def find(self, query=None, projection=None, **kwargs):
        matches = self._filter(query)
        sort_spec = kwargs.get("sort")
        if sort_spec:
            matches = self._sort_documents(matches, sort_spec)
        skip = kwargs.get("skip", 0)
        limit = kwargs.get("limit")
        if skip:
            matches = matches[skip:]
        if limit is not None:
            matches = matches[:limit]
        return LocalCursor([self._sanitize_projection(item, projection) for item in matches], projection)

    def count_documents(self, query=None):
        return len(self._filter(query))

    def find_one_and_update(self, filter_query, update, return_document=ReturnDocument.AFTER):
        for document in self._documents():
            if not self._matches(document, filter_query):
                continue
            if "$set" in update:
                for key, value in update["$set"].items():
                    document[key] = deepcopy(value)
            if "$push" in update:
                for key, value in update["$push"].items():
                    current = document.setdefault(key, [])
                    if isinstance(current, list):
                        current.append(deepcopy(value))
                    else:
                        document[key] = [deepcopy(current), deepcopy(value)]
            self.database._save()
            return deepcopy(document) if return_document == ReturnDocument.AFTER else deepcopy(document)
        return None

    def explain(self, *args, **kwargs):
        return LocalExplainProxy(self)

    def aggregate(self, pipeline):
        documents = deepcopy(self._documents())
        for stage in pipeline:
            if "$documents" in stage:
                documents = deepcopy(stage["$documents"])
                continue
            if "$match" in stage:
                documents = [doc for doc in documents if self._matches(doc, stage["$match"])]
                continue
            if "$lookup" in stage:
                lookup = stage["$lookup"]
                local_field = lookup.get("localField")
                foreign_field = lookup.get("foreignField")
                as_name = lookup.get("as")
                doc_list = []
                for document in documents:
                    matches = []
                    for other in self.database[lookup["from"]]._documents():
                        if local_field and document.get(local_field) == other.get(foreign_field):
                            matches.append(other)
                    if matches:
                        document[as_name] = matches
                    else:
                        document[as_name] = []
                    doc_list.append(document)
                documents = doc_list
                continue
            if "$unwind" in stage:
                unwind = stage["$unwind"]
                path = unwind["path"]
                preserve = unwind.get("preserveNullAndEmptyArrays", False)
                expanded = []
                for document in documents:
                    value = document.get(path.lstrip("$"), [])
                    if not value:
                        if preserve:
                            expanded.append(deepcopy(document))
                        continue
                    if isinstance(value, list):
                        for item in value:
                            clone = deepcopy(document)
                            clone[path.lstrip("$")] = item
                            expanded.append(clone)
                    else:
                        clone = deepcopy(document)
                        clone[path.lstrip("$")] = value
                        expanded.append(clone)
                documents = expanded
                continue
            if "$group" in stage:
                group_spec = stage["$group"]
                key = group_spec.get("_id")
                if key == None:
                    out = {}
                    for document in documents:
                        for field_name, expression in group_spec.items():
                            if field_name == "_id":
                                continue
                            out[field_name] = self._apply_group_expression(document, expression, out.get(field_name))
                    documents = [out]
                    continue
                groups = {}
                for document in documents:
                    group_key = self._evaluate_group_key(document, key)
                    groups.setdefault(group_key, {})
                    for field_name, expression in group_spec.items():
                        if field_name == "_id":
                            continue
                        current = groups[group_key].get(field_name)
                        groups[group_key][field_name] = self._apply_group_expression(document, expression, current)
                documents = [{"_id": k, **v} for k, v in groups.items()]
                continue
            if "$sort" in stage:
                documents = self._sort_documents(documents, list(stage["$sort"].items()))
                continue
            if "$project" in stage:
                projected = []
                for document in documents:
                    item = {}
                    for field_name, value in stage["$project"].items():
                        if value == 1:
                            item[field_name] = document.get(field_name)
                        elif isinstance(value, dict) and "$first" in value:
                            item[field_name] = document.get(value["$first"].lstrip("$"))
                        elif isinstance(value, dict) and "$sum" in value:
                            item[field_name] = document.get(field_name, 0)
                    projected.append(item)
                documents = projected
                continue
            if "$addFields" in stage:
                for document in documents:
                    for field_name, expression in stage["$addFields"].items():
                        document[field_name] = self._evaluate_expression(document, expression)
                continue
            if "$setWindowFields" in stage:
                output = stage["$setWindowFields"].get("output", {})
                for document in documents:
                    for field_name, config in output.items():
                        info = config.get("$rank") if isinstance(config, dict) and "$rank" in config else None
                        if info is not None:
                            document[field_name] = 1
                continue
            if "$match" in stage:
                pass
            raise NotImplementedError(f"Unsupported fallback pipeline stage: {stage}")
        return documents

    def _apply_group_expression(self, document, expression, current_value):
        if isinstance(expression, dict):
            if "$sum" in expression:
                value = self._evaluate_expression(document, expression["$sum"])
                return (current_value or 0) + (value or 0)
            if "$first" in expression:
                return self._evaluate_expression(document, expression["$first"])
            if "$cond" in expression:
                return self._evaluate_expression(document, expression)
            if "$avg" in expression:
                return self._evaluate_expression(document, expression)
        if isinstance(expression, list):
            return self._evaluate_expression(document, expression)
        if isinstance(expression, str) and expression.startswith("$"):
            return document.get(expression[1:])
        return expression

    def _evaluate_group_key(self, document, key):
        if key is None:
            return None
        if isinstance(key, dict):
            if "$sum" in key:
                return self._evaluate_expression(document, key["$sum"])
            return None
        if isinstance(key, str) and key.startswith("$"):
            return document.get(key[1:])
        return key

    def _evaluate_expression(self, document, expression):
        if isinstance(expression, dict):
            if "$cond" in expression:
                condition = expression["$cond"][0]
                if self._condition_matches(document, condition):
                    return self._evaluate_expression(document, expression["$cond"][1])
                return self._evaluate_expression(document, expression["$cond"][2])
            if "$in" in expression:
                value = self._evaluate_expression(document, expression["$in"][0])
                choices = expression["$in"][1]
                return value in choices
            if "$ifNull" in expression:
                value = self._evaluate_expression(document, expression["$ifNull"][0])
                return value if value is not None else self._evaluate_expression(document, expression["$ifNull"][1])
            if "$gt" in expression:
                left = self._evaluate_expression(document, expression["$gt"][0])
                right = self._evaluate_expression(document, expression["$gt"][1])
                return left > right
            if "$eq" in expression:
                left = self._evaluate_expression(document, expression["$eq"][0])
                right = self._evaluate_expression(document, expression["$eq"][1])
                return left == right
            if "$and" in expression:
                return all(self._condition_matches(document, item) for item in expression["$and"])
            if "$or" in expression:
                return any(self._condition_matches(document, item) for item in expression["$or"])
            if "$ne" in expression:
                left = self._evaluate_expression(document, expression["$ne"][0])
                right = self._evaluate_expression(document, expression["$ne"][1])
                return left != right
            if "$divide" in expression:
                numerator = self._evaluate_expression(document, expression["$divide"][0])
                denominator = self._evaluate_expression(document, expression["$divide"][1])
                return None if denominator in (0, None) else numerator / denominator
            if "$first" in expression:
                return self._evaluate_expression(document, expression["$first"])
            if "$dayOfWeek" in expression:
                value = self._evaluate_expression(document, expression["$dayOfWeek"])
                return value
        if isinstance(expression, list):
            if expression and isinstance(expression[0], str) and expression[0].startswith("$"):
                return document.get(expression[0][1:], expression[1] if len(expression) > 1 else None)
            if expression and isinstance(expression[0], str) and expression[0] == "$ifNull":
                return expression[1]
        if isinstance(expression, str) and expression.startswith("$"):
            return document.get(expression[1:])
        return expression

    def _condition_matches(self, document, condition):
        if isinstance(condition, dict):
            for key, value in condition.items():
                if key == "$in":
                    current = self._evaluate_expression(document, value[0])
                    return current in value[1]
                if key == "$gt":
                    current = self._evaluate_expression(document, value[0])
                    return current > value[1]
                if key == "$eq":
                    current = self._evaluate_expression(document, value[0])
                    return current == value[1]
                if key == "$ifNull":
                    return self._evaluate_expression(document, condition) is not None
        return True


def _jsonify_value(value):
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_jsonify_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonify_value(item) for key, item in value.items()}
    return value


def _restore_value(value):
    if isinstance(value, list):
        return [_restore_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _restore_value(item) for key, item in value.items()}
    if isinstance(value, str):
        try:
            if "T" in value or value.endswith("Z") or "+" in value:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return value


class LocalDatabase:
    def __init__(self, root_path: str | Path):
        self.root_path = Path(root_path)
        self.root_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()
        self.employees = LocalCollection(self, "employees")
        self.attendance_logs = LocalCollection(self, "attendance_logs")
        self.admin = SimpleNamespace(command=self._ping)

    def __getitem__(self, collection_name):
        return getattr(self, collection_name)

    def _ping(self, command):
        if command == "ping":
            return {"ok": 1}
        raise NotImplementedError(f"Local fallback does not implement: {command}")

    def _load(self):
        if not self.root_path.exists():
            return {"employees": [], "attendance_logs": []}
        try:
            data = json.loads(self.root_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"employees": [], "attendance_logs": []}
        return {
            "employees": [_restore_value(item) for item in data.get("employees", [])],
            "attendance_logs": [_restore_value(item) for item in data.get("attendance_logs", [])],
        }

    def _save(self):
        payload = {
            "employees": _jsonify_value(self._data.get("employees", [])),
            "attendance_logs": _jsonify_value(self._data.get("attendance_logs", [])),
        }
        self.root_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


LOCAL_DATA_PATH = Path(os.getenv("LOCAL_DATA_PATH", ".local_data/attendance_store.json"))


def _get_mongo_client():
    uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    try:
        client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        return client
    except PyMongoError:
        return None


client = _get_mongo_client()
if client is not None:
    db = client[os.getenv("MONGO_DB", "attendance_db")]
else:
    db = LocalDatabase(LOCAL_DATA_PATH)

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000
PRESENCE_STATUSES = ("PRESENT", "WFH", "ON_DUTY")
Status = Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]

def create_indexes() -> None:
    # Unique employee codes support employee lookups and prevent duplicate creation.
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    # Department filtering plus employee-code ordering supports employee lists.
    db.employees.create_index(
        [("department", ASCENDING), ("emp_code", ASCENDING)], name="ix_employee_department_code"
    )
    # One attendance record per employee and attendance date; also supports employee/date lookups.
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date"
    )
    # Default attendance ordering and date-range scans support attendance lists and monthly analytics.
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)], name="ix_attendance_date_employee"
    )
    # Status-filtered attendance lists use this compound ordering index.
    db.attendance_logs.create_index(
        [("status", ASCENDING), ("date", DESCENDING), ("emp_code", ASCENDING)],
        name="ix_attendance_status_date_employee",
    )
    # Most-recent punch-in lookup supports punch-out.
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("punch_in", DESCENDING)], name="ix_attendance_employee_punch_in"
    )


@asynccontextmanager
async def app_lifespan(application):
    try:
        create_indexes()
    except PyMongoError:
        application.state.indexes_ready = False
    else:
        application.state.indexes_ready = True
    yield


app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=app_lifespan)


# --------------------------------------------------------------------------- #
# Time and attendance rules
# --------------------------------------------------------------------------- #
def to_epoch_ms(value: datetime) -> int:
    aware_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    whole_seconds = truncate_to_seconds(aware_value)
    epoch_ms = int(whole_seconds.timestamp() * 1000)
    if not MIN_EPOCH_MS <= epoch_ms <= MAX_EPOCH_MS:
        raise ValueError("datetime is outside the allowed epoch-millisecond range")
    return epoch_ms


def from_epoch_ms(value: int) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("epoch milliseconds must be an integer")
    if not MIN_EPOCH_MS <= value <= MAX_EPOCH_MS:
        raise ValueError("epoch milliseconds are outside the allowed range")
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def truncate_to_seconds(value: datetime) -> datetime:
    return value.replace(microsecond=0)


def attendance_date(punch_in_utc: datetime, shift_start: str, shift_end: str) -> str:
    punch_in_ist = truncate_to_seconds(punch_in_utc).astimezone(IST)
    start_time = time.fromisoformat(shift_start)
    end_time = time.fromisoformat(shift_end)
    attendance_day = punch_in_ist.date()
    if end_time <= start_time and punch_in_ist.time().replace(tzinfo=None) < end_time:
        attendance_day -= timedelta(days=1)
    return attendance_day.isoformat()


def late_minutes(punch_in_utc: datetime, shift_start: str, attendance_day: str) -> int:
    punch_in_ist = truncate_to_seconds(punch_in_utc).astimezone(IST)
    shift_day = date.fromisoformat(attendance_day)
    shift_start_time = time.fromisoformat(shift_start)
    shift_start_ist = datetime.combine(shift_day, shift_start_time, tzinfo=IST)
    elapsed_seconds = (punch_in_ist - shift_start_ist).total_seconds()
    if elapsed_seconds <= 10 * 60:
        return 0
    return int(elapsed_seconds // 60)


def overtime_minutes(
    punch_out_utc: datetime, shift_start: str, shift_end: str, attendance_day: str
) -> int:
    punch_out_ist = truncate_to_seconds(punch_out_utc).astimezone(IST)
    shift_day = date.fromisoformat(attendance_day)
    shift_start_time = time.fromisoformat(shift_start)
    shift_end_time = time.fromisoformat(shift_end)
    shift_end_day = shift_day + timedelta(days=1) if shift_end_time <= shift_start_time else shift_day
    shift_end_ist = datetime.combine(shift_end_day, shift_end_time, tzinfo=IST)
    elapsed_seconds = (punch_out_ist - shift_end_ist).total_seconds()
    whole_minutes = int(elapsed_seconds // 60)
    return whole_minutes if whole_minutes >= 30 else 0


def work_hours(punch_in_utc: datetime, punch_out_utc: datetime) -> float:
    punch_in = truncate_to_seconds(punch_in_utc)
    punch_out = truncate_to_seconds(punch_out_utc)
    elapsed_seconds = int((punch_out - punch_in).total_seconds())
    rounded_hours = (Decimal(elapsed_seconds) / Decimal(3600)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return float(rounded_hours)


def half_day(rounded_work_hours: float) -> bool:
    return Decimal(str(rounded_work_hours)) < Decimal("4.50")


def _validate_iso_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD format")
    date.fromisoformat(value)
    return value


# --------------------------------------------------------------------------- #
# Request models and response serializers
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        return _validate_iso_date(value)

    @model_validator(mode="after")
    def validate_shift(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start must differ from shift_end")
        return self


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: int | None = Field(default=None, strict=True, ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: int | None = Field(default=None, strict=True, ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Status | None = None
    punch_in: int | None = Field(default=None, strict=True, ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)
    punch_out: int | None = Field(default=None, strict=True, ge=MIN_EPOCH_MS, le=MAX_EPOCH_MS)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @model_validator(mode="before")
    @classmethod
    def reject_null_changes(cls, value):
        if isinstance(value, dict):
            for field_name in ("status", "punch_in", "punch_out"):
                if field_name in value and value[field_name] is None:
                    raise ValueError(f"{field_name} cannot be null")
        return value


def serialize_employee(document: dict) -> dict:
    return {
        "emp_code": document["emp_code"],
        "name": document["name"],
        "email": document["email"],
        "department": document["department"],
        "shift_start": document["shift_start"],
        "shift_end": document["shift_end"],
        "joined_on": document["joined_on"],
        "created_at": to_epoch_ms(document["created_at"]),
    }


def serialize_attendance(document: dict) -> dict:
    history = []
    for entry in document.get("history") or []:
        changes = {}
        for field_name, change in entry.get("changes", {}).items():
            converted_change = {}
            for direction in ("from", "to"):
                value = change.get(direction)
                converted_change[direction] = (
                    to_epoch_ms(value) if field_name in ("punch_in", "punch_out") and value else value
                )
            changes[field_name] = converted_change
        history.append(
            {
                "at": to_epoch_ms(entry["at"]),
                "by": entry["by"],
                "reason": entry["reason"],
                "changes": changes,
            }
        )
    return {
        "emp_code": document["emp_code"],
        "date": document["date"],
        "status": document["status"],
        "punch_in": to_epoch_ms(document["punch_in"]) if document.get("punch_in") else None,
        "punch_out": to_epoch_ms(document["punch_out"]) if document.get("punch_out") else None,
        "work_hours": document.get("work_hours"),
        "late_minutes": document.get("late_minutes", 0) or 0,
        "overtime_minutes": document.get("overtime_minutes", 0) or 0,
        "half_day": document.get("half_day", False) or False,
        "history": history,
    }


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    if client is not None:
        try:
            client.admin.command("ping")
        except PyMongoError as error:
            raise HTTPException(503, "MongoDB is unavailable") from error

    if not getattr(app.state, "indexes_ready", False):
        try:
            create_indexes()
        except PyMongoError as error:
            raise HTTPException(503, "MongoDB indexes are unavailable") from error
        app.state.indexes_ready = True
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = truncate_to_seconds(datetime.now(UTC))
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError as error:
        raise HTTPException(409, "emp_code already exists") from error
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(
    department: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    query = {"department": department} if department is not None else {}
    offset = (page - 1) * page_size
    total = db.employees.count_documents(query)
    documents = db.employees.find(query, {"_id": 0}).sort("emp_code", ASCENDING).skip(offset).limit(page_size)
    return {
        "items": [serialize_employee(document) for document in documents],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    ts = truncate_to_seconds(from_epoch_ms(body.punched_at)) if body.punched_at is not None else truncate_to_seconds(datetime.now(UTC))
    attendance_day = attendance_date(ts, emp["shift_start"], emp["shift_end"])
    doc = {
        "emp_code": body.emp_code,
        "date": attendance_day,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes(ts, emp["shift_start"], attendance_day),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError as error:
        raise HTTPException(409, "already punched in for this date") from error
    return serialize_attendance(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = db.employees.find_one({"emp_code": body.emp_code})
    if employee is None:
        raise HTTPException(404, "employee not found")

    punched_at = (
        truncate_to_seconds(from_epoch_ms(body.punched_at))
        if body.punched_at is not None
        else truncate_to_seconds(datetime.now(UTC))
    )
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": punched_at}},
        sort=[("punch_in", DESCENDING)],
    )
    if record is None:
        raise HTTPException(404, "no punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "record is already punched out")
    if punched_at <= record["punch_in"] or punched_at - record["punch_in"] > timedelta(hours=24):
        raise HTTPException(422, "punched_at must be after punch_in and within 24 hours")

    hours = work_hours(record["punch_in"], punched_at)
    overtime = overtime_minutes(
        punched_at,
        employee["shift_start"],
        employee["shift_end"],
        record["date"],
    )
    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {
            "$set": {
                "punch_out": punched_at,
                "work_hours": hours,
                "overtime_minutes": overtime,
                "half_day": half_day(hours),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "record is already punched out")
    return serialize_attendance(updated)


def _regularized_values(record: dict, employee: dict, body: RegularizeIn) -> dict:
    final_status = body.status if body.status is not None else record["status"]
    supplied_time = "punch_in" in body.model_fields_set or "punch_out" in body.model_fields_set
    if final_status in ("ABSENT", "LEAVE"):
        if supplied_time:
            raise HTTPException(422, "ABSENT and LEAVE cannot include punch times")
        final_punch_in = None
        final_punch_out = None
    else:
        current_punch_in = record.get("punch_in")
        current_punch_out = record.get("punch_out")
        final_punch_in = (
            truncate_to_seconds(from_epoch_ms(body.punch_in))
            if "punch_in" in body.model_fields_set and body.punch_in is not None
            else current_punch_in
        )
        final_punch_out = (
            truncate_to_seconds(from_epoch_ms(body.punch_out))
            if "punch_out" in body.model_fields_set and body.punch_out is not None
            else current_punch_out
        )
        if final_punch_in is None:
            raise HTTPException(422, "presence status requires punch_in")
        if attendance_date(final_punch_in, employee["shift_start"], employee["shift_end"]) != record["date"]:
            raise HTTPException(422, "punch_in must remain on the attendance date")
        if final_punch_out is not None and (
            final_punch_out <= final_punch_in
            or final_punch_out - final_punch_in > timedelta(hours=24)
        ):
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    if final_status in ("ABSENT", "LEAVE"):
        derived = {
            "late_minutes": 0,
            "work_hours": None,
            "overtime_minutes": 0,
            "half_day": False,
        }
    else:
        late = late_minutes(final_punch_in, employee["shift_start"], record["date"])
        if final_punch_out is None:
            derived = {"late_minutes": late, "work_hours": None, "overtime_minutes": 0, "half_day": False}
        else:
            hours = work_hours(final_punch_in, final_punch_out)
            derived = {
                "late_minutes": late,
                "work_hours": hours,
                "overtime_minutes": overtime_minutes(
                    final_punch_out, employee["shift_start"], employee["shift_end"], record["date"]
                ),
                "half_day": half_day(hours),
            }
    return {
        "status": final_status,
        "punch_in": final_punch_in,
        "punch_out": final_punch_out,
        **derived,
    }


def _regularization_changes(record: dict, final_values: dict) -> dict:
    changes = {}
    fields = ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")
    for field_name in fields:
        previous = record.get(field_name)
        current = final_values[field_name]
        if previous != current:
            changes[field_name] = {"from": previous, "to": current}
    return changes


def _compare_and_regularize(record: dict, final_values: dict, history_entry: dict) -> dict | None:
    history = record.get("history") or []
    update_filter = {
        "_id": record["_id"],
        "status": record.get("status"),
        "punch_in": record.get("punch_in"),
        "punch_out": record.get("punch_out"),
        "$expr": {"$eq": [{"$size": {"$ifNull": ["$history", []]}}, len(history)]},
    }
    return db.attendance_logs.find_one_and_update(
        update_filter,
        {"$set": final_values, "$push": {"history": history_entry}},
        return_document=ReturnDocument.AFTER,
    )


def parse_month(month: str) -> date:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("month must use YYYY-MM format")
    return date.fromisoformat(f"{month}-01")


def month_start(month: date) -> str:
    return month.replace(day=1).isoformat()


def month_end(month: date) -> str:
    if month.month == 12:
        next_month = date(month.year + 1, 1, 1)
    else:
        next_month = date(month.year, month.month + 1, 1)
    return (next_month - timedelta(days=1)).isoformat()


def working_days(month: date, joined_on: str) -> int:
    first_day = max(month, date.fromisoformat(joined_on))
    last_day = date.fromisoformat(month_end(month))
    if first_day > last_day:
        return 0
    total = 0
    current_day = first_day
    while current_day <= last_day:
        if current_day.weekday() < 5:
            total += 1
        current_day += timedelta(days=1)
    return total


def _present_weekday_expression(date_field: str, status_field: str, half_day_field: str) -> dict:
    weekday_number = {
        "$dayOfWeek": {"$dateFromString": {"dateString": date_field}}
    }
    is_presence = {"$in": [status_field, list(PRESENCE_STATUSES)]}
    is_weekday = {"$in": [weekday_number, [2, 3, 4, 5, 6]]}
    day_fraction = {"$cond": [{"$ifNull": [half_day_field, False]}, 0.5, 1]}
    return {"$cond": [is_presence, {"$cond": [is_weekday, day_fraction, 0]}, 0]}


def build_employee_monthly_pipeline(emp_code: str, month: date) -> list[dict]:
    start = month_start(month)
    end = month_end(month)
    return [
        {"$match": {"emp_code": emp_code, "date": {"$gte": start, "$lte": end}}},
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": _present_weekday_expression("$date", "$status", "$half_day")
                },
                "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
            }
        },
    ]


def build_department_summary_pipeline(month: date, department: str | None = None) -> list[dict]:
    end = month_end(month)
    start = month_start(month)
    employee_filter = {"joined_on": {"$lte": end}}
    if department is not None:
        employee_filter["department"] = department

    lookup_match = {
        "$match": {
            "$expr": {
                "$and": [
                    {"$eq": ["$emp_code", "$$employee_code"]},
                    {"$gte": ["$date", start]},
                    {"$lte": ["$date", end]},
                ]
            }
        }
    }
    month_logs_path = "$month_logs"
    presence_status = {"$in": [f"{month_logs_path}.status", list(PRESENCE_STATUSES)]}
    has_work_hours = {
        "$ne": [{"$ifNull": [f"{month_logs_path}.work_hours", None]}, None]
    }
    valid_hours_record = {"$and": [presence_status, has_work_hours]}
    return [
        {"$match": employee_filter},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [lookup_match],
                "as": "month_logs",
            }
        },
        {"$unwind": {"path": "$month_logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": {"department": "$department", "emp_code": "$emp_code"},
                "headcount": {"$first": 1},
                "present_days": {
                    "$sum": _present_weekday_expression(
                        "$month_logs.date", "$month_logs.status", "$month_logs.half_day"
                    )
                },
                "work_hours_total": {
                    "$sum": {
                        "$cond": [valid_hours_record, f"{month_logs_path}.work_hours", 0]
                    }
                },
                "work_hours_count": {"$sum": {"$cond": [valid_hours_record, 1, 0]}},
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": [f"{month_logs_path}.late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": [f"{month_logs_path}.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": [f"{month_logs_path}.status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {
                        "$cond": [{"$eq": [f"{month_logs_path}.status", "ON_DUTY"]}, 1, 0]
                    }
                },
            }
        },
        {
            "$group": {
                "_id": "$_id.department",
                "headcount": {"$sum": "$headcount"},
                "present_days": {"$sum": "$present_days"},
                "work_hours_total": {"$sum": "$work_hours_total"},
                "work_hours_count": {"$sum": "$work_hours_count"},
                "late_count": {"$sum": "$late_count"},
                "total_late_minutes": {"$sum": "$total_late_minutes"},
                "leave_count": {"$sum": "$leave_count"},
                "on_duty_count": {"$sum": "$on_duty_count"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {"$gt": ["$work_hours_count", 0]},
                        {"$divide": ["$work_hours_total", "$work_hours_count"]},
                        None,
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1,
            }
        },
        {"$sort": {"department": 1}},
    ]


def _round_half_up(value: int | float, places: str) -> float:
    return float(Decimal(str(value)).quantize(Decimal(places), rounding=ROUND_HALF_UP))


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: date, body: RegularizeIn):
    date_string = date.isoformat()
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "employee not found")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date_string})
    if record is None:
        raise HTTPException(404, "attendance record not found")

    for attempt in range(2):
        final_values = _regularized_values(record, employee, body)
        changes = _regularization_changes(record, final_values)
        requested_fields = body.model_fields_set.intersection({"status", "punch_in", "punch_out"})
        requested_changes = {
            field_name
            for field_name in requested_fields
            if record.get(field_name) != final_values[field_name]
        }
        if not requested_fields or not requested_changes:
            if attempt > 0:
                raise HTTPException(409, "attendance record changed concurrently")
            raise HTTPException(422, "request does not change the attendance record")
        if not changes:
            raise HTTPException(422, "request does not change the attendance record")

        history_entry = {
            "at": truncate_to_seconds(datetime.now(UTC)),
            "by": body.regularized_by,
            "reason": body.reason,
            "changes": changes,
        }
        updated = _compare_and_regularize(record, final_values, history_entry)
        if updated is not None:
            return serialize_attendance(updated)
        if attempt == 0:
            record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date_string})
            if record is None:
                raise HTTPException(404, "attendance record not found")
    raise HTTPException(409, "attendance record changed concurrently")


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")):
    parsed_month = parse_month(month)
    employee = db.employees.find_one({"emp_code": emp_code}, {"_id": 0, "joined_on": 1})
    if employee is None:
        raise HTTPException(404, "employee not found")

    aggregate_result = list(db.attendance_logs.aggregate(build_employee_monthly_pipeline(emp_code, parsed_month)))
    totals = aggregate_result[0] if aggregate_result else {}
    present_days = totals.get("present_days", 0)
    days = working_days(parsed_month, employee["joined_on"])
    attendance_pct = (
        _round_half_up(Decimal(str(present_days)) / Decimal(days) * Decimal(100), "0.01")
        if days
        else None
    )
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": days,
        "present_days": present_days,
        "leave_days": totals.get("leave_days", 0),
        "late_count": totals.get("late_count", 0),
        "total_late_minutes": totals.get("total_late_minutes", 0),
        "total_overtime_minutes": totals.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct,
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"), department: str | None = None
):
    parsed_month = parse_month(month)
    results = db.employees.aggregate(build_department_summary_pipeline(parsed_month, department))
    items = []
    for result in results:
        result["avg_work_hours"] = (
            _round_half_up(result["avg_work_hours"], "0.01")
            if result["avg_work_hours"] is not None
            else None
        )
        items.append(result)
    return {"month": month, "items": items}


def build_late_leaderboard_pipeline(month: date, limit: int = 10, department: str | None = None) -> list[dict]:
    start = month_start(month)
    end = month_end(month)
    pipeline: list[dict] = [
        {"$match": {"date": {"$gte": start, "$lte": end}}},
        {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "employee"}},
        {"$unwind": "$employee"},
        {
            "$group": {
                "_id": "$emp_code",
                "name": {"$first": "$employee.name"},
                "department": {"$first": "$employee.department"},
                "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                "late_count": {
                    "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}
                },
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$setWindowFields": {"sortBy": {"total_late_minutes": -1, "_id": 1}, "output": {"rank": {"$rank": {}}}}},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": 1, "department": 1, "total_late_minutes": 1, "late_count": 1}},
        {"$sort": {"rank": 1, "emp_code": 1}},
    ]
    if department is not None:
        pipeline.insert(3, {"$match": {"employee.department": department}})
    return pipeline


def build_department_trend_pipeline(department: str, from_date: date, to_date: date) -> list[dict]:
    start = from_date.isoformat()
    end = to_date.isoformat()
    date_range = [from_date + timedelta(days=offset) for offset in range((to_date - from_date).days + 1)]
    return [
        {"$documents": [{"date": day.isoformat()} for day in date_range]},
        {
            "$lookup": {
                "from": "employees",
                "let": {"day": "$date"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$joined_on", "$$day"]},
                                ]
                            }
                        }
                    },
                    {"$count": "headcount"},
                ],
                "as": "headcount_docs",
            }
        },
        {"$addFields": {"headcount": {"$ifNull": [{"$first": "$headcount_docs.headcount"}, 0]}}},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"day": "$date"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$date", "$$day"]}}},
                    {
                        "$lookup": {
                            "from": "employees",
                            "localField": "emp_code",
                            "foreignField": "emp_code",
                            "as": "employee",
                        }
                    },
                    {"$unwind": "$employee"},
                    {"$match": {"employee.department": department}},
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$in": ["$status", list(PRESENCE_STATUSES)]},
                                        {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]},
                                        0,
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]
                                }
                            },
                        }
                    },
                    {"$project": {"_id": 0, "present_count": 1, "late_count": 1}},
                ],
                "as": "daily_logs",
            }
        },
        {
            "$addFields": {
                "present_count": {"$ifNull": [{"$first": "$daily_logs.present_count"}, 0]},
                "late_count": {"$ifNull": [{"$first": "$daily_logs.late_count"}, 0]},
            }
        },
        {
            "$addFields": {
                "is_working_day": {
                    "$in": [
                        {"$dayOfWeek": {"$dateFromString": {"dateString": {"$concat": ["$date", "T00:00:00Z"]}}}},
                        [2, 3, 4, 5, 6],
                    ]
                }
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {"$and": [{"$eq": ["$is_working_day", True]}, {"$gt": ["$headcount", 0]}]},
                        {"$divide": ["$present_count", "$headcount"]},
                        None,
                    ]
                }
            }
        },
        {"$sort": {"date": 1}},
        {
            "$setWindowFields": {
                "sortBy": {"date": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": {
                            "$cond": [{"$ne": ["$attendance_rate", None]}, "$attendance_rate", "$$REMOVE"]
                        },
                        "window": {"documents": [-6, 0]},
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": 1,
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1,
            }
        },
    ]


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(default=10, ge=1, le=50),
    department: str | None = None,
):
    parsed_month = parse_month(month)
    results = list(db.attendance_logs.aggregate(build_late_leaderboard_pipeline(parsed_month, limit, department)))
    return {"month": month, "items": results}


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_: date = Query(alias="from", default=...),
    to: date = Query(...),
):
    if to < from_:
        raise HTTPException(422, "to must be on or after from")
    if (to - from_).days + 1 > 92:
        raise HTTPException(422, "date range must not exceed 92 days")
    if db.employees.find_one({"department": department}) is None:
        raise HTTPException(404, "department not found")

    results = list(db.employees.aggregate(build_department_trend_pipeline(department, from_, to)))
    formatted = []
    for row in results:
        row["attendance_rate"] = (
            _round_half_up(Decimal(str(row["attendance_rate"])), "0.0001")
            if row.get("attendance_rate") is not None
            else None
        )
        row["moving_avg_7d"] = (
            _round_half_up(Decimal(str(row["moving_avg_7d"])), "0.0001")
            if row.get("moving_avg_7d") is not None
            else None
        )
        formatted.append(row)
    return {"department": department, "items": formatted}


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: str | None = None,
    month: str | None = None,
    department: str | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: date | None = None,
    date_to: date | None = None,
    status: Status | None = None,
    from_: date | None = Query(default=None, alias="from"),
    to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if endpoint not in {"attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"}:
        raise HTTPException(422, "unexpected endpoint")

    if endpoint == "attendance_list":
        query = {}
        if emp_code is not None:
            query["emp_code"] = emp_code
        if date_from is not None:
            query["date"] = {"$gte": date_from.isoformat()}
        if date_to is not None:
            query.setdefault("date", {})["$lte"] = date_to.isoformat()
        if status is not None:
            query["status"] = status
        collection = db.attendance_logs
        explain = collection.find(query).sort([("date", -1), ("emp_code", 1)]).skip((page - 1) * page_size).limit(page_size).explain("executionStats")
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    if endpoint == "employee_monthly":
        if emp_code is None or month is None:
            raise HTTPException(422, "emp_code and month are required for employee_monthly")
        pipeline = build_employee_monthly_pipeline(emp_code, parse_month(month))
        explain = db.attendance_logs.explain("executionStats").aggregate(pipeline)
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    if endpoint == "department_summary":
        if month is None:
            raise HTTPException(422, "month is required for department_summary")
        pipeline = build_department_summary_pipeline(parse_month(month), department)
        explain = db.employees.explain("executionStats").aggregate(pipeline)
        return {"endpoint": endpoint, "collection": "employees", "explain": explain}

    if endpoint == "late_leaderboard":
        if month is None:
            raise HTTPException(422, "month is required for late_leaderboard")
        pipeline = build_late_leaderboard_pipeline(parse_month(month), limit, department)
        explain = db.attendance_logs.explain("executionStats").aggregate(pipeline)
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain}

    if from_ is None or to is None:
        raise HTTPException(422, "from and to are required for department_trend")
    if to < from_:
        raise HTTPException(422, "to must be on or after from")
    if department is None:
        raise HTTPException(422, "department is required for department_trend")
    pipeline = build_department_trend_pipeline(department, from_, to)
    explain = db.employees.explain("executionStats").aggregate(pipeline)
    return {"endpoint": endpoint, "collection": "employees", "explain": explain}


@app.get("/attendance")
def list_attendance(
    emp_code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: Status | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")

    query = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if date_from is not None or date_to is not None:
        query["date"] = {}
        if date_from is not None:
            query["date"]["$gte"] = date_from.isoformat()
        if date_to is not None:
            query["date"]["$lte"] = date_to.isoformat()
    if status is not None:
        query["status"] = status

    offset = (page - 1) * page_size
    total = db.attendance_logs.count_documents(query)
    documents = (
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip(offset)
        .limit(page_size)
    )
    return {
        "items": [serialize_attendance(document) for document in documents],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
