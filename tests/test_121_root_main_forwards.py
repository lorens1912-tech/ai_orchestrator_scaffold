from pathlib import Path

def test_root_main_forwards_to_app_main():
    text = Path("main.py").read_text(encoding="utf-8")
    assert "from app.main import app" in text
    assert "__all__ = [\"app\"]" in text
