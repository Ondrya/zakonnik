"""Лёгкий HTTP API + статический UI на stdlib.

Эндпоинты:
  GET  /                     — UI
  GET  /api/health
  GET  /api/me
  GET  /api/glossary[?code=]
  GET  /api/analyze?cn=... [&refresh=1]
  POST /api/analyze          {"numbers": [...], "refresh": false}
  GET  /api/history
  POST /api/history          {"cn": "...", "name": "..."}
  PATCH /api/history         {"cn": "...", "name": "..."}
  DELETE /api/history?cn=...
  GET  /api/search?q=адрес
  GET  /auth/yandex/login
  GET  /auth/yandex/callback
  POST /auth/local           {"login": "...", "password": "..."}
  GET  /auth/logout
"""
from __future__ import annotations

import json
import secrets
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
from ..domain.auth import User
from ..infrastructure.auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    SqliteAuthStore,
    YandexOAuth,
    parse_cookies,
)
from ..infrastructure.cache_repo import JsonCacheRepository
from ..infrastructure.glossary_repo import JsonGlossaryRepository
from ..infrastructure.history_repo import JsonHistoryRepository
from ..infrastructure.nspd import NspdBlocked, NspdGateway

ROOT = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).resolve().parent / "static"
GLOSSARY_PATH = ROOT / "data" / "glossary.json"
DATA = ROOT / "data"
CACHE_TTL = 86400  # 24 часа


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _safe_uid(user: User | None) -> str | None:
    if not user:
        return None
    return "".join(c for c in str(user.id) if c.isalnum() or c in "-_") or None


class AuthContext:
    def __init__(self, store: SqliteAuthStore, yandex: YandexOAuth, require_auth: bool, allow_local: bool):
        self.store = store
        self.yandex = yandex
        self.require_auth = require_auth
        self.allow_local = allow_local

    def user_from_headers(self, headers) -> User | None:
        cookies = parse_cookies(headers)
        return self.store.user_from_cookie(cookies.get(SESSION_COOKIE))

    def history_path(self, user: User | None) -> Path:
        uid = _safe_uid(user)
        return DATA / (f"history_{uid}.json" if uid else "history.json")

    def cache_path(self, user: User | None) -> Path:
        uid = _safe_uid(user)
        return DATA / (f"cache_{uid}.json" if uid else "cache.json")


def make_handler(
    *,
    delay: float,
    retries: int,
    proxy: str | None,
    auth: AuthContext,
):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kadastr-api/1.0"

        def log_message(self, fmt, *args):
            sys.stderr.write("[http] " + fmt % args + "\n")

        # ---- ответы ----

        def _send_json(self, payload, status=200, cookie: str | None = None, redirect: str | None = None):
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}",
                )
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str, status=200):
            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str, cookie: str | None = None, clear_cookie: bool = False):
            self.send_response(302)
            self.send_header("Location", location)
            if cookie:
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}",
                )
            if clear_cookie:
                self.send_header(f"Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; Max-Age=0")
            self.end_headers()

        def _unauthorized(self):
            return self._send_json(
                {
                    "ok": False,
                    "data": None,
                    "error": {"code": "unauthorized", "message": "Требуется вход"},
                    "meta": {},
                },
                401,
            )

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        # ---- утилиты ----

        def _user(self) -> User | None:
            return auth.user_from_headers(self.headers)

        def _guard(self) -> User | None:
            user = self._user()
            if auth.require_auth and not user:
                self._unauthorized()
                return None
            return user

        def do_GET(self):
            u = urlparse(self.path)
            if u.path in ("/", "/index.html", "/ui"):
                return self._send_file("index.html")
            if u.path == "/api/health":
                return self._send_json({"ok": True, "data": {"status": "up"}, "error": None, "meta": {}})
            if u.path == "/api/me":
                user = self._user()
                return self._send_json(
                    {
                        "ok": True,
                        "data": {
                            "user": user.to_dict() if user else None,
                            "require_auth": auth.require_auth,
                            "yandex": auth.yandex.enabled(),
                            "allow_local": auth.allow_local,
                        },
                        "error": None,
                        "meta": {},
                    }
                )
            if u.path == "/api/glossary":
                qs = parse_qs(u.query)
                code = (qs.get("code") or [None])[0]
                uc = GetGlossary(JsonGlossaryRepository(GLOSSARY_PATH))
                env = uc.execute_envelope(code)
                return self._send_json(env.to_dict(), 200 if env.ok else 404)
            if u.path == "/api/history":
                user = self._guard()
                if user is None and auth.require_auth:
                    return
                uc = ManageHistory(JsonHistoryRepository(auth.history_path(user)))
                return self._send_json(uc.list_envelope().to_dict())
            if u.path == "/api/search":
                user = self._guard()
                if user is None and auth.require_auth:
                    return
                qs = parse_qs(u.query)
                q = (qs.get("q") or qs.get("query") or [""])[0]
                uc = SearchByText(NspdGateway(delay=delay, retries=retries, proxy=proxy))
                env = uc.execute_envelope(q)
                return self._send_json(env.to_dict(), 200 if env.ok else 400)
            if u.path == "/api/analyze":
                user = self._guard()
                if user is None and auth.require_auth:
                    return
                qs = parse_qs(u.query)
                cns = qs.get("cn") or qs.get("numbers") or []
                text = " ".join(cns)
                entries = extract_entries(text)
                if not entries:
                    return self._send_json(
                        {
                            "ok": False,
                            "data": None,
                            "error": {
                                "code": "bad_request",
                                "message": "Укажите кадастровые номера: ?cn=50:14:0000000:156789",
                            },
                            "meta": {},
                        },
                        400,
                    )
                refresh = (qs.get("refresh") or [""])[0] in ("1", "true", "yes")
                return self._analyze(entries, user=user, refresh=refresh)
            if u.path == "/auth/yandex/login":
                if not auth.yandex.enabled():
                    return self._send_json(
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "oauth_disabled", "message": "Яндекс OAuth не настроен"},
                            "meta": {},
                        },
                        400,
                    )
                state = secrets.token_urlsafe(24)
                auth.store.put_oauth_state(state)
                redirect_uri = f"{self._base_url()}/auth/yandex/callback"
                return self._redirect(auth.yandex.authorize_url(state, redirect_uri))
            if u.path == "/auth/yandex/callback":
                qs = parse_qs(u.query)
                code = (qs.get("code") or [""])[0]
                state = (qs.get("state") or [""])[0]
                if not code or not state or not auth.store.pop_oauth_state(state):
                    return self._send_json(
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "oauth_state", "message": "Некорректный state/code"},
                            "meta": {},
                        },
                        400,
                    )
                redirect_uri = f"{self._base_url()}/auth/yandex/callback"
                user = auth.yandex.exchange_code(code, redirect_uri)
                if not user:
                    return self._send_json(
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "oauth_failed", "message": "Не удалось войти через Яндекс"},
                            "meta": {},
                        },
                        401,
                    )
                user = auth.store.upsert(user)
                sess = auth.store.create_session(user.id)
                return self._redirect("/", cookie=sess.token)
            if u.path == "/auth/logout":
                cookies = parse_cookies(self.headers)
                token = cookies.get(SESSION_COOKIE)
                if token:
                    auth.store.delete_session(token)
                return self._redirect("/", clear_cookie=True)
            if u.path == "/api/report.html":
                return self._send_json(
                    {
                        "ok": False,
                        "error": {"code": "not_found", "message": "Используйте /api/analyze"},
                        "data": None,
                        "meta": {},
                    },
                    404,
                )
            return self._send_json(
                {"ok": False, "data": None, "error": {"code": "not_found", "message": "Нет такого пути"}, "meta": {}},
                404,
            )

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == "/auth/local":
                if not auth.allow_local:
                    return self._send_json(
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "local_disabled", "message": "Локальный вход отключён"},
                            "meta": {},
                        },
                        400,
                    )
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
                except Exception as e:
                    return self._send_json(
                        {"ok": False, "data": None, "error": {"code": "bad_json", "message": str(e)}, "meta": {}},
                        400,
                    )
                login = str(body.get("login") or "").strip()
                password = str(body.get("password") or "")
                if not login or not password:
                    return self._send_json(
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "bad_request", "message": "Нужны login и password"},
                            "meta": {},
                        },
                        400,
                    )
                user = auth.store.verify_password(login, password)
                if not user:
                    # первый пользователь или регистрация, если разрешена
                    if auth.store.count() == 0 or body.get("register"):
                        from ..domain.auth import User as U

                        user = auth.store.upsert(U(id=0, login=login, display_name=login, provider="local"), password=password)
                    else:
                        return self._send_json(
                            {
                                "ok": False,
                                "data": None,
                                "error": {"code": "bad_credentials", "message": "Неверный логин или пароль"},
                                "meta": {},
                            },
                            401,
                        )
                sess = auth.store.create_session(user.id)
                return self._send_json(
                    {"ok": True, "data": {"user": user.to_dict()}, "error": None, "meta": {}},
                    cookie=sess.token,
                )
            if u.path == "/api/history":
                user = self._guard()
                if user is None and auth.require_auth:
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
                except Exception as e:
                    return self._send_json(
                        {"ok": False, "data": None, "error": {"code": "bad_json", "message": str(e)}, "meta": {}},
                        400,
                    )
                uc = ManageHistory(JsonHistoryRepository(auth.history_path(user)))
                env = uc.save_envelope(str(body.get("cn") or ""), body.get("name"))
                return self._send_json(env.to_dict(), 200 if env.ok else 400)
            if u.path == "/api/analyze":
                user = self._guard()
                if user is None and auth.require_auth:
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
                except Exception as e:
                    return self._send_json(
                        {"ok": False, "data": None, "error": {"code": "bad_json", "message": str(e)}, "meta": {}},
                        400,
                    )
                raw_items = body.get("numbers") or body.get("items") or []
                entries: list[tuple[str, str | None]] = []
                for it in raw_items:
                    if isinstance(it, dict):
                        cn = str(it.get("cn") or "")
                        if cn:
                            entries.append((cn, it.get("name") or None))
                    else:
                        entries.extend(extract_entries(str(it)))
                names: dict[str, str | None] = {}
                order: list[str] = []
                for cn, name in entries:
                    if cn not in names:
                        order.append(cn)
                    names[cn] = name or names.get(cn)
                if not order:
                    return self._send_json(
                        {
                            "ok": False,
                            "data": None,
                            "error": {"code": "bad_request", "message": "Поле numbers: список КН или КН | имя"},
                            "meta": {},
                        },
                        400,
                    )
                refresh = bool(body.get("refresh"))
                return self._analyze(
                    list(zip(order, [names[c] for c in order])),
                    user=user,
                    refresh=refresh,
                )
            return self._send_json(
                {"ok": False, "data": None, "error": {"code": "not_found", "message": "Нет такого пути"}, "meta": {}},
                404,
            )

        def do_PATCH(self):
            u = urlparse(self.path)
            if u.path == "/api/history":
                user = self._guard()
                if user is None and auth.require_auth:
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
                except Exception as e:
                    return self._send_json(
                        {"ok": False, "data": None, "error": {"code": "bad_json", "message": str(e)}, "meta": {}},
                        400,
                    )
                uc = ManageHistory(JsonHistoryRepository(auth.history_path(user)))
                env = uc.rename_envelope(str(body.get("cn") or ""), body.get("name"))
                return self._send_json(env.to_dict(), 200 if env.ok else 400)
            return self._send_json(
                {"ok": False, "data": None, "error": {"code": "not_found", "message": "Нет такого пути"}, "meta": {}},
                404,
            )

        def do_DELETE(self):
            u = urlparse(self.path)
            if u.path == "/api/history":
                user = self._guard()
                if user is None and auth.require_auth:
                    return
                qs = parse_qs(u.query)
                cn = (qs.get("cn") or [""])[0]
                uc = ManageHistory(JsonHistoryRepository(auth.history_path(user)))
                env = uc.delete_envelope(cn)
                return self._send_json(env.to_dict(), 200 if env.ok else 404)
            return self._send_json(
                {"ok": False, "data": None, "error": {"code": "not_found", "message": "Нет такого пути"}, "meta": {}},
                404,
            )

        def _analyze(self, entries, *, user: User | None = None, refresh: bool = False):
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
            history = JsonHistoryRepository(auth.history_path(user))
            cache = JsonCacheRepository(auth.cache_path(user), ttl_seconds=CACHE_TTL)
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
            return self._send_json(env.to_dict(), 200 if env.ok else 502)

        def _base_url(self) -> str:
            host = self.headers.get("Host") or "127.0.0.1:8765"
            return f"http://{host}"

        def _send_file(self, name: str):
            path = STATIC / name
            if not path.exists():
                return self._send_json(
                    {
                        "ok": False,
                        "data": None,
                        "error": {"code": "not_found", "message": f"Нет файла {name}"},
                        "meta": {},
                    },
                    404,
                )
            return self._send_html(path.read_text(encoding="utf-8"))

    return Handler


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    delay: float,
    retries: int,
    proxy: str | None,
    auth: AuthContext,
) -> None:
    handler = make_handler(delay=delay, retries=retries, proxy=proxy, auth=auth)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Kadastr API/UI: http://{host}:{port}/  (Ctrl+C — стоп)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")
    finally:
        httpd.server_close()
