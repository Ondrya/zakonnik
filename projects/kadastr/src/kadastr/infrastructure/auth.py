"""SQLite-аутентификация: пользователи, сессии, локальный вход, Яндекс OAuth."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ..domain.auth import Session, User


SESSION_COOKIE = "kadastr_session"
SESSION_TTL = 7 * 86400


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _expires(ttl: int = SESSION_TTL) -> str:
    return (dt.datetime.now() + dt.timedelta(seconds=ttl)).isoformat(timespec="seconds")


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{salt.hex()}:{h.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or ":" not in stored:
        return False
    salt_hex, h_hex = stored.split(":", 1)
    try:
        h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 200_000)
        return hmac.compare_digest(h.hex(), h_hex)
    except Exception:
        return False


class SqliteAuthStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  login TEXT NOT NULL,
                  provider TEXT NOT NULL DEFAULT 'local',
                  display_name TEXT,
                  password_hash TEXT,
                  created_at TEXT,
                  UNIQUE(login, provider)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                  token TEXT PRIMARY KEY,
                  user_id INTEGER NOT NULL,
                  expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS oauth_states (
                  state TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL
                );
                """
            )

    # ---- users ----

    def _row_to_user(self, row: sqlite3.Row) -> User:
        return User(
            id=row["id"],
            login=row["login"],
            display_name=row["display_name"],
            provider=row["provider"],
            created_at=row["created_at"],
        )

    def get_by_login(self, login: str, provider: str = "local") -> User | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM users WHERE login=? AND provider=?", (login, provider)
            ).fetchone()
            return self._row_to_user(row) if row else None

    def get(self, user_id: int | str) -> User | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return self._row_to_user(row) if row else None

    def upsert(self, user: User, password: str | None = None) -> User:
        with self._conn() as c:
            now = _now()
            c.execute(
                """
                INSERT INTO users (login, provider, display_name, password_hash, created_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(login, provider) DO UPDATE SET
                  display_name=excluded.display_name,
                  password_hash=COALESCE(excluded.password_hash, users.password_hash)
                """,
                (
                    user.login,
                    user.provider,
                    user.display_name or user.login,
                    hash_password(password) if password else None,
                    now,
                ),
            )
            row = c.execute(
                "SELECT * FROM users WHERE login=? AND provider=?", (user.login, user.provider)
            ).fetchone()
            return self._row_to_user(row)

    def verify_password(self, login: str, password: str) -> User | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM users WHERE login=? AND provider='local'", (login,)
            ).fetchone()
            if not row or not verify_password(password, row["password_hash"]):
                return None
            return self._row_to_user(row)

    def count(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    # ---- sessions ----

    def create_session(self, user_id: int | str, ttl_seconds: int = SESSION_TTL) -> Session:
        token = secrets.token_urlsafe(32)
        exp = _expires(ttl_seconds)
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions (token, user_id, expires_at) VALUES (?,?,?)",
                (token, user_id, exp),
            )
        return Session(token=token, user_id=user_id, expires_at=exp)

    def get_session(self, token: str) -> Session | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
            if not row:
                return None
            if row["expires_at"] < _now():
                c.execute("DELETE FROM sessions WHERE token=?", (token,))
                return None
            return Session(token=row["token"], user_id=row["user_id"], expires_at=row["expires_at"])

    def delete_session(self, token: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM sessions WHERE token=?", (token,))
            return cur.rowcount > 0

    def user_from_cookie(self, cookie_value: str | None) -> User | None:
        if not cookie_value:
            return None
        sess = self.get_session(cookie_value)
        if not sess:
            return None
        return self.get(sess.user_id)

    # ---- oauth state ----

    def put_oauth_state(self, state: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO oauth_states (state, created_at) VALUES (?,?)",
                (state, _now()),
            )

    def pop_oauth_state(self, state: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM oauth_states WHERE state=?", (state,))
            return cur.rowcount > 0


class YandexOAuth:
    name = "yandex"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.auth_url = "https://oauth.yandex.ru/authorize"
        self.token_url = "https://oauth.yandex.ru/token"
        self.info_url = "https://login.yandex.ru/info"

    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        q = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "force_confirm": "no",
            }
        )
        return f"{self.auth_url}?{q}"

    def exchange_code(self, code: str, redirect_uri: str) -> User | None:
        body = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": redirect_uri,
            }
        ).encode()
        req = urllib.request.Request(
            self.token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            tok = json.loads(r.read())
        access = tok.get("access_token")
        if not access:
            return None
        req2 = urllib.request.Request(
            self.info_url, headers={"Authorization": f"OAuth {access}"}
        )
        with urllib.request.urlopen(req2, timeout=20) as r:
            info = json.loads(r.read())
        login = str(info.get("login") or info.get("id") or "")
        if not login:
            return None
        display = info.get("display_name") or info.get("real_name") or login
        return User(
            id=str(info.get("id") or login),
            login=login,
            display_name=display,
            provider="yandex",
        )


def parse_cookies(headers) -> dict[str, str]:
    raw = headers.get("Cookie") or headers.get("cookie") or ""
    out: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out
