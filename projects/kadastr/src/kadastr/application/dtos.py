"""Транспортабельные DTO и конверт домена в API-ответ.

Формат ответа стабилен для REST/JSON и браузерного UI:
  { "ok": true|false, "data": ..., "error": {...}|null, "meta": {...} }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain.entities import AnalysisRun, CadastralObject
from ..domain.glossary import Glossary
from ..domain.value_objects import Severity


API_VERSION = "1.0"


@dataclass
class Envelope:
    ok: bool
    data: Any = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        meta = {"api_version": API_VERSION, **self.meta}
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "meta": meta,
        }


def ok(data: Any, **meta: Any) -> Envelope:
    return Envelope(ok=True, data=data, error=None, meta=meta)


def fail(code: str, message: str, **details: Any) -> Envelope:
    return Envelope(
        ok=False,
        data=None,
        error={"code": code, "message": message, **details},
        meta={},
    )


def run_to_api(run: AnalysisRun) -> dict[str, Any]:
    return run.to_dict()


def glossary_to_api(g: Glossary) -> dict[str, Any]:
    return g.to_dict()


def object_summary(obj: CadastralObject) -> dict[str, Any]:
    """Короткая сводка для таблицы/UI."""
    return {
        "cn": obj.number.value,
        "found": obj.found,
        "kind": obj.kind.value,
        "kind_title": obj.kind.title_ru,
        "status": obj.status,
        "address": obj.address,
        "max_severity": obj.max_severity().value,
        "counts": obj.counts(),
        "top_problems": [
            p.to_dict()
            for p in sorted(obj.problems, key=lambda p: p.severity.rank)
            if p.severity in (Severity.CRITICAL, Severity.HIGH)
        ][:5],
    }
