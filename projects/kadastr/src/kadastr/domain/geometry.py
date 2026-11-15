"""Чистая геометрия: WGS84/меркатор, полигоны, наложения. Без I/O."""
from __future__ import annotations

import math
from typing import Callable, Iterable

R_EQ = 6378137.0
ORIGIN = 20037508.342789244


def mercator_to_lonlat(x: float, y: float) -> tuple[float, float]:
    return x / ORIGIN * 180.0, math.degrees(2.0 * math.atan(math.exp(y / R_EQ)) - math.pi / 2.0)


def transform_geom(geom: dict, fn: Callable[[float, float], tuple[float, float]]) -> dict:
    out: dict = {"type": geom.get("type"), "coordinates": None}
    t = geom.get("type")
    if t == "Point":
        out["coordinates"] = list(fn(*geom["coordinates"][:2]))
    elif t in ("MultiPoint", "LineString"):
        out["coordinates"] = [list(fn(x, y)) for x, y in geom["coordinates"]]
    elif t == "MultiLineString":
        out["coordinates"] = [[list(fn(x, y)) for x, y in ring] for ring in geom["coordinates"]]
    elif t == "Polygon":
        out["coordinates"] = [[list(fn(x, y)) for x, y in ring] for ring in geom["coordinates"]]
    elif t == "MultiPolygon":
        out["coordinates"] = [
            [[list(fn(x, y)) for x, y in ring] for ring in poly]
            for poly in geom["coordinates"]
        ]
    else:
        raise ValueError(f"Unsupported geometry type: {t}")
    return out


def to_wgs84(geom: dict) -> dict:
    g = transform_geom(geom, mercator_to_lonlat)
    g["crs"] = {"type": "name", "properties": {"name": "EPSG:4326"}}
    return g


def rings_of(geom: dict) -> list[list[tuple[float, float]]]:
    t = geom.get("type")
    if t == "Polygon":
        return [[(p[0], p[1]) for p in ring] for ring in geom["coordinates"]]
    if t == "MultiPolygon":
        return [
            [(p[0], p[1]) for p in ring]
            for poly in geom["coordinates"]
            for ring in poly
        ]
    if t == "LineString":
        return [[(p[0], p[1]) for p in geom["coordinates"]]]
    if t == "MultiLineString":
        return [[(p[0], p[1]) for p in ring] for ring in geom["coordinates"]]
    return []


def bbox_of(geom: dict) -> tuple[float, float, float, float] | None:
    pts = [p for ring in rings_of(geom) for p in ring]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def centroid_of(geom: dict) -> tuple[float, float] | None:
    bb = bbox_of(geom)
    if not bb:
        return None
    return (bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0


def point_in_ring(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    inside = False
    n = len(ring)
    if n < 3:
        return False
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            x_int = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-15) + x1
            if x < x_int:
                inside = not inside
    return inside


def point_in_geom(x: float, y: float, geom: dict) -> bool:
    t = geom.get("type")
    if t == "Point":
        return abs(x - geom["coordinates"][0]) < 1e-9 and abs(y - geom["coordinates"][1]) < 1e-9
    if t not in ("Polygon", "MultiPolygon"):
        return False
    if t == "Polygon":
        rings = [[(p[0], p[1]) for p in ring] for ring in geom["coordinates"]]
        if not rings or not point_in_ring(x, y, rings[0]):
            return False
        return not any(point_in_ring(x, y, r) for r in rings[1:])
    for poly in geom["coordinates"]:
        outer = [(p[0], p[1]) for p in poly[0]]
        if not point_in_ring(x, y, outer):
            continue
        holes = [[(p[0], p[1]) for p in ring] for ring in poly[1:]]
        if not any(point_in_ring(x, y, h) for h in holes):
            return True
    return False


def _segments_intersect(p1, p2, p3, p4) -> bool:
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1 = orient(p3, p4, p1)
    d2 = orient(p3, p4, p2)
    d3 = orient(p1, p2, p3)
    d4 = orient(p1, p2, p4)
    return ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    )


def _outer_pts(geom: dict) -> list[tuple[float, float]]:
    if geom["type"] == "Polygon":
        return [(p[0], p[1]) for p in geom["coordinates"][0]]
    pts: list[tuple[float, float]] = []
    for poly in geom["coordinates"]:
        pts.extend((p[0], p[1]) for p in poly[0])
    return pts


def _edges(geom: dict) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    segs = []
    for poly in polys:
        for ring in poly:
            n = len(ring)
            for i in range(n):
                segs.append(((ring[i][0], ring[i][1]), (ring[(i + 1) % n][0], ring[(i + 1) % n][1])))
    return segs


def geometries_overlap(geom_a: dict, geom_b: dict) -> bool:
    """Ненулевое перекрытие полигонов (общая вершина/касание — не наложение)."""
    if geom_a.get("type") not in ("Polygon", "MultiPolygon"):
        return False
    if geom_b.get("type") not in ("Polygon", "MultiPolygon"):
        return False
    bb_a, bb_b = bbox_of(geom_a), bbox_of(geom_b)
    if not bb_a or not bb_b:
        return False
    if bb_a[2] < bb_b[0] or bb_b[2] < bb_a[0] or bb_a[3] < bb_b[1] or bb_b[3] < bb_a[1]:
        return False

    pts_a, pts_b = _outer_pts(geom_a), _outer_pts(geom_b)
    inside_a = sum(1 for x, y in pts_a if point_in_geom(x, y, geom_b))
    inside_b = sum(1 for x, y in pts_b if point_in_geom(x, y, geom_a))
    if inside_a >= 2 or inside_b >= 2:
        return True
    if inside_a == 0 and inside_b == 0:
        return False
    crossings = 0
    for p1, p2 in _edges(geom_a):
        for p3, p4 in _edges(geom_b):
            if _segments_intersect(p1, p2, p3, p4):
                crossings += 1
                if crossings >= 2:
                    return True
    return False
