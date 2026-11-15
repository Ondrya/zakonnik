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

from ..application.usecases import AnalyzeCadastralObjects, GetGlossary, extract_numbers
from ..infrastructure.glossary_repo import JsonGlossaryRepository
from ..infrastructure.nspd import NspdBlocked, NspdGateway

ROOT = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).resolve().parent / "static"
GLOSSARY_PATH = ROOT / "data" / "glossary.json"


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
                numbers = extract_numbers(text)
                if not numbers:
                    return self._send_json(
                        {"ok": False, "data": None, "error": {"code": "bad_request", "message": "Укажите кадастровые номера: ?cn=50:14:0000000:156789"}, "meta": {}},
                        400,
                    )
                return self._analyze(numbers)
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
            numbers = extract_numbers(" ".join(str(x) for x in (body.get("numbers") or [])))
            if not numbers:
                return self._send_json(
                    {"ok": False, "data": None, "error": {"code": "bad_request", "message": "Поле numbers: список КН"}, "meta": {}},
                    400,
                )
            return self._analyze(numbers)

        def _analyze(self, numbers):
            gateway = NspdGateway(delay=delay, retries=retries, proxy=proxy)
            uc = AnalyzeCadastralObjects(gateway)
            try:
                env = uc.execute_envelope(numbers)
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
