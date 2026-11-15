"""Справочник юридических терминов: модель и коллекция в памяти.

JSON-файл — в data/glossary.json (загружает infrastructure).
Домен не знает про файлы.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class GlossaryTerm:
    code: str
    title: str
    for_citizen: str
    for_lawyer: str
    regulations: tuple[str, ...] = ()
    related_problems: tuple[str, ...] = ()
    short: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "short": self.short or self.title,
            "for_citizen": self.for_citizen,
            "for_lawyer": self.for_lawyer,
            "regulations": list(self.regulations),
            "related_problems": list(self.related_problems),
        }


@dataclass
class Glossary:
    terms: list[GlossaryTerm] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._index: dict[str, GlossaryTerm] = {t.code: t for t in self.terms}

    def get(self, code: str) -> GlossaryTerm | None:
        return self._index.get(code)

    def all(self) -> list[GlossaryTerm]:
        return list(self.terms)

    def to_dict(self) -> dict[str, Any]:
        return {"count": len(self.terms), "terms": [t.to_dict() for t in self.terms]}

    @classmethod
    def from_list(cls, items: list[dict]) -> "Glossary":
        terms = [
            GlossaryTerm(
                code=i["code"],
                title=i["title"],
                for_citizen=i.get("for_citizen", ""),
                for_lawyer=i.get("for_lawyer", ""),
                regulations=tuple(i.get("regulations") or ()),
                related_problems=tuple(i.get("related_problems") or ()),
                short=i.get("short", ""),
            )
            for i in items
        ]
        return cls(terms=terms)
