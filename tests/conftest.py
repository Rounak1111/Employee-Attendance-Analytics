import json
import os
from pathlib import Path

import pytest
from bson import json_util
from bson.json_util import JSONOptions
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError


load_dotenv(override=False)
TEST_DATABASE = os.getenv("MONGO_DB_TEST", "attendance_test")
os.environ["MONGO_DB"] = TEST_DATABASE


@pytest.fixture(scope="session")
def mongo_database():
    mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    mongo_client = MongoClient(
        mongo_uri,
        serverSelectionTimeoutMS=2000,
        tz_aware=True,
    )
    try:
        mongo_client.admin.command("ping")
    except PyMongoError as error:
        mongo_client.close()
        pytest.skip(f"MongoDB unavailable at {mongo_uri}: {error}")

    mongo_client.drop_database(TEST_DATABASE)
    database = mongo_client[TEST_DATABASE]
    sample_data = Path(__file__).resolve().parents[1] / "sample_data"
    json_options = JSONOptions(tz_aware=True)

    for collection_name in ("employees", "attendance_logs"):
        sample_path = sample_data / f"{collection_name}.json"
        documents = json_util.loads(sample_path.read_text(encoding="utf-8"), json_options=json_options)
        if documents:
            database[collection_name].insert_many(documents)

    yield database
    mongo_client.close()


@pytest.fixture(scope="session")
def client(mongo_database):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client