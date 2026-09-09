@echo off
cd /d C:\AI\ai_orchestrator_scaffold
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
