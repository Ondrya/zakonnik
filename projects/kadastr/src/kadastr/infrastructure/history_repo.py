"""JSON-хранилище истории поиска. Потокобезопасно для ThreadingHTTPServer."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import threading
from pathlib import Path

from ..domain.history import HistoryItem
from ..domain.value_objects import CadastralNumber


class JsonHistoryRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[HistoryItem]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = data.get("items") if isinstance(data, dict) else data
        out: list[HistoryItem] = []
        for raw in items or []:
            try:
                out.append(
                    HistoryItem(
                        number=CadastralNumber(raw["cn"]),
                        name=raw.get("name") or None,
                        created_at=raw.get("created_at"),
                        updated_at=raw.get("updated_at"),
                        last_used_at=raw.get("last_used_at"),
                        runs=int(raw.get("runs") or 0),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def _write(self, items: list[HistoryItem]) -> None:
        payload = {
            "version": 1,
            "items": [i.to_dict() for i in items],
        }
        # атомарная замена
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _now() -> str:
        return dt.datetime.now().isoformat(timespec="seconds")

    def list_items(self) -> list[HistoryItem]:
        with self._lock:
            items = self._read()
        # свежие использованные сверху
        return sorted(
            items,
            key=lambda i: (i.last_used_at or i.created_at or "", i.runs),
            reverse=True,
        )

    def upsert(self, item: HistoryItem) -> HistoryItem:
        with self._lock:
            items = self._read()
            now = self._now()
            for i, existing in enumerate(items):
                if existing.number.value == item.number.value:
                    merged = HistoryItem(
                        number=existing.number,
                        name=item.name if item.name is not None else existing.name,
                        created_at=existing.created_at or now,
                        updated_at=now,
                        last_used_at=now,
                        runs=max(existing.runs, item.runs) + 1 if item.runs else existing.runs + 1,
                    )
                    items[i] = merged
                    self._write(items)
                    return merged
            created = HistoryItem(
                number=item.number,
                name=item.name,
                created_at=now,
                updated_at=now,
                last_used_at=now,
                runs=max(1, item.runs),
            )
            items.append(created)
            self._write(items)
            return created

    def rename(self, cn: CadastralNumber, name: str | None) -> HistoryItem | None:
        with self._lock:
            items = self._read()
            for i, existing in enumerate(items):
                if existing.number.value == cn.value:
                    updated = HistoryItem(
                        number=existing.number,
                        name=(name or "").strip() or None,
                        created_at=existing.created_at,
                        updated_at=self._now(),
                        last_used_at=existing.last_used_at,
                        runs=existing.runs,
                    )
                    items[i] = updated
                    self._write(items)
                    return updated
            return None

    def delete(self, cn: CadastralNumber) -> bool:
        with self._lock:
            items = self._read()
            keep = [i for i in items if i.number.value != cn.value]
            if len(keep) == len(items):
                return False
            self._write(keep)
            return True

    def touch(self, cn: CadastralNumber) -> HistoryItem | None:
        return self.upsert(HistoryItem(number=cn))
