"""Командная строка. Тонкий адаптер к use cases."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..application.usecases import AnalyzeCadastralObjects, GetGlossary, ManageHistory, extract_entries
from ..infrastructure.glossary_repo import JsonGlossaryRepository
from ..infrastructure.history_repo import JsonHistoryRepository
from ..infrastructure.nspd import NspdGateway, NspdBlocked
from ..infrastructure.reporting.html import render_html


ROOT = Path(__file__).resolve().parents[3]  # projects/kadastr
DEFAULT_CONFIG = ROOT / "config.json"
DEFAULT_NUMBERS = ROOT / "numbers.txt"
DEFAULT_OUT = ROOT / "output"
DEFAULT_GLOSSARY = ROOT / "data" / "glossary.json"


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"! config {path}: {e}", file=sys.stderr)
        return {}


def main(argv=None) -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    p = argparse.ArgumentParser(prog="kadastr", description="Анализ кадастровых номеров (ПКК/НСПД)")
    p.add_argument("numbers", nargs="*", help="Кадастровые номера (можно КН | имя)")
    p.add_argument("-f", "--file", help="Файл со списком КН")
    p.add_argument("-o", "--out", help="Каталог результатов")
    p.add_argument("--proxy")
    p.add_argument("--delay", type=float)
    p.add_argument("--retries", type=int)
    p.add_argument("--no-open", action="store_true")
    p.add_argument("--name", help="Имя для единственного КН")
    p.add_argument("--history", action="store_true", help="Показать историю и выйти")
    p.add_argument("--glossary", action="store_true", help="Показать справочник и выйти")
    args = p.parse_args(argv)

    config = load_config(DEFAULT_CONFIG)
    out_raw = args.out or config.get("output_dir") or str(DEFAULT_OUT)
    out_dir = Path(out_raw)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_raw
    out_dir.mkdir(parents=True, exist_ok=True)

    glossary_path = Path(config.get("glossary_file") or DEFAULT_GLOSSARY)
    if not glossary_path.is_absolute():
        glossary_path = ROOT / glossary_path
    if args.history:
        uc = ManageHistory(JsonHistoryRepository(ROOT / "data" / "history.json"))
        env = uc.list_envelope()
        print(json.dumps(env.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.glossary:
        uc = GetGlossary(JsonGlossaryRepository(glossary_path))
        env = uc.execute_envelope()
        print(json.dumps(env.to_dict(), ensure_ascii=False, indent=2))
        return 0

    text = " ".join(args.numbers) if args.numbers else ""
    if not text:
        file = Path(args.file or config.get("numbers_file") or DEFAULT_NUMBERS)
        if not file.is_absolute():
            file = ROOT / file
        if not file.exists():
            print(
                "Нет номеров. Укажите аргументами или создайте numbers.txt.\n"
                "Пример: python run.py 50:14:0000000:156789 | мой участок",
                file=sys.stderr,
            )
            return 2
        text = file.read_text(encoding="utf-8")
    entries = extract_entries(text)
    if args.name and entries:
        if len(entries) == 1:
            entries = [(entries[0][0], args.name)]
        else:
            entries[-1] = (entries[-1][0], args.name)
    if not entries:
        print("Кадастровые номера не распознаны.", file=sys.stderr)
        return 2
    numbers = [cn for cn, _ in entries]
    names = {cn: name for cn, name in entries if name}
    print(f"Номеров: {len(numbers)}")
    for cn, name in entries:
        print(f"  - {cn}" + (f" ({name})" if name else ""))

    delay = args.delay if args.delay is not None else float(config.get("delay_ms", 700) or 700) / 1000.0
    retries = args.retries if args.retries is not None else int(config.get("retries", 3))
    proxy = args.proxy or config.get("proxy")

    gateway = NspdGateway(delay=delay, retries=retries, proxy=proxy)
    history = JsonHistoryRepository(ROOT / "data" / "history.json")
    usecase = AnalyzeCadastralObjects(gateway, history=history)

    def progress(i, n, cn):
        print(f"[{i}/{n}] {cn} ...", flush=True)

    # progress через обёртку execute — упростим: печатаем до запроса
    print("Запрос к НСПД...", flush=True)
    run = usecase.execute(numbers, names=names)

    json_path = out_dir / "report.json"
    html_path = out_dir / "report.html"
    json_path.write_text(
        json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    html_path.write_text(render_html(run), encoding="utf-8")

    print("\n=== ИТОГИ ===")
    for o in run.objects:
        c = o.counts()
        flags = []
        if c.get("critical"):
            flags.append(f"критично {c['critical']}")
        if c.get("high"):
            flags.append(f"существенно {c['high']}")
        if c.get("medium"):
            flags.append(f"средне {c['medium']}")
        if o.error:
            flags.append(f"ошибка: {o.error[:80]}")
        print(f"{o.display_name or o.number.value}: {'найден' if o.found else 'НЕ НАЙДЕН'}; {'; '.join(flags) or 'без критичных замечаний'}")
        for pr in sorted(o.problems, key=lambda x: x.severity.rank):
            if pr.severity.value in ("critical", "high"):
                print(f"    [{pr.severity.value.upper()}] {pr.title}")
        if o.linked_plot:
            zc = o.linked_plot.counts()
            print(
                f"    Связанный ЗУ {o.linked_plot.number.value}: "
                f"критично {zc.get('critical', 0)}; существенно {zc.get('high', 0)}"
            )
            for pr in sorted(o.linked_plot.problems, key=lambda x: x.severity.rank):
                if pr.severity.value in ("critical", "high"):
                    print(f"      [ЗУ {pr.severity.value.upper()}] {pr.title}")

    print(f"\nJSON: {json_path}\nHTML: {html_path}")
    if not args.no_open:
        import webbrowser

        try:
            webbrowser.open(html_path.resolve().as_uri())
        except Exception:
            pass
    return 0
