"""Лёгкий HTTP API + статический UI на stdlib. Без внешних фреймворков.

Эндпоинты:
  GET  /                     — UI
  GET  /api/health
  GET  /api/glossary[?code=]
  GET  /api/analyze?cn=50:14:...&cn=...
  POST /api/analyze          {"numbers": ["50:14:..."]}
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..application.usecases import (
    AnalyzeCadastralObjects,
    GetGlossary,
    ManageHistory,
    SearchByText,
    extract_entries,
)
from ..infrastructure.cache_repo import JsonCacheRepository
from ..infrastructure.glossary_repo import JsonGlossaryRepository
from ..infrastructure.history_repo import JsonHistoryRepository
from ..infrastructure.nspd import NspdBlocked, NspdGateway

ROOT = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).resolve().parent / "static"
GLOSSARY_PATH = ROOT / "data" / "glossary.json"
HISTORY_PATH = ROOT / "data" / "history.json"
CACHE_PATH = ROOT / "data" / "cache.json"
CACHE_TTL = 86400  # 24 часа


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def make_handler(*, delay: float, retries: int, proxy: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kadastr-api/1.0"

        def log_message(self, fmt, *args):  # тихий лог
            sys.stderr.write("[http] " + fmt % args + "\n")

        def _send_json(self, payload, status=200):
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str, status=200):
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html", "/ui"):
                return self._send_file("index.html")
            if u.path == "/api/health":
                return self._send_json({"ok": True, "data": {"status": "up"}, "error": None, "meta": {}})
            if u.path == "/api/history":
                uc = ManageHistory(JsonHistoryRepository(HISTORY_PATH))
                return self._send_json(uc.list_envelope().to_dict())
            if u.path == "/api/search":
                qs = parse_qs(u.query)
                q = (qs.get("q") or qs.get("query") or [""])[0]
                uc = SearchByText(NspdGateway(delay=delay, retries=retries, proxy=proxy))
                env = uc.execute_envelope(q)
                return self._send_json(env.to_dict(), 200 if env.ok else 400)
            if u.path == "/api/glossary":
                qs = parse_qs(u.query)
                code = (qs.get("code") or [None])[0]
                uc = GetGlossary(JsonGlossaryRepository(GLOSSARY_PATH))
                env = uc.execute_envelope(code)
                return self._send_json(env.to_dict(), 200 if env.ok else 404)
            if u.path == "/api/analyze":
                qs = parse_qs(u.query)
                cns = qs.get("cn") or qs.get("numbers") or []
                text = " ".join(cns)
                entries = extract_entries(text)
                if not entries:
                    return self._send_json(
                        {"ok": False, "data": None, "error": {"code": "bad_request", "message": "Укажите кадастровые номера: ?cn=50:14:0000000:156789"}, "meta": {}},
                        400,
                    )
                refresh = (qs.get("refresh") or [""])[0] in ("1", "true", "yes")
                return self._analyze(entries, refresh=refresh)
            if u.path == "/api/report.html":
                # не кэшируем — отчёт по последнему анализу не храним в API-режиме
                return self._send_json(
                    {"ok": False, "error": {"code": "not_found", "message": "Используйте /api/analyze"}, "data": None, "meta": {}},
                    404,
                )
            return self._send_json(
                {"ok": False, "data": None, "error": {"code": "not_found", "message": "Нет такого пути"}, "meta": {}},
                404,
            )

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/api/history":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    body = json.loads(raw.decode("utf-8", "replace") or "{}")
                except Exception as e:
                    return self._send_json(
                        {"ok": False, "data": None, "error": {"code": "bad_json", "message": str(e)}, "meta": {}},
                        400,
                    )
                cn = str(body.get("cn") or "")
                name = body.get("name")
                uc = ManageHistory(JsonHistoryRepository(HISTORY_PATH))
                env = uc.save_envelope(cn, name)
                return self._send_json(env.to_dict(), 200 if env.ok else 400)
            if u.path != "/api/analyze":
                return self._send_json(
                    {"ok": False, "data": None, "error": {"code": "not_found", "message": "Нет такого пути"}, "meta": {}},
                    404,
                )
            try:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw.decode("utf-8", "replace") or "{}")
            except Exception as e:
                return self._send_json(
                    {"ok": False, "data": None, "error": {"code": "bad_json", "message": str(e)}, "meta": {}},
                    400,
                )
            # поддержим и строки «КН | имя», и объекты {cn, name}
            raw_items = body.get("numbers") or body.get("items") or []
            entries: list[tuple[str, str | None]] = []
            for it in raw_items:
                if isinstance(it, dict):
                    cn = str(it.get("cn") or "")
                    name = it.get("name") or None
                    if cn:
                        entries.append((cn, name))
                else:
                    entries.extend(extract_entries(str(it)))
            # дедуп по КН, последнее имя выигрывает
            names: dict[str, str | None] = {}
            order: list[str] = []
            for cn, name in entries:
                if cn not in names:
                    order.append(cn)
                names[cn] = name or names.get(cn)
            if not order:
                return self._send_json(
                    {"ok": False, "data": None, "error": {"code": "bad_request", "message": "Поле numbers: список КН или КН | имя"}, "meta": {}},
                    400,
                )
            refresh = bool(body.get("refresh"))
            return self._analyze(list(zip(order, [names[c] for c in order])), refresh=refresh)

        def do_DELETE(self):
            u = urlparse(self.path)
            if u.path == "/api/history":
                qs = parse_qs(u.query)
                cn = (qs.get("cn") or [""])[0]
                uc = ManageHistory(JsonHistoryRepository(HISTORY_PATH))
                env = uc.delete_envelope(cn)
                return self._send_json(env.to_dict(), 200 if env.ok else 404)
            return self._send_json(
                {"ok": False, "data": None, "error": {"code": "not_found", "message": "Нет такого пути"}, "meta": {}},
                404,
            )

        def do_PATCH(self):
            u = urlparse(self.path)
            if u.path == "/api/history":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    body = json.loads(raw.decode("utf-8", "replace") or "{}")
                except Exception as e:
                    return self._send_json(
                        {"ok": False, "data": None, "error": {"code": "bad_json", "message": str(e)}, "meta": {}},
                        400,
                    )
                uc = ManageHistory(JsonHistoryRepository(HISTORY_PATH))
                env = uc.rename_envelope(str(body.get("cn") or ""), body.get("name"))
                return self._send_json(env.to_dict(), 200 if env.ok else 400)
            return self._send_json(
                {"ok": False, "data": None, "error": {"code": "not_found", "message": "Нет такого пути"}, "meta": {}},
                404,
            )

        def _analyze(self, entries, *, refresh: bool = False):
            # entries: список (cn, name|None) или (cn, name) из zip
            names: dict[str, str | None] = {}
            cns: list[str] = []
            for item in entries:
                if isinstance(item, (list, tuple)):
                    cn, name = item[0], item[1]
                else:
                    cn, name = str(item), None
                if cn not in names:
                    cns.append(cn)
                names[cn] = name
            gateway = NspdGateway(delay=delay, retries=retries, proxy=proxy)
            history = JsonHistoryRepository(HISTORY_PATH)
            cache = JsonCacheRepository(CACHE_PATH, ttl_seconds=CACHE_TTL)
            uc = AnalyzeCadastralObjects(gateway, history=history, cache=cache)
            try:
                env = uc.execute_envelope(cns, names=names, refresh=refresh)
            except NspdBlocked as e:
                return self._send_json(
                    {"ok": False, "data": None, "error": {"code": "blocked", "message": str(e)}, "meta": {}},
                    503,
                )
            except Exception as e:
                return self._send_json(
                    {"ok": False, "data": None, "error": {"code": "internal", "message": str(e)}, "meta": {}},
                    500,
                )
            status = 200 if env.ok else 502
            return self._send_json(env.to_dict(), status)

        def _send_file(self, name: str):
            path = STATIC / name
            if not path.exists():
                return self._send_json(
                    {"ok": False, "data": None, "error": {"code": "not_found", "message": f"Нет файла {name}"}, "meta": {}},
                    404,
                )
            return self._send_html(path.read_text(encoding="utf-8"))

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, **gateway_kwargs) -> None:
    handler = make_handler(**gateway_kwargs)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Kadastr API/UI: http://{host}:{port}/  (Ctrl+C — стоп)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    finally:
        httpd.server_close()
