"""Антикоррупционный слой: перевод сырых структур НСПД в доменные модели.

Правила:
  - домен не видит поля вида build_record_area / land_record_category_type;
  - ACL единственный знает словарь НСПД;
  - смена формата НСПД чинится здесь, а не в use cases.
"""
from __future__ import annotations

from typing import Any

from ...domain.entities import CadastralObject
from ...domain.geometry import centroid_of, to_wgs84
from ...domain.value_objects import (
    CadastralNumber,
    GeoGeometry,
    ObjectKind,
    RelatedFeature,
    RelatedKind,
)


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cad_of(feat: dict) -> str:
    props = feat.get("properties") or {}
    opts = props.get("options") or {}
    for key in ("cad_num", "cadNum", "cadastral_number"):
        v = opts.get(key) or props.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return str(props.get("label") or props.get("descr") or "")


def classify_kind(props: dict, opts: dict) -> ObjectKind:
    cat = props.get("category")
    cat_name = (props.get("categoryName") or "").lower()
    type_val = (
        opts.get("land_record_type") or opts.get("build_record_type_value") or ""
    ).lower()
    if cat == 36368 or "земельн" in cat_name or "участок" in type_val:
        return ObjectKind.LAND_PLOT
    if cat == 36369 or "здани" in cat_name or type_val == "здание":
        return ObjectKind.BUILDING
    if "сооружен" in cat_name:
        return ObjectKind.CONSTRUCTION
    if "незаверш" in cat_name:
        return ObjectKind.UNFINISHED
    if "квартал" in cat_name or "кадастровое деление" in cat_name:
        return ObjectKind.QUARTER
    return ObjectKind.OTHER


def classify_related(cat, cat_name, opts: dict) -> RelatedKind:
    try:
        cat_i = int(cat) if cat is not None else None
    except (TypeError, ValueError):
        cat_i = None
    name = (cat_name or "").lower()
    blob = " ".join(str(v) for v in (opts or {}).values()).lower()
    if cat_i == 36368 or "земельн" in name:
        return RelatedKind.ZU
    if cat_i == 36369 or name in ("здания", "сооружения") or "здани" in name:
        return RelatedKind.OKS
    if cat_i == 36940 or "зоуит" in name or "особые условия" in name:
        return RelatedKind.ZOUIT
    if cat_i == 38942 or "красн" in name:
        return RelatedKind.RED_LINE
    if cat_i == 472819 or "территориальн" in name:
        return RelatedKind.TERR_ZONE
    if cat_i == 472825 or "оопт" in name or "охраняемые природные" in name:
        return RelatedKind.OOPT
    if cat_i == 38967 or "затопл" in name or "подтопл" in name:
        return RelatedKind.FLOOD
    if "зоуит" in blob or "охранная зона" in blob or "зона регулирования" in blob:
        return RelatedKind.ZOUIT
    return RelatedKind.OTHER


def _geometry_from_feat(feat: dict) -> GeoGeometry | None:
    geom = feat.get("geometry")
    if not geom or geom.get("type") in (None, "GeometryCollection"):
        return None
    try:
        wgs = to_wgs84(geom)
        c = centroid_of(wgs)
        return GeoGeometry(
            geojson=wgs,
            centroid_lon=round(c[0], 7) if c else None,
            centroid_lat=round(c[1], 7) if c else None,
        )
    except Exception:
        return None


class NspdAcl:
    """Переводчик НСПД → домен. Без сети."""

    def feature_to_object(self, feat: dict, *, found: bool = True) -> CadastralObject:
        props = feat.get("properties") or {}
        opts = props.get("options") or {}
        cn_raw = _cad_of(feat) or str(props.get("label") or "")
        try:
            number = CadastralNumber(cn_raw)
        except ValueError:
            # нестандартный label (зона и т.п.) — не роняем сборку агрегата
            number = CadastralNumber.__new__(CadastralNumber)
            object.__setattr__(number, "value", cn_raw or "unknown")

        kind = classify_kind(props, opts)
        obj = CadastralObject(
            number=number,
            kind=kind,
            category_id=props.get("category"),
            category_name=props.get("categoryName"),
            status=opts.get("status"),
            address=opts.get("readable_address"),
            ownership=opts.get("ownership_type"),
            cost_value=_num(opts.get("cost_value")),
            cost_index=_num(opts.get("cost_index")),
            cost_cause=opts.get("determination_couse"),
            quarter_number=opts.get("quarter_cad_number"),
            display_name=opts.get("building_name") or opts.get("name"),
            raw_options=opts,
            source_id=feat.get("id"),
            found=found,
        )
        if isinstance(obj.reg_date, list):
            obj.reg_date = None
        obj.reg_date = (
            opts.get("land_record_reg_date") or opts.get("build_record_registration_date")
        )

        if kind is ObjectKind.LAND_PLOT:
            obj.area = _num(
                opts.get("specified_area") or opts.get("area") or opts.get("land_record_area")
            )
            obj.vri = opts.get("permitted_use_established_by_document") or opts.get(
                "permitted_use_name"
            )
            obj.land_category = opts.get("land_record_category_type")
        elif kind is ObjectKind.BUILDING:
            obj.area = _num(opts.get("build_record_area"))
            obj.purpose = opts.get("purpose") or opts.get("permitted_use_name")
            obj.floors = opts.get("floors")
            obj.year_built = opts.get("year_built")
            obj.materials = opts.get("materials")
            obj.vri = opts.get("permitted_use_name")
        else:
            obj.area = _num(opts.get("build_record_area"))
            obj.vri = opts.get("permitted_use_established_by_document")

        geo = _geometry_from_feat(feat)
        if geo:
            obj.geometry = geo
            obj.has_geometry = geo.has_shape
        return obj

    def feature_to_related(self, feat: dict, *, overlap: bool = False) -> RelatedFeature | None:
        props = feat.get("properties") or {}
        opts = props.get("options") or {}
        kind = classify_related(props.get("category"), props.get("categoryName"), opts)
        cad = opts.get("cad_num") or props.get("label")
        title = (
            opts.get("name_by_doc")
            or opts.get("name_zone")
            or opts.get("building_name")
            or props.get("label")
        )
        geo = _geometry_from_feat(feat)
        return RelatedFeature(
            kind=kind,
            cad_number=cad,
            category_name=props.get("categoryName"),
            title=title,
            options=opts,
            geometry=geo,
            overlap=overlap,
        )

    @staticmethod
    def extract_cns(values: list | None) -> list[str]:
        """Достаёт кадастровые номера из значений вкладок."""
        import re

        out: list[str] = []
        for v in values or []:
            for m in re.findall(r"\d{2}:\d{2}:\d{6,7}:\d+", str(v)):
                if m not in out:
                    out.append(m)
        return out
