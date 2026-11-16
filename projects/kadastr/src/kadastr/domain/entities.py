"""Агрегат «Объект недвижимости» и коллекция результатов анализа."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .value_objects import (
    CadastralNumber,
    GeoGeometry,
    ObjectKind,
    Problem,
    RelatedFeature,
    Severity,
)


@dataclass
class CadastralObject:
    """Агрегат: объект недвижимости с атрибутами ПКК, проблемами и связями.

    Инварианты:
      - number валиден;
      - problems не дублируются по code+title;
      - linked_plot заполняется для ОКС, если известен родительский ЗУ.
    """

    number: CadastralNumber
    kind: ObjectKind = ObjectKind.OTHER
    category_id: int | None = None
    category_name: str | None = None
    status: str | None = None
    address: str | None = None
    area: float | None = None
    ownership: str | None = None
    cost_value: float | None = None
    cost_index: float | None = None
    cost_cause: str | None = None
    vri: str | None = None
    land_category: str | None = None
    reg_date: str | None = None
    purpose: str | None = None
    floors: str | None = None
    year_built: str | None = None
    materials: str | None = None
    display_name: str | None = None
    quarter_number: str | None = None
    has_geometry: bool = False
    geometry: GeoGeometry | None = None
    raw_options: dict[str, Any] = field(default_factory=dict)
    tabs: dict[str, list[str]] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    related: list[RelatedFeature] = field(default_factory=list)
    parent_plot_number: CadastralNumber | None = None
    linked_plot: "CadastralObject | None" = None
    found: bool = False
    error: str | None = None
    source_id: int | str | None = None

    def apply_problem(self, problem: Problem) -> None:
        if any(p.code == problem.code and p.title == problem.title for p in self.problems):
            return
        self.problems.append(problem)

    def max_severity(self) -> Severity:
        if not self.problems:
            return Severity.OK
        return min((p.severity for p in self.problems), key=lambda s: s.rank)

    def counts(self) -> dict[str, int]:
        out = {"critical": 0, "high": 0, "medium": 0, "info": 0, "ok": 0}
        for p in self.problems:
            out[p.severity.value] = out.get(p.severity.value, 0) + 1
        return out

    def related_by_kind(self, *kinds: str) -> list[RelatedFeature]:
        want = set(kinds)
        return [r for r in self.related if r.kind.value in want]

    def other_plots(self) -> list[RelatedFeature]:
        return [
            r
            for r in self.related
            if r.kind.value == "zu" and r.cad_number and r.cad_number != self.number.value
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_cn": self.number.value,
            "found": self.found,
            "error": self.error,
            "cad_number": self.number.value,
            "kind": self.kind.value,
            "kind_title": self.kind.title_ru,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "status": self.status,
            "address": self.address,
            "area": self.area,
            "ownership": self.ownership,
            "cost_value": self.cost_value,
            "cost_index": self.cost_index,
            "cost_cause": self.cost_cause,
            "vri": self.vri,
            "land_category": self.land_category,
            "reg_date": self.reg_date,
            "purpose": self.purpose,
            "floors": self.floors,
            "year_built": self.year_built,
            "materials": self.materials,
            "name": self.display_name,
            "quarter_cn": self.quarter_number,
            "has_geometry": self.has_geometry,
            "geometry": self.geometry.to_dict() if self.geometry else None,
            "raw_options": self.raw_options,
            "problems": [p.to_dict() for p in self.problems],
            "related": [r.to_dict() for r in self.related],
            "tabs": self.tabs,
            "parent_zu": self.parent_plot_number.value if self.parent_plot_number else None,
            "linked_zu": self.linked_plot.to_public_dict() if self.linked_plot else None,
            "max_severity": self.max_severity().value,
            "counts": self.counts(),
            "nspd_id": self.source_id,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Сокращённая форма для вложенного связанного ЗУ (без рекурсии)."""
        return {
            "cad_number": self.number.value,
            "kind": self.kind.value,
            "kind_title": self.kind.title_ru,
            "status": self.status,
            "address": self.address,
            "area": self.area,
            "vri": self.vri,
            "land_category": self.land_category,
            "cost_value": self.cost_value,
            "has_geometry": self.has_geometry,
            "geometry": self.geometry.to_dict() if self.geometry else None,
            "problems": [p.to_dict() for p in self.problems],
            "related": [r.to_dict() for r in self.related],
            "max_severity": self.max_severity().value,
            "counts": self.counts(),
        }


@dataclass
class AnalysisRun:
    """Результат одного запуска анализа по списку номеров."""

    created_at: str
    objects: list[CadastralObject] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cached_objects: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "warnings": self.warnings,
            "objects": [o.to_dict() for o in self.objects],
            "cached_objects": self.cached_objects,
        }
