"""Порты домена. Реализации — в infrastructure.

Порт не знает про НСПД. Если парсер развалится, пишем новый адаптер
CadastralGateway, домен и use cases не трогаем.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .entities import CadastralObject
from .value_objects import CadastralNumber, RelatedFeature


@runtime_checkable
class CadastralGateway(Protocol):
    """Внешний источник кадастровых сведений (ПКК, ЕГРН-шлюз, мок, файл)."""

    def find_object(self, cn: CadastralNumber) -> CadastralObject | None:
        """Найти объект по КН, заполнив основные атрибуты (без related)."""
        ...

    def find_linked_plot_number(self, obj: CadastralObject) -> CadastralNumber | None:
        """КН земельного участка, на котором стоит ОКС (если есть)."""
        ...

    def load_tabs(self, obj: CadastralObject) -> dict[str, list[str]]:
        """Вкладки карточки (ВРИ, части, связанные ЗУ). Опционально."""
        ...

    def load_intersecting(self, obj: CadastralObject) -> list[RelatedFeature]:
        """Зоны, красные линии, смежные ЗУ, ОКС в границах."""
        ...

    def close(self) -> None:
        """Освобождение ресурсов (сессии, куки)."""
        ...


@runtime_checkable
class ObjectCacheRepository(Protocol):
    def get(self, cn: str) -> dict | None: ...

    def put(self, cn: str, snapshot: dict) -> None: ...

    def invalidate(self, cn: str) -> None: ...


@runtime_checkable
class GlossaryRepository(Protocol):
    def load(self) -> list[dict]:
        """Список терминов: code, title, for_citizen, for_lawyer, regulations, related_problems."""
        ...
