# Data model

This project stores employee and attendance records in MongoDB collections named `employees` and `attendance_logs`.

## employees
- `emp_code`: string, unique employee identifier
- `name`: string
- `email`: string
- `department`: string
- `shift_start`: IST time string like `09:30`
- `shift_end`: IST time string like `18:30`
- `joined_on`: ISO date string (YYYY-MM-DD)
- `created_at`: UTC datetime

## attendance_logs
- `emp_code`: string
- `date`: attendance day (YYYY-MM-DD)
- `status`: one of `PRESENT`, `WFH`, `ON_DUTY`, `ABSENT`, `LEAVE`
- `punch_in`: UTC datetime or null
- `punch_out`: UTC datetime or null
- `work_hours`: decimal hours or null
- `late_minutes`: int
- `overtime_minutes`: int
- `half_day`: bool
- `history`: array of audit entries

This is the canonical persisted shape used by the API and aggregation pipelines.
