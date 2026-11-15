"""HTML-отчёт (карта Leaflet + карточки проблем). Инфраструктура представления."""
from __future__ import annotations

import html
import json
from pathlib import Path

from ...application.dtos import object_summary
from ...domain.entities import AnalysisRun, CadastralObject
from ...domain.value_objects import Severity

SEV_COLOR = {
    "critical": "#dc2626",
    "high": "#ea580c",
    "medium": "#ca8a04",
    "info": "#2563eb",
    "ok": "#16a34a",
}
SEV_BG = {
    "critical": "#fee2e2",
    "high": "#ffedd5",
    "medium": "#fef9c3",
    "info": "#dbeafe",
    "ok": "#dcfce7",
}
SEV_LABEL = {
    "critical": "Критично",
    "high": "Существенно",
    "medium": "Средне",
    "info": "Инфо",
    "ok": "Норма",
}


def _esc(v) -> str:
    if v is None:
        return "—"
    return html.escape(str(v), quote=True)


def _fmt_cost(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}".replace(",", " ") + " руб."
    except Exception:
        return str(v)


def _fmt_area(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}".replace(",", " ") + " кв.м"
    except Exception:
        return str(v)


def _badge(o: CadastralObject) -> str:
    c = o.counts()
    bits = []
    if c.get("critical"):
        bits.append(
            f'<span class="badge" style="background:{SEV_COLOR["critical"]}">критично: {c["critical"]}</span>'
        )
    if c.get("high"):
        bits.append(
            f'<span class="badge" style="background:{SEV_COLOR["high"]}">существенно: {c["high"]}</span>'
        )
    if c.get("medium"):
        bits.append(
            f'<span class="badge" style="background:{SEV_COLOR["medium"]}">средне: {c["medium"]}</span>'
        )
    if c.get("info"):
        bits.append(
            f'<span class="badge" style="background:{SEV_COLOR["info"]}">инфо: {c["info"]}</span>'
        )
    return " ".join(bits) or '<span class="badge" style="background:#16a34a">без проблем</span>'


def _problems_html(o: CadastralObject) -> str:
    if not o.problems:
        return '<p class="muted">Проблемы не выявлены (по доступным ПКК данным).</p>'
    items = sorted(o.problems, key=lambda p: p.severity.rank)
    parts = []
    for p in items:
        color = SEV_COLOR.get(p.severity.value, "#333")
        bg = SEV_BG.get(p.severity.value, "#f5f5f5")
        rec = f'<div class="p-rec">Что делать: {_esc(p.recommendation)}</div>' if p.recommendation else ""
        legal = f'<div class="p-legal">{_esc(p.legal_note)}</div>' if p.legal_note else ""
        gloss = (
            f'<div class="p-gloss">Термины: {", ".join(_esc(g) for g in p.glossary)}</div>'
            if p.glossary
            else ""
        )
        parts.append(
            f"""
            <div class="problem" style="border-left-color:{color};background:{bg}">
              <div class="p-head">
                <span class="badge" style="background:{color}">{SEV_LABEL.get(p.severity.value, p.severity.value)}</span>
                <strong>{_esc(p.title)}</strong>
                <span class="p-code">{_esc(p.code)}</span>
              </div>
              <div class="p-detail">{_esc(p.detail)}</div>
              {legal}{rec}{gloss}
            </div>
            """
        )
    return "\n".join(parts)


def _attrs_html(o: CadastralObject) -> str:
    rows = [
        ("Кадастровый номер", o.number.value),
        ("Тип", o.kind.title_ru),
        ("Статус", o.status),
        ("Адрес", o.address),
        ("Площадь", _fmt_area(o.area)),
        ("Категория земель", o.land_category),
        ("ВРИ / назначение", o.vri or o.purpose),
        ("Форма собственности", o.ownership),
        ("Кадастровая стоимость", _fmt_cost(o.cost_value)),
        ("Удельный показатель", _fmt_cost(o.cost_index) if o.cost_index else "—"),
        ("Дата постановки", o.reg_date),
        ("Кадастровый квартал", o.quarter_number),
        ("Этажность", o.floors),
        ("Год постройки", o.year_built),
        ("Материал стен", o.materials),
        ("Геометрия", "есть" if o.has_geometry else "нет"),
    ]
    trs = []
    for k, v in rows:
        if v in (None, "—") and k not in (
            "ВРИ / назначение",
            "Кадастровая стоимость",
            "Статус",
            "Адрес",
        ):
            continue
        trs.append(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>")
    skip = {
        "cad_num", "status", "readable_address", "ownership_type", "cost_value",
        "cost_index", "determination_couse", "quarter_cad_number", "area",
        "specified_area", "declared_area", "land_record_category_type",
        "permitted_use_established_by_document", "land_record_reg_date",
        "build_record_registration_date", "land_record_type",
        "build_record_type_value", "building_name", "purpose", "floors",
        "year_built", "materials", "permitted_use_name", "geocoderObject",
        "objdocId", "registersId",
    }
    extras = []
    for k, v in (o.raw_options or {}).items():
        if k in skip or v in (None, "", "---"):
            continue
        extras.append(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>")
    extra_html = ""
    if extras:
        extra_html = (
            '<details><summary>Все атрибуты ПКК</summary><table class="attrs">'
            + "".join(extras)
            + "</table></details>"
        )
    return '<table class="attrs">' + "".join(trs) + "</table>" + extra_html


def _table_html(objects: list[CadastralObject]) -> str:
    rows = []
    for o in objects:
        c = o.counts()
        sev = o.max_severity()
        color = SEV_COLOR.get(sev.value, "#666")
        probs = []
        if c.get("critical"):
            probs.append(f"крит. {c['critical']}")
        if c.get("high"):
            probs.append(f"сущ. {c['high']}")
        if c.get("medium"):
            probs.append(f"сред. {c['medium']}")
        if c.get("info"):
            probs.append(f"инфо {c['info']}")
        top = [
            _esc(p.title)
            for p in sorted(o.problems, key=lambda p: p.severity.rank)
            if p.severity in (Severity.CRITICAL, Severity.HIGH)
        ]
        addr = o.address or (o.linked_plot.address if o.linked_plot else None)
        rows.append(
            f"""
            <tr style="border-left:4px solid {color}">
              <td><a href="#obj-{_esc(o.number.value).replace(':', '_')}">{_esc(o.number.value)}</a></td>
              <td>{_esc(o.kind.title_ru)}</td>
              <td>{_esc(addr or "—")}</td>
              <td>{_esc(o.status or "—")}</td>
              <td>{' '.join(f'<span class="badge" style="background:{color}">{p}</span>' for p in probs) or '<span class="badge" style="background:#16a34a">ок</span>'}</td>
              <td>{'; '.join(top[:3]) or '—'}</td>
            </tr>
            """
        )
    return f"""
    <table class="summary">
      <thead><tr><th>КН</th><th>Тип</th><th>Адрес</th><th>Статус</th><th>Проблемы</th><th>Главное</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def _geojson(run: AnalysisRun) -> dict:
    features = []
    kind_colors = {
        "zouit": ("#ea580c", 0.25),
        "red_line": ("#dc2626", 0.35),
        "oopt": ("#16a34a", 0.25),
        "flood": ("#0891b2", 0.3),
        "terr_zone": ("#7c3aed", 0.15),
        "zu": ("#64748b", 0.1),
        "oks": ("#334155", 0.2),
    }

    def add(geom, props):
        if geom and geom.geojson:
            features.append(
                {"type": "Feature", "geometry": geom.geojson, "properties": props}
            )

    for o in run.objects:
        sev = o.max_severity()
        add(
            o.geometry,
            {
                "layer": "object",
                "title": f"{o.number.value} — {o.kind.title_ru}",
                "color": SEV_COLOR.get(sev.value, "#2563eb"),
                "weight": 4,
                "fillOpacity": 0.25,
            },
        )
        if o.linked_plot:
            add(
                o.linked_plot.geometry,
                {
                    "layer": "object",
                    "title": f"ЗУ {o.linked_plot.number.value}",
                    "color": SEV_COLOR.get(o.linked_plot.max_severity().value, "#2563eb"),
                    "weight": 3,
                    "fillOpacity": 0.2,
                },
            )
        for r in o.related:
            if not r.geometry:
                continue
            color, op = kind_colors.get(r.kind.value, ("#94a3b8", 0.15))
            add(
                r.geometry,
                {
                    "layer": r.kind.value,
                    "title": r.title or r.cad_number or r.category_name or r.kind.value,
                    "color": color,
                    "weight": 2,
                    "fillOpacity": op,
                    "dash": 1 if r.kind.value in ("zouit", "red_line") else 0,
                },
            )
    return {"type": "FeatureCollection", "features": features}


def render_html(run: AnalysisRun) -> str:
    geo = json.dumps(_geojson(run), ensure_ascii=False)
    objs_html = []
    for o in run.objects:
        anchor = f"obj-{_esc(o.number.value).replace(':', '_')}"
        err = f'<p class="error">Ошибка: {_esc(o.error)}</p>' if o.error else ""
        linked = ""
        if o.linked_plot:
            zu = o.linked_plot
            linked = f"""
            <section class="obj-inner">
              <h3>Связанный ЗУ: {_esc(zu.number.value)} {_badge(zu)}</h3>
              <p class="muted">{_esc(zu.address)}</p>
              {_attrs_html(zu)}
              <h4>Проблемы участка</h4>
              {_problems_html(zu)}
            </section>
            """
        objs_html.append(
            f"""
            <section class="obj" id="{anchor}">
              <h2>{_esc(o.number.value)} {_badge(o)}</h2>
              <p class="muted">{_esc(o.kind.title_ru)} · {_esc(o.address)}</p>
              {err}
              {_attrs_html(o)}
              <h3>Проблемы</h3>
              {_problems_html(o)}
              {linked}
            </section>
            """
        )
    warn = "".join(f'<div class="warn">{_esc(w)}</div>' for w in run.warnings)
    n = len(run.objects)
    crit = sum(1 for o in run.objects if o.counts().get("critical"))
    high = sum(1 for o in run.objects if o.counts().get("high"))

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Кадастровая сводка — {n}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root {{ --bg:#f8fafc; --card:#fff; --line:#e2e8f0; --text:#0f172a; --muted:#64748b }}
* {{ box-sizing:border-box }}
body {{ margin:0; font-family:"Segoe UI",system-ui,sans-serif; background:var(--bg); color:var(--text) }}
header {{ background:#0f172a; color:#fff; padding:16px 24px }}
header h1 {{ margin:0 0 4px; font-size:20px }}
header .sub {{ color:#cbd5e1; font-size:13px }}
main {{ max-width:1200px; margin:0 auto; padding:16px }}
.kpis {{ display:flex; gap:12px; flex-wrap:wrap; margin:12px 0 }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 16px; min-width:140px }}
.kpi b {{ font-size:22px; display:block }}
.warn {{ background:#fef3c7; border:1px solid #f59e0b; padding:10px 14px; border-radius:8px; margin:10px 0; font-size:14px }}
.error {{ color:#dc2626 }}
.muted {{ color:var(--muted); font-size:13px }}
table.summary, table.attrs {{ width:100%; border-collapse:collapse; background:var(--card); font-size:13px }}
table.summary th, table.summary td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top }}
table.summary th {{ background:#f1f5f9 }}
table.attrs td {{ padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top }}
table.attrs td:first-child {{ color:var(--muted); width:220px }}
.badge {{ display:inline-block; color:#fff; border-radius:999px; padding:2px 8px; font-size:11px; font-weight:600 }}
section.obj {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 20px; margin:16px 0 }}
section.obj h2 {{ margin:0 0 8px; font-size:18px }}
section.obj-inner {{ border-top:1px dashed var(--line); margin-top:16px; padding-top:12px }}
.problem {{ border-left:4px solid; border-radius:8px; padding:10px 12px; margin:8px 0 }}
.p-head {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap }}
.p-code {{ color:var(--muted); font-family:monospace; font-size:11px }}
.p-detail {{ margin-top:6px; font-size:14px }}
.p-legal, .p-rec, .p-gloss {{ margin-top:6px; font-size:13px; color:#334155 }}
.p-gloss {{ color:var(--muted) }}
#map {{ height:480px; border-radius:12px; border:1px solid var(--line); background:#e2e8f0 }}
.legend {{ display:flex; gap:12px; flex-wrap:wrap; font-size:12px; color:var(--muted); margin:8px 0 }}
.legend span {{ display:inline-flex; align-items:center; gap:6px }}
.dot {{ width:12px; height:12px; border-radius:3px; display:inline-block }}
footer {{ color:var(--muted); font-size:12px; padding:20px; text-align:center }}
</style>
</head>
<body>
<header>
  <h1>Кадастровая сводка · подсветка проблем</h1>
  <div class="sub">ПКК/НСПД · {_esc(run.created_at)} · объектов: {n}</div>
</header>
<main>
  <div class="kpis">
    <div class="kpi"><b>{n}</b>объектов</div>
    <div class="kpi" style="border-color:{SEV_COLOR['critical']}"><b style="color:{SEV_COLOR['critical']}">{crit}</b>с критичными</div>
    <div class="kpi" style="border-color:{SEV_COLOR['high']}"><b style="color:{SEV_COLOR['high']}">{high}</b>с существенными</div>
  </div>
  {warn}
  <h2>Сводная таблица</h2>
  {_table_html(run.objects)}
  <h2>Карта</h2>
  <div class="legend">
    <span><i class="dot" style="background:#dc2626"></i>критично</span>
    <span><i class="dot" style="background:#ea580c"></i>ЗОУИТ</span>
    <span><i class="dot" style="background:#7c3aed"></i>тер. зоны</span>
    <span><i class="dot" style="background:#16a34a"></i>ООПТ</span>
    <span><i class="dot" style="background:#0891b2"></i>затопление</span>
    <span><i class="dot" style="background:#64748b"></i>смежные ЗУ</span>
  </div>
  <div id="map"></div>
  <p class="muted">Аресты, ипотеки, сервитуты на карте не отображаются — см. выписку ЕГРН.</p>
  {''.join(objs_html)}
</main>
<footer>Проект «Законник» · kadastr · данные ПКК не являются выпиской из ЕГРН</footer>
<script>window.NSPD_GEOJSON = {geo};</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
(function() {{
  var map = L.map('map', {{ preferCanvas: true }});
  var osm = L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 19, attribution: '© OpenStreetMap' }});
  osm.addTo(map);
  L.control.layers({{'OpenStreetMap': osm}}).addTo(map);
  var gj = L.geoJSON(window.NSPD_GEOJSON, {{
    style: function(f) {{
      var p = f.properties || {{}};
      return {{
        color: p.color || '#2563eb', weight: p.weight || 2,
        fillColor: p.color || '#2563eb',
        fillOpacity: p.fillOpacity != null ? p.fillOpacity : 0.2,
        dashArray: p.dash ? '4 4' : null
      }};
    }},
    onEachFeature: function(f, layer) {{
      var t = (f.properties || {{}}).title || '';
      layer.bindPopup('<b>' + t + '</b>');
      layer.bindTooltip(t, {{ sticky: true }});
    }}
  }}).addTo(map);
  try {{ map.fitBounds(gj.getBounds(), {{ padding: [24, 24] }}); }} catch(e) {{}}
  if (!window.NSPD_GEOJSON.features.length) map.setView([55.75, 37.62], 10);
}})();
</script>
</body>
</html>
"""
