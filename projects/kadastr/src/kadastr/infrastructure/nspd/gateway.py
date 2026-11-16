"""Адаптер НСПД: реализация порта CadastralGateway.

Сменяется на другой Gateway (ЕГРН API, файловый мок) без правки домена.
"""
from __future__ import annotations

from ...domain.entities import CadastralObject
from ...domain.geometry import geometries_overlap
from ...domain.value_objects import CadastralNumber, RelatedFeature
from ...domain.gateways import CadastralGateway
from .acl import NspdAcl, _cad_of
from .http import NspdBlocked, NspdHttpClient, NspdHttpError

# Категории слоёв НСПД для intersects
CAT_ZOUIT = 36940
CAT_RED_LINES = 38942
CAT_TERR_ZONES = 472819
CAT_OOPT = 472825
CAT_FLOOD = 38967
CAT_ZU = 36368
CAT_OKS = 36369
INTERSECT_CATEGORIES = [
    CAT_ZOUIT,
    CAT_RED_LINES,
    CAT_TERR_ZONES,
    CAT_OOPT,
    CAT_FLOOD,
    CAT_ZU,
    CAT_OKS,
]


class NspdGateway(CadastralGateway):
    def __init__(
        self,
        *,
        delay: float = 0.7,
        retries: int = 3,
        proxy: str | None = None,
        timeout: float = 30.0,
        acl: NspdAcl | None = None,
    ):
        self.http = NspdHttpClient(
            delay=delay, retries=retries, proxy=proxy, timeout=timeout
        )
        self.acl = acl or NspdAcl()

    def find_object(self, cn: CadastralNumber) -> CadastralObject | None:
        feat = self._find_feature(cn.value)
        if not feat:
            return None
        return self.acl.feature_to_object(feat, found=True)

    def find_linked_plot_number(self, obj: CadastralObject) -> CadastralNumber | None:
        if obj.kind.value not in ("building", "construction", "unfinished"):
            return None
        feat = self._find_feature(obj.number.value)
        if not feat:
            return None
        try:
            links = self.http.tab_values(feat, "landLinks") or []
        except NspdHttpError:
            return None
        cns = self.acl.extract_cns(links)
        if not cns:
            return None
        try:
            return CadastralNumber(cns[0])
        except ValueError:
            return None

    def load_tabs(self, obj: CadastralObject) -> dict[str, list[str]]:
        feat = self._find_feature(obj.number.value)
        if not feat:
            return {}
        tabs: dict[str, list[str]] = {}
        try:
            links = self.http.tab_values(feat, "landLinks") or []
            if links:
                tabs["landLinks"] = links
        except NspdHttpError:
            pass
        try:
            parts = self.http.tab_values(feat, "landParts") or self.http.tab_values(
                feat, "buildParts"
            )
            if parts:
                tabs["parts"] = parts
        except NspdHttpError:
            pass
        try:
            vri = self.http.tab_values(feat, "permissionType")
            if vri:
                tabs["vri"] = vri
        except NspdHttpError:
            pass
        return tabs

    def load_intersecting(self, obj: CadastralObject) -> list[RelatedFeature]:
        if not obj.has_geometry or not obj.geometry:
            return []
        # нужен исходный меркатор — возьмём из ACL при первичной загрузке;
        # geometry у нас уже WGS84. Сервер принимает WGS84 с crs 4326.
        geom = dict(obj.geometry.geojson)
        try:
            feats = self.http.intersects(geom, INTERSECT_CATEGORIES) or []
        except NspdBlocked:
            raise
        except NspdHttpError:
            return []
        out: list[RelatedFeature] = []
        for f in feats:
            rel = self.acl.feature_to_related(f)
            if not rel:
                continue
            if rel.cad_number == obj.number.value:
                continue  # сам объект не нужен в related
            overlap = False
            if rel.kind.value == "zu" and obj.geometry and rel.geometry:
                try:
                    overlap = geometries_overlap(
                        obj.geometry.geojson, rel.geometry.geojson
                    )
                except Exception:
                    overlap = False
            out.append(
                RelatedFeature(
                    kind=rel.kind,
                    cad_number=rel.cad_number,
                    category_name=rel.category_name,
                    title=rel.title,
                    options=rel.options,
                    geometry=rel.geometry,
                    overlap=overlap,
                )
            )
        return out

    def geocode(self, address: str) -> list[dict]:
        return self.http.geocode(address)

    def search_text(self, query: str) -> list[dict]:
        """Поиск НСПД по произвольной строке (адрес, КН, название)."""
        return self.http.search(query, thematic_search_id=1) or []

    def close(self) -> None:
        # urllib opener не требует close
        return None

    def _find_feature(self, cn: str) -> dict | None:
        for thematic in (1, 2):
            feats = self.http.search(cn, thematic_search_id=thematic) or []
            exact = [f for f in feats if _cad_of(f) == cn]
            if exact:
                return exact[0]
            if len(feats) == 1:
                return feats[0]
            if feats:
                # лучший по вхождению
                scored = sorted(
                    feats,
                    key=lambda f: str(
                        (f.get("properties") or {}).get("label") or ""
                    ).find(cn)
                    >= 0,
                    reverse=True,
                )
                return scored[0]
        return None
