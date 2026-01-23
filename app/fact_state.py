from dataclasses import dataclass
from pathlib import Path
import json, time

@dataclass
class FactState:
    last_ok: bool | None = None
    last_ts: float | None = None
    source: str | None = None
    checked_chars: int | None = None

    @staticmethod
    def path(book_dir: Path) -> Path:
        return book_dir / "fact_state.json"

    @classmethod
    def load(cls, book_dir: Path):
        p = cls.path(book_dir)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(**data)

    def save(self, book_dir: Path):
        self.path(book_dir).write_text(
            json.dumps(self.__dict__, indent=2),
            encoding="utf-8"
        )

def update_fact_state(book_dir: Path, ok: bool, source: str, checked_chars: int | None = None):
    st = FactState.load(book_dir)
    st.last_ok = ok
    st.last_ts = time.time()
    st.source = source
    st.checked_chars = checked_chars
    st.save(book_dir)
