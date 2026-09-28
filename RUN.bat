@echo off
cd /d "%~dp0"
set "AGENT_TEST_MODE=0"
set "PYTEST_FASTPATH="
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001