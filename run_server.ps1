$env:EXPECTED_MODES_COUNT="14"
$env:WRITE_MODEL_FORCE="gpt-4.1-mini"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
