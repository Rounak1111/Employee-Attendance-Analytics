# Run tests locally (PowerShell)

Run these four commands from the repository root:

```powershell
docker run --name attendance-mongo -p 27017:27017 -d mongo:7
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q -x
```