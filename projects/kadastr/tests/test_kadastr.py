"""Юнит-тесты домена, ACL и use cases. Запуск: python -m unittest discover tests"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kadastr.application.usecases import AnalyzeCadastralObjects, extract_numbers  # noqa: E402
from kadastr.domain.entities import CadastralObject  # noqa: E402
from kadastr.domain.glossary import Glossary  # noqa: E402
from kadastr.domain.problems import ProblemPolicy  # noqa: E402
from kadastr.domain.value_objects import (  # noqa: E402
    CadastralNumber,
    ObjectKind,
    Severity,
)
from kadastr.infrastructure.glossary_repo import JsonGlossaryRepository  # noqa: E402
from kadastr.infrastructure.nspd.acl import NspdAcl  # noqa: E402


class FakeGateway:
    def __init__(self, objects=None, related=None):
        self.objects = objects or {}
        self.related = related or []

    def find_object(self, cn):
        return self.objects.get(cn.value)

    def find_linked_plot_number(self, obj):
        return None

    def load_tabs(self, obj):
        return {}

    def load_intersecting(self, obj):
        return self.related

    def close(self):
        pass


def make_plot(cn="50:14:0000000:156713", **kw):
    defaults = dict(
        kind=ObjectKind.LAND_PLOT,
        status="Учтенный",
        address="тест",
        area=1391.0,
        vri="---",
        has_geometry=True,
        found=True,
    )
    defaults.update(kw)
    return CadastralObject(number=CadastralNumber(cn), **defaults)


class TestValueObjects(unittest.TestCase):
    def test_cn_ok(self):
        self.assertEqual(CadastralNumber("50:14:0000000:156789").value, "50:14:0000000:156789")
        self.assertEqual(CadastralNumber(" 50:14:0000000:156789 ").value, "50:14:0000000:156789")

    def test_cn_bad(self):
        with self.assertRaises(ValueError):
            CadastralNumber("hello")
        with self.assertRaises(ValueError):
            CadastralNumber("50:14")
        with self.assertRaises(ValueError):
            CadastralNumber("50:14:1:2:3")

    def test_severity_rank(self):
        self.assertLess(Severity.CRITICAL.rank, Severity.HIGH.rank)
        self.assertLess(Severity.HIGH.rank, Severity.MEDIUM.rank)


class TestExtractNumbers(unittest.TestCase):
    def test_from_text(self):
        text = """
        # комментарий
        50:14:0000000:156789
        мусор 77:01:0001001:1 и 50:14:0000000:156789
        """
        self.assertEqual(extract_numbers(text), ["50:14:0000000:156789", "77:01:0001001:1"])


class TestProblemPolicy(unittest.TestCase):
    def test_not_found(self):
        obj = CadastralObject(number=CadastralNumber("50:14:0000000:156789"), found=False)
        ProblemPolicy().evaluate(obj)
        codes = [p.code for p in obj.problems]
        self.assertIn("not_found", codes)
        self.assertEqual(obj.max_severity(), Severity.CRITICAL)

    def test_vri_missing(self):
        obj = make_plot(vri="---", has_geometry=True)
        ProblemPolicy().evaluate(obj)
        codes = [p.code for p in obj.problems]
        self.assertIn("vri_missing", codes)
        self.assertIn("need_egrn_rights", codes)
        self.assertIn("landplot_ok_base", codes)

    def test_no_geometry(self):
        obj = make_plot(has_geometry=False)
        ProblemPolicy().evaluate(obj)
        codes = [p.code for p in obj.problems]
        self.assertIn("no_geometry", codes)

    def test_building_on_zu_not_overlap(self):
        obj = CadastralObject(
            number=CadastralNumber("50:14:0000000:156789"),
            kind=ObjectKind.BUILDING,
            found=True,
            has_geometry=True,
            parent_plot_number=CadastralNumber("50:14:0000000:156713"),
        )
        ProblemPolicy().evaluate(obj)
        codes = [p.code for p in obj.problems]
        self.assertIn("oks_on_zu", codes)
        self.assertNotIn("zu_overlap", codes)


class TestAcl(unittest.TestCase):
    def setUp(self):
        self.acl = NspdAcl()
        self.feat = {
            "id": 446529371,
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[4240133.7, 7550380.7], [4240153.4, 7550374.0], [4240159.8, 7550393.0], [4240133.7, 7550380.7]]],
                "crs": {"type": "name", "properties": {"name": "EPSG:3857"}},
            },
            "properties": {
                "category": 36369,
                "categoryName": "Здания",
                "label": "50:14:0000000:156789",
                "options": {
                    "cad_num": "50:14:0000000:156789",
                    "build_record_area": 306.7,
                    "build_record_type_value": "Здание",
                    "building_name": "Жилой дом",
                    "purpose": "Жилой дом",
                    "status": "Учтенный",
                    "readable_address": "МО, тест",
                    "cost_value": 11805854.63,
                    "permitted_use_name": "Жилой дом",
                },
            },
        }

    def test_to_object_building(self):
        obj = self.acl.feature_to_object(self.feat)
        self.assertTrue(obj.found)
        self.assertEqual(obj.number.value, "50:14:0000000:156789")
        self.assertEqual(obj.kind, ObjectKind.BUILDING)
        self.assertEqual(obj.area, 306.7)
        self.assertTrue(obj.has_geometry)
        self.assertIsNotNone(obj.geometry)
        self.assertAlmostEqual(obj.geometry.centroid_lon, 38.08, delta=0.2)

    def test_to_related_zouit(self):
        feat = {
            "id": 1,
            "geometry": None,
            "properties": {
                "category": 36940,
                "categoryName": "Зоны с особыми условиями использования территории",
                "options": {
                    "name_by_doc": "Зона регулирования застройки ОКН «Усадьба Гребнево»",
                    "content_restrict_encumbrances": "Постановление № 1117/49",
                },
            },
        }
        rel = self.acl.feature_to_related(feat)
        self.assertEqual(rel.kind.value, "zouit")
        self.assertIn("Усадьба", rel.title or "")


class TestUseCase(unittest.TestCase):
    def test_not_found_envelope(self):
        uc = AnalyzeCadastralObjects(FakeGateway())
        run = uc.execute(["50:14:0000000:156789"])
        self.assertEqual(len(run.objects), 1)
        self.assertFalse(run.objects[0].found)
        env = uc.execute_envelope(["50:14:0000000:156789"])
        self.assertTrue(env.ok)
        self.assertFalse(env.data["objects"][0]["found"])

    def test_found_plot_vri(self):
        plot = make_plot()
        uc = AnalyzeCadastralObjects(FakeGateway({plot.number.value: plot}))
        env = uc.execute_envelope(["50:14:0000000:156713"])
        self.assertTrue(env.ok)
        o = env.data["objects"][0]
        self.assertTrue(o["found"])
        codes = [p["code"] for p in o["problems"]]
        self.assertIn("vri_missing", codes)


class TestGlossary(unittest.TestCase):
    def test_load_json(self):
        repo = JsonGlossaryRepository(ROOT / "data" / "glossary.json")
        g = Glossary.from_list(repo.load())
        self.assertGreater(len(g.all()), 20)
        vri = g.get("vri")
        self.assertIsNotNone(vri)
        self.assertTrue(vri.for_citizen)
        self.assertTrue(vri.for_lawyer)
        self.assertTrue(vri.regulations)


if __name__ == "__main__":
    unittest.main()
