"""Порт и реализация кэша объектов (заменяем на PG без правки use cases)."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ObjectCacheRepository(Protocol):
    def get(self, cn: str) -> dict[str, Any] | None:
        """Свежий снапшот или None (нет / протух)."""

    def put(self, cn: str, snapshot: dict[str, Any]) -> None: ...

    def invalidate(self, cn: str) -> None: ...


class JsonCacheRepository:
    """Постоянный кэш в JSON. TTL по умолчанию 24 часа."""

    def __init__(self, path: Path | str, ttl_seconds: int = 86400):
        self.path = Path(path)
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data.get("items") if isinstance(data, dict) else {}

    def _write_all(self, items: dict[str, Any]) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "items": items}, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, cn: str) -> dict[str, Any] | None:
        with self._lock:
            items = self._read_all()
            rec = items.get(cn)
            if not rec:
                return None
            saved = rec.get("saved_at")
            try:
                age = dt.datetime.now() - dt.datetime.fromisoformat(saved)
            except Exception:
                return None
            if age.total_seconds() > self.ttl:
                return None
            return rec.get("snapshot")

    def put(self, cn: str, snapshot: dict[str, Any]) -> None:
        with self._lock:
            items = self._read_all()
            items[cn] = {
                "saved_at": dt.datetime.now().isoformat(timespec="seconds"),
                "snapshot": snapshot,
            }
            self._write_all(items)

    def invalidate(self, cn: str) -> None:
        with self._lock:
            items = self._read_all()
            if cn in items:
                del items[cn]
                self._write_all(items)
