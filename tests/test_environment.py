def test_sample_database_is_seeded(mongo_database):
    assert mongo_database.employees.count_documents({}) > 0
    assert mongo_database.attendance_logs.count_documents({}) > 0