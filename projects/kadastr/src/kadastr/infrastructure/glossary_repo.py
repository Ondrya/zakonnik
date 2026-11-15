"""JSON-репозиторий справочника (файл data/glossary.json)."""
from __future__ import annotations

import json
from pathlib import Path


class JsonGlossaryRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data.get("terms") or []
        if isinstance(data, list):
            return data
        return []
