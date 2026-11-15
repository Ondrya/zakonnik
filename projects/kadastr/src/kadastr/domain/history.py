"""История поиска: VO и порт хранилища."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .value_objects import CadastralNumber


@dataclass(frozen=True, slots=True)
class HistoryItem:
    """Запись истории: кадастровый номер + человеческое имя (опционально)."""

    number: CadastralNumber
    name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_used_at: str | None = None
    runs: int = 0

    @property
    def title(self) -> str:
        return (self.name or "").strip() or self.number.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "cn": self.number.value,
            "name": self.name,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "runs": self.runs,
        }


@runtime_checkable
class HistoryRepository(Protocol):
    def list_items(self) -> list[HistoryItem]: ...

    def upsert(self, item: HistoryItem) -> HistoryItem:
        """Добавить или обновить (по КН)."""
        ...

    def rename(self, cn: CadastralNumber, name: str | None) -> HistoryItem | None: ...

    def delete(self, cn: CadastralNumber) -> bool: ...

    def touch(self, cn: CadastralNumber) -> HistoryItem | None:
        """Отметить использование (last_used_at, runs+1), создать если нет."""
        ...
