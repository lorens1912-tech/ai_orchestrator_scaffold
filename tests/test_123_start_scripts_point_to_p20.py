from pathlib import Path

def test_run_server_ps1_points_to_p20_main():
    text = Path("run_server.ps1").read_text(encoding="utf-8")
    assert "uvicorn app.main:app" in text
    assert "--port 8001" in text

def test_run_server_agent_ps1_points_to_p20_main():
    text = Path("run_server_agent.ps1").read_text(encoding="utf-8")
    assert "uvicorn app.main:app" in text
    assert "--port 8001" in text

def test_run_bat_points_to_p20_main():
    text = Path("RUN.bat").read_text(encoding="ascii")
    assert "uvicorn app.main:app" in text
    assert "--port 8001" in text
