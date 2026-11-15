"""Value Objects домена."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    INFO = "info"
    OK = "ok"

    @property
    def rank(self) -> int:
        return {
            Severity.CRITICAL: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.INFO: 3,
            Severity.OK: 4,
        }[self]

    @classmethod
    def parse(cls, value: str | "Severity") -> "Severity":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            return Severity.INFO


class ObjectKind(str, Enum):
    LAND_PLOT = "landplot"
    BUILDING = "building"
    CONSTRUCTION = "construction"
    UNFINISHED = "unfinished"
    QUARTER = "quarter"
    OTHER = "other"

    @property
    def title_ru(self) -> str:
        return {
            ObjectKind.LAND_PLOT: "Земельный участок",
            ObjectKind.BUILDING: "Здание (ОКС)",
            ObjectKind.CONSTRUCTION: "Сооружение",
            ObjectKind.UNFINISHED: "Объект незавершённого строительства",
            ObjectKind.QUARTER: "Кадастровый квартал",
            ObjectKind.OTHER: "Объект",
        }[self]


class RelatedKind(str, Enum):
    ZU = "zu"
    OKS = "oks"
    ZOUIT = "zouit"
    RED_LINE = "red_line"
    TERR_ZONE = "terr_zone"
    OOPT = "oopt"
    FLOOD = "flood"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class CadastralNumber:
    """Кадастровый номер. Нормализуется: пробелы срезаются, разделитель — двоеточие."""

    value: str

    _RE = re.compile(r"^\d{2}:\d{2}:\d{6,7}:\d+$")
    _RE_QUARTER = re.compile(r"^\d{2}:\d{2}:\d{6,7}$")

    def __post_init__(self) -> None:
        raw = (self.value or "").strip().replace(" ", "")
        if not self._RE.match(raw) and not self._RE_QUARTER.match(raw):
            raise ValueError(
                f"Некорректный кадастровый номер: {self.value!r}. "
                "Ожидается формат XX:XX:XXXXXX(XXX):NNN или квартал XX:XX:XXXXXX"
            )
        object.__setattr__(self, "value", raw)

    def __str__(self) -> str:
        return self.value

    @property
    def is_quarter(self) -> bool:
        return bool(self._RE_QUARTER.match(self.value))

    @property
    def quarter(self) -> str | None:
        parts = self.value.split(":")
        if len(parts) >= 3:
            return ":".join(parts[:3])
        return None


@dataclass(frozen=True, slots=True)
class Problem:
    """Проблема (риск) по объекту. Immutable VO."""

    code: str
    severity: Severity
    title: str
    detail: str
    legal_note: str = ""
    recommendation: str = ""
    source: str = "ПКК/НСПД"
    glossary: tuple[str, ...] = ()  # коды терминов справочника

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "legal_note": self.legal_note,
            "recommendation": self.recommendation,
            "source": self.source,
            "glossary": list(self.glossary),
        }


@dataclass(frozen=True, slots=True)
class GeoGeometry:
    """Геометрия в WGS84 (EPSG:4326). Сырой GeoJSON — только для карты/выгрузки."""

    geojson: dict[str, Any]
    centroid_lon: float | None = None
    centroid_lat: float | None = None

    @property
    def has_shape(self) -> bool:
        return bool(self.geojson) and self.geojson.get("type") in (
            "Point",
            "MultiPoint",
            "LineString",
            "MultiLineString",
            "Polygon",
            "MultiPolygon",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "geojson": self.geojson,
            "centroid": (
                [self.centroid_lon, self.centroid_lat]
                if self.centroid_lon is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class RelatedFeature:
    """Смежный/пересекающий объект или зона."""

    kind: RelatedKind
    cad_number: str | None
    category_name: str | None
    title: str | None
    options: dict[str, Any]
    geometry: GeoGeometry | None
    overlap: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "cad_number": self.cad_number,
            "category_name": self.category_name,
            "title": self.title,
            "options": self.options,
            "geometry": self.geometry.to_dict() if self.geometry else None,
            "overlap": self.overlap,
        }
