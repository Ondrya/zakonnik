"""Доменная политика подсветки проблем.

Работает только с агрегатом CadastralObject. Не знает про НСПД.
Коды проблем связаны со справочником терминов (glossary).
"""
from __future__ import annotations

from .entities import CadastralObject
from .value_objects import Problem, Severity


SRC = "ПКК/НСПД Росреестра"
SRC_EGRN = "Выписка ЕГРН (ГРП)"


def _p(**kwargs) -> Problem:
    return Problem(**kwargs)


class ProblemPolicy:
    """Применяeт правила к объекту. Вызывается use case'ом после наполнения агрегата."""

    def evaluate(self, obj: CadastralObject) -> None:
        obj.problems = []
        self._existence(obj)
        if not obj.found:
            return
        self._geometry(obj)
        self._status(obj)
        self._vri_and_area(obj)
        self._heritage_on_object(obj)
        self._cost(obj)
        self._related(obj)
        self._egrn_reminder(obj)
        self._ok_base(obj)
        self._linked_plot(obj)

    def _existence(self, obj: CadastralObject) -> None:
        if obj.found:
            return
        obj.apply_problem(
            _p(
                code="not_found",
                severity=Severity.CRITICAL,
                title="Объект не найден",
                detail=(
                    f"По кадастровому номеру {obj.number.value} объект недвижимости "
                    "в НСПД/ПКК не найден. Возможны: ошибка в номере, объект снят "
                    "с учёта, номер относится к кадастровому делению, либо сведения "
                    "ещё не опубликованы."
                ),
                legal_note=(
                    "Отсутствие объекта в ПКК не заменяет выписку ЕГРН: актуальный "
                    "статус и права подтверждаются только выпиской из ЕГРН."
                ),
                recommendation="Проверьте номер. Запросите выписку ЕГРН об основных характеристиках и о переходе прав.",
                source=SRC,
                glossary=("egrn", "pkk", "kad_nomer"),
            )
        )

    def _geometry(self, obj: CadastralObject) -> None:
        if obj.has_geometry:
            return
        obj.apply_problem(
            _p(
                code="no_geometry",
                severity=Severity.CRITICAL,
                title="Границы не установлены / нет координат",
                detail=(
                    "В ПКК отсутствует геометрия объекта. Для земельного участка это "
                    "означает, что границы не установлены в соответствии с требованиями "
                    "закона (межевание не проведено либо сведения не внесены)."
                ),
                legal_note=(
                    "ФЗ-218 «О государственной регистрации недвижимости» и ФЗ-221 "
                    "«О кадастровой деятельности»: без описания местоположения границ "
                    "распоряжение ЗУ сильно ограничено."
                ),
                recommendation="Заказать выписку ЕГРН. Для ЗУ — межевание (межевой план) и внесение границ в ЕГРН.",
                source=SRC,
                glossary=("mezhevanie", "granicy", "egrn"),
            )
        )

    def _status(self, obj: CadastralObject) -> None:
        status = (obj.status or "").strip().lower()
        if status in ("временный", "временная", "temporary"):
            obj.apply_problem(
                _p(
                    code="status_temporary",
                    severity=Severity.CRITICAL,
                    title="Статус «Временный»",
                    detail="Кадастровый учёт не завершён (нет регистрации права). Объект может быть снят с учёта по истечении срока.",
                    legal_note="Временный статус характерен для ЗУ, поставленных на учёт до регистрации права. Сделки невозможны до госрегистрации.",
                    recommendation="Проверить наличие прав в ЕГРН; завершить оформление.",
                    source=SRC,
                    glossary=("status_temporary", "egrn"),
                )
            )
        elif "снят" in status or "аннулир" in status or "исключен" in status:
            obj.apply_problem(
                _p(
                    code="status_removed",
                    severity=Severity.CRITICAL,
                    title=f"Статус: {obj.status}",
                    detail="Объект снят с государственного кадастрового учёта.",
                    legal_note="Объект прекратил существование как объект недвижимости либо преобразован.",
                    recommendation="Запросить выписку ЕГРН о снятии с учёта и о правопреемниках.",
                    source=SRC,
                    glossary=("status", "egrn"),
                )
            )
        elif "ранее учтенный" in status or "ранее учтённый" in status:
            obj.apply_problem(
                _p(
                    code="status_early",
                    severity=Severity.MEDIUM,
                    title="Статус «Ранее учтенный»",
                    detail="Объект учтён по прежним правилам. Часто сопровождается отсутствием/неточностью границ.",
                    legal_note="Для ЗУ может требоваться уточнение местоположения границ.",
                    recommendation="Проверить описание границ в выписке ЕГРН.",
                    source=SRC,
                    glossary=("status_early", "mezhevanie"),
                )
            )

    def _vri_and_area(self, obj: CadastralObject) -> None:
        vri = (obj.vri or "").strip()
        if obj.kind.value == "landplot" and vri in ("", "---", "-", "не установлен", "отсутствует"):
            obj.apply_problem(
                _p(
                    code="vri_missing",
                    severity=Severity.HIGH,
                    title="Вид разрешенного использования не установлен",
                    detail="В сведениях ЕГРН/ПКК ВРИ земельного участка отсутствует (значение «---»). Целевое использование не определено.",
                    legal_note=(
                        "ВРИ — обязательная характеристика ЗУ (ст. 37 ГрК РФ, ЗК РФ). "
                        "Использование не по назначению — штраф (ст. 8.8 КоАП РФ), "
                        "в крайних случаях — изъятие. Влияет на кадастровую стоимость и налог."
                    ),
                    recommendation="Установить ВРИ по ПЗЗ/генплану и внести в ЕГРН.",
                    source=SRC,
                    glossary=("vri", "pzz", "koaap_8_8"),
                )
            )
        if obj.kind.value == "landplot" and obj.area is None:
            obj.apply_problem(
                _p(
                    code="no_area",
                    severity=Severity.MEDIUM,
                    title="Площадь не указана",
                    detail="В ПКК площадь ЗУ не заполнена.",
                    legal_note="Площадь — обязательная характеристика ЗУ.",
                    recommendation="Сверить площадь с выпиской ЕГРН и межевым планом.",
                    source=SRC,
                    glossary=("ploschad", "mezhevanie"),
                )
            )

    def _heritage_on_object(self, obj: CadastralObject) -> None:
        ch = (obj.raw_options.get("cultural_heritage_val") or "").strip()
        if not ch:
            return
        obj.apply_problem(
            _p(
                code="object_is_heritage",
                severity=Severity.CRITICAL,
                title="Объект культурного наследия",
                detail=f"Объект включён в реестр ОКН: {ch}.",
                legal_note=(
                    "Охрана, содержание и использование ОКН регулируются ФЗ-73. "
                    "Изменение облика, работы; собственник обязуется сохранять объект."
                ),
                recommendation="Изучить предмет охраны и охранные обязательства.",
                source=SRC,
                glossary=("okn", "fz73"),
            )
        )

    def _cost(self, obj: CadastralObject) -> None:
        if obj.cost_value is None:
            obj.apply_problem(
                _p(
                    code="no_cost",
                    severity=Severity.MEDIUM,
                    title="Кадастровая стоимость не определена",
                    detail="В ПКК кадастровая стоимость отсутствует.",
                    legal_note="От КС считается земельный налог / налог на имущество.",
                    recommendation="Проверить внесение КС в ЕГРН.",
                    source=SRC,
                    glossary=("kad_stoimost", "nalog"),
                )
            )
            return
        cause = (obj.cost_cause or "").lower()
        if "бюджетного учреждения" in cause or "массов" in cause:
            obj.apply_problem(
                _p(
                    code="cost_mass_valuation",
                    severity=Severity.MEDIUM,
                    title="Кадастровая стоимость определена массовым методом",
                    detail=f"Основание: {obj.cost_cause}. Возможна неточность относительно рыночной стоимости.",
                    legal_note="ФЗ-237: КС может быть оспорена в комиссии при Росреестре или в суде. Юрлица — сначала комиссия.",
                    recommendation="Сопоставить КС с рыночной; при расхождении — оспаривание.",
                    source=SRC,
                    glossary=("kad_stoimost", "fz237"),
                )
            )

    def _related(self, obj: CadastralObject) -> None:
        zouit = obj.related_by_kind("zouit")
        red = obj.related_by_kind("red_line")
        terr = obj.related_by_kind("terr_zone")
        oopt = obj.related_by_kind("oopt")
        flood = obj.related_by_kind("flood")
        others = obj.other_plots()
        oks = obj.related_by_kind("oks")

        if red:
            names = "; ".join(filter(None, (r.title for r in red))) or "красные линии"
            obj.apply_problem(
                _p(
                    code="red_lines",
                    severity=Severity.CRITICAL,
                    title="Пересечение с красными линиями",
                    detail=f"Выявлено пересечение с красными линиями: {names}.",
                    legal_note="Красные линии (ст. 1 ГрК РФ) — границы территорий общего пользования. Строительство в их пределах запрещено; возможен снос/реконструкция.",
                    recommendation="Получить выписку о красных линиях в ОМСУ; проверить ПЗЗ и проекты планировки.",
                    source=SRC,
                    glossary=("krasnye_linii", "grk", "pzz"),
                )
            )
        if flood:
            names = "; ".join(filter(None, (r.title for r in flood)))
            obj.apply_problem(
                _p(
                    code="flood_zone",
                    severity=Severity.CRITICAL,
                    title="Зона затопления/подтопления",
                    detail=f"Пересечение со слоем затопления/подтопления: {names or 'выявлено'}.",
                    legal_note="Зоны затопления/подтопления (ст. 67.1 Водного кодекса РФ) — особые условия использования. Ограничения на новые объекты, страхование.",
                    recommendation="Проверить генплан/ПЗЗ и схемы зон затопления; учесть риски.",
                    source=SRC,
                    glossary=("zatoplenie", "vk_rf"),
                )
            )
        if oopt:
            names = "; ".join(filter(None, (r.title for r in oopt)))
            obj.apply_problem(
                _p(
                    code="oopt",
                    severity=Severity.CRITICAL,
                    title="Особо охраняемая природная территория",
                    detail=f"Пересечение с ООПТ: {names or 'выявлено'}.",
                    legal_note="Режим ООПТ (ФЗ-33) ограничивает хозяйственную деятельность, строительство, рубки.",
                    recommendation="Установить категорию ООПТ и режим; согласовать с природоохранным органом.",
                    source=SRC,
                    glossary=("oopt", "fz33"),
                )
            )

        energy_keys = ("энерг", "лэп", "электропередач", "газопровод", "связ", "транспорт", "железн", "автомобильн", "трубопровод")
        heritage_keys = ("культурн", "памятник", "усадьб", "охранная зона объекта культурн", "защитная зона объекта культурн")
        nature_keys = ("природн", "водоохр", "лесопарк", "зеленый фонд", "особо охраняем")
        security_keys = ("безопасност", "охраняем", "границ", "погранич", "оборон")

        for r in zouit:
            blob = (r.title or "") + " " + " ".join(str(v) for v in (r.options or {}).values())
            tl = blob.lower()
            doc = (
                f"{r.options.get('legal_act_document_name') or ''} "
                f"{r.options.get('legal_act_document_number') or ''} "
                f"от {r.options.get('legal_act_document_date') or ''} "
                f"({r.options.get('legal_act_document_issuer') or ''})"
            ).strip()
            restrict = r.options.get("content_restrict_encumbrances") or "—"
            if any(k in tl for k in heritage_keys):
                obj.apply_problem(
                    _p(
                        code="zouit_heritage",
                        severity=Severity.HIGH,
                        title="ЗОУИТ объекта культурного наследия",
                        detail=f"{r.title or 'ЗОУИТ ОКН'}. Документ: {doc}. Ограничения: {restrict}",
                        legal_note=(
                            "Зона регулирования застройки / охранная зона ОКН (ФЗ-73): "
                            "ограничения на новое строительство, реконструкцию, изменение облика, земляные работы. "
                            "Требуются согласования с органом охраны памятников."
                        ),
                        recommendation="Получить выписку о ЗОУИТ и предмете охраны; проверить попадание ОКС/стройки в режим.",
                        source=SRC,
                        glossary=("zouit", "okn", "fz73"),
                    )
                )
            elif any(k in tl for k in energy_keys):
                obj.apply_problem(
                    _p(
                        code="zouit_energy",
                        severity=Severity.HIGH,
                        title="ЗОУИТ энергетики / связи / транспорта",
                        detail=f"{r.title or 'ЗОУИТ энергетики'}. Документ: {doc}. Ограничения: {restrict}",
                        legal_note="Охранные зоны ЛЭП, газопроводов, связи, ж/д запрещают или ограничивают строительство, посадку деревьев, земляные работы.",
                        recommendation="Уточнить охранные зоны и получить согласование сетевиков при стройке.",
                        source=SRC,
                        glossary=("zouit", "ohrannaya_zona"),
                    )
                )
            elif any(k in tl for k in nature_keys):
                obj.apply_problem(
                    _p(
                        code="zouit_nature",
                        severity=Severity.HIGH,
                        title="ЗОУИТ природных территорий",
                        detail=str(r.title or "ЗОУИТ природных территорий"),
                        legal_note="Водоохранные зоны, защитные леса и т.п. — ограничения по ВК РФ, ЛК РФ.",
                        recommendation="Проверить режим соответствующей зоны.",
                        source=SRC,
                        glossary=("zouit", "vodoohrannaya"),
                    )
                )
            elif any(k in tl for k in security_keys):
                obj.apply_problem(
                    _p(
                        code="zouit_security",
                        severity=Severity.HIGH,
                        title="ЗОУИТ охраняемых объектов и безопасности",
                        detail=str(r.title or "ЗОУИТ безопасности"),
                        legal_note="Ограничения в интересах безопасности (промышленная, пограничная и др.).",
                        recommendation="Установить вид зоны и согласования.",
                        source=SRC,
                        glossary=("zouit",),
                    )
                )
            else:
                obj.apply_problem(
                    _p(
                        code="zouit_other",
                        severity=Severity.HIGH,
                        title="Иная ЗОУИТ",
                        detail=f"{r.title or 'ЗОУИТ'}. Ограничения: {restrict}",
                        legal_note="Зона с особыми условиями использования территории (ст. 105 ЗК РФ).",
                        recommendation="Получить выписку о зоне и её режиме.",
                        source=SRC,
                        glossary=("zouit",),
                    )
                )

        if terr:
            nums = "; ".join(filter(None, (r.options.get("reg_numb_border") or r.title for r in terr)))
            obj.apply_problem(
                _p(
                    code="territorial_zone",
                    severity=Severity.INFO,
                    title="Участок в территориальной зоне (ПЗЗ)",
                    detail=f"Номер границы/зоны: {nums or 'выявлена территориальная зона'}.",
                    legal_note="Территориальные зоны определяют градостроительный регламент (ВРИ, предельные параметры).",
                    recommendation="Сверить ВРИ участка с регламентом зоны по ПЗЗ.",
                    source=SRC,
                    glossary=("terr_zona", "pzz", "vri"),
                )
            )

        overlap = [r for r in others if r.overlap]
        adjacent = [r for r in others if not r.overlap]
        if overlap and obj.kind.value == "landplot":
            cns = ", ".join(r.cad_number or "?" for r in overlap)
            obj.apply_problem(
                _p(
                    code="zu_overlap",
                    severity=Severity.CRITICAL,
                    title="Наложение границ с другими ЗУ",
                    detail=f"Геометрия пересекается с ЗУ: {cns}. Возможен межевой спор, ошибка в координатах.",
                    legal_note="Наложение границ — приостановка учёта, судебные споры, риск сделки.",
                    recommendation="Межевание, выкопировка, при необходимости — суд об исправлении кадастровой ошибки.",
                    source=SRC,
                    glossary=("nalozhenie", "mezhevanie", "mezh_spor"),
                )
            )
        if obj.kind.value in ("building", "construction", "unfinished") and obj.parent_plot_number:
            obj.apply_problem(
                _p(
                    code="oks_on_zu",
                    severity=Severity.INFO,
                    title="ОКС расположен на земельном участке",
                    detail=(
                        f"По сведениям ПКК объект расположен в границах ЗУ {obj.parent_plot_number.value}. "
                        "Проблемы участка — в блоке «Связанный земельный участок»."
                    ),
                    legal_note="Единая судьба ЗУ и ОКС (ст. 552 ГК РФ, ст. 35 ЗК РФ) при сделках.",
                    recommendation="Проверить права и ВРИ участка, оформление ОКС.",
                    source=SRC,
                    glossary=("oks", "edinaya_sudba"),
                )
            )
        if adjacent and obj.kind.value == "landplot":
            cns = ", ".join(r.cad_number or "?" for r in adjacent[:8])
            obj.apply_problem(
                _p(
                    code="zu_adjacent",
                    severity=Severity.INFO,
                    title="Смежные земельные участки",
                    detail=f"По ПКК граничит/расположен рядом: {cns}.",
                    legal_note="Смежные землепользователи — участники согласования границ.",
                    recommendation="При уточнении границ — акт согласования со смежниками.",
                    source=SRC,
                    glossary=("smezhnye", "mezhevanie"),
                )
            )
        if oks and obj.kind.value == "landplot":
            cns = ", ".join(filter(None, (r.cad_number for r in oks)))
            obj.apply_problem(
                _p(
                    code="oks_on_plot",
                    severity=Severity.INFO,
                    title="ОКС на участке",
                    detail=f"По ПКК в границах/на участке расположены ОКС: {cns or 'да'}.",
                    legal_note="Единая судьба ЗУ и ОКС при сделках.",
                    recommendation="Сверить ОКС с техпаспортом/выпиской; проверить оформление.",
                    source=SRC,
                    glossary=("oks", "edinaya_sudba"),
                )
            )

        parts = obj.tabs.get("parts") or []
        real_parts = [p for p in parts if str(p).strip() not in ("", "—", "-")]
        if len(real_parts) >= 3:
            obj.apply_problem(
                _p(
                    code="many_parts",
                    severity=Severity.MEDIUM,
                    title="Много частей объекта",
                    detail=f"Выявлено частей: {len(real_parts)}.",
                    legal_note="Части могут означать обременения (аренда, сервитуты) или помещения.",
                    recommendation="Проверить части в выписке ЕГРН.",
                    source=SRC,
                    glossary=("chasti", "servitut"),
                )
            )

    def _egrn_reminder(self, obj: CadastralObject) -> None:
        obj.apply_problem(
            _p(
                code="need_egrn_rights",
                severity=Severity.INFO,
                title="Ограничения прав в ПКК не видны",
                detail="Аресты, ипотеки, сервитуты, аренда, запреты регистрации в публичной кадастровой карте не раскрываются.",
                legal_note="Полные сведения о правах и ограничениях — только выписка из ЕГРН (раздел 2) и выписка о переходе прав.",
                recommendation="Заказать актуальную выписку ЕГРН и выписку о переходе прав.",
                source=SRC_EGRN,
                glossary=("egrn", "arest", "ipoteka", "servitut"),
            )
        )

    def _ok_base(self, obj: CadastralObject) -> None:
        if obj.kind.value == "landplot" and obj.has_geometry:
            obj.apply_problem(
                _p(
                    code="landplot_ok_base",
                    severity=Severity.OK,
                    title="Земельный участок найден",
                    detail=(
                        f"КН {obj.number.value}; статус: {obj.status or '—'}; "
                        f"категория: {obj.land_category or '—'}; ВРИ: {obj.vri or '—'}; "
                        f"площадь: {obj.area if obj.area is not None else '—'} кв.м."
                    ),
                    source=SRC,
                    glossary=("zu", "vri", "kad_stoimost"),
                )
            )
        if obj.kind.value == "building" and obj.has_geometry:
            obj.apply_problem(
                _p(
                    code="building_ok_base",
                    severity=Severity.OK,
                    title="Объект капитального строительства найден",
                    detail=(
                        f"КН {obj.number.value}; назначение: {obj.purpose or obj.display_name or '—'}; "
                        f"статус: {obj.status or '—'}; площадь: {obj.area or '—'} кв.м."
                    ),
                    source=SRC,
                    glossary=("oks",),
                )
            )

    def _linked_plot(self, obj: CadastralObject) -> None:
        if not obj.linked_plot:
            return
        sev = obj.linked_plot.max_severity()
        if sev not in (Severity.CRITICAL, Severity.HIGH):
            return
        titles = "; ".join(
            p.title for p in obj.linked_plot.problems if p.severity in (Severity.CRITICAL, Severity.HIGH)
        )
        obj.apply_problem(
            _p(
                code="linked_zu_issues",
                severity=Severity.CRITICAL if sev == Severity.CRITICAL else Severity.HIGH,
                title="Проблемы связанного земельного участка",
                detail=(
                    f"ЗУ {obj.linked_plot.number.value}: {titles}. "
                    "ОКС неразрывно связан с участком — ограничения земли действуют на использование объекта."
                ),
                legal_note="Ст. 552 ГК РФ, ст. 35 ЗК РФ: при переходе прав на ОКС переходит и право на ЗУ; обременения участка следуют за объектом.",
                recommendation="Разобрать проблемы участка в блоке «Связанный земельный участок».",
                source=SRC,
                glossary=("edinaya_sudba", "zu"),
            )
        )
