"""Use cases. Зависят от портов домена, не от НСПД."""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

from ..domain.entities import AnalysisRun, CadastralObject
from ..domain.gateways import CadastralGateway, GlossaryRepository
from ..domain.glossary import Glossary
from ..domain.problems import ProblemPolicy
from ..domain.value_objects import CadastralNumber
from .dtos import Envelope, fail, object_summary, ok


CN_RE = re.compile(r"\b\d{2}:\d{2}:\d{6,7}:\d+\b")


def extract_numbers(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in CN_RE.findall(text or ""):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


class AnalyzeCadastralObjects:
    """Основной use case: по списку КН собрать агрегаты и применить правила проблем."""

    def __init__(
        self,
        gateway: CadastralGateway,
        policy: ProblemPolicy | None = None,
    ):
        self.gateway = gateway
        self.policy = policy or ProblemPolicy()

    def execute(self, numbers: Iterable[str]) -> AnalysisRun:
        run = AnalysisRun(created_at=dt.datetime.now().isoformat(timespec="seconds"))
        unique: list[str] = []
        for raw in numbers:
            cn = (raw or "").strip()
            if cn and cn not in unique:
                unique.append(cn)
        for cn in unique:
            run.objects.append(self._analyze_one(cn))
        if any((o.error or "").find("403") >= 0 for o in run.objects):
            run.warnings.append(
                "Часть запросов заблокирована (403). НСПД ограничивает доступ "
                "с зарубежных/датацентровых IP. Настройте российский резидентный IP или прокси."
            )
        return run

    def execute_envelope(self, numbers: Iterable[str]) -> Envelope:
        try:
            run = self.execute(numbers)
        except Exception as e:
            return fail("analyze_failed", str(e))
        return ok(
            run.to_dict(),
            generated_at=run.created_at,
            objects=len(run.objects),
            source="nspd",
        )

    def _analyze_one(self, cn_raw: str) -> CadastralObject:
        try:
            number = CadastralNumber(cn_raw)
        except ValueError as e:
            bad = CadastralNumber.__new__(CadastralNumber)
            object.__setattr__(bad, "value", cn_raw)
            obj = CadastralObject(number=bad, found=False, error=str(e))
            self.policy.evaluate(obj)
            return obj

        try:
            obj = self.gateway.find_object(number)
        except Exception as e:
            obj = CadastralObject(number=number, found=False, error=str(e))
            self.policy.evaluate(obj)
            return obj

        if not obj:
            obj = CadastralObject(number=number, found=False)
            self.policy.evaluate(obj)
            return obj

        try:
            tabs = self.gateway.load_tabs(obj)
            if tabs:
                obj.tabs = tabs
                if tabs.get("vri") and (not obj.vri or obj.vri == "---"):
                    joined = "; ".join(v for v in tabs["vri"] if str(v).strip() not in ("", "---"))
                    if joined:
                        obj.vri = joined
        except Exception as e:
            obj.error = (obj.error or "") + f"; tabs: {e}"

        try:
            obj.related = self.gateway.load_intersecting(obj)
        except Exception as e:
            obj.error = (obj.error or "") + f"; intersects: {e}"

        # ОКС → связанный ЗУ
        if obj.kind.value in ("building", "construction", "unfinished"):
            try:
                parent = self.gateway.find_linked_plot_number(obj)
                if parent:
                    obj.parent_plot_number = parent
                    linked = self.gateway.find_object(parent)
                    if linked:
                        try:
                            linked.related = self.gateway.load_intersecting(linked)
                        except Exception as e:
                            linked.error = (linked.error or "") + f"; intersects: {e}"
                        try:
                            ltabs = self.gateway.load_tabs(linked)
                            if ltabs:
                                linked.tabs = ltabs
                                if ltabs.get("vri") and (not linked.vri or linked.vri == "---"):
                                    joined = "; ".join(
                                        v for v in ltabs["vri"] if str(v).strip() not in ("", "---")
                                    )
                                    if joined:
                                        linked.vri = joined
                        except Exception:
                            pass
                        self.policy.evaluate(linked)
                        obj.linked_plot = linked
            except Exception as e:
                obj.error = (obj.error or "") + f"; parent ZU: {e}"

        self.policy.evaluate(obj)
        return obj


class GetGlossary:
    def __init__(self, repo: GlossaryRepository):
        self.repo = repo

    def execute(self) -> Glossary:
        return Glossary.from_list(self.repo.load() or [])

    def execute_envelope(self, code: str | None = None) -> Envelope:
        try:
            g = self.execute()
        except Exception as e:
            return fail("glossary_failed", str(e))
        if code:
            term = g.get(code)
            if not term:
                return fail("term_not_found", f"Термин не найден: {code}")
            return ok(term.to_dict())
        return ok(g.to_dict())
