"""Домен аутентификации: пользователь, сессия, порты."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class User:
    id: int | str
    login: str
    display_name: str | None = None
    provider: str = "local"  # local | yandex | vk | mail
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "login": self.login,
            "display_name": self.display_name or self.login,
            "provider": self.provider,
            "created_at": self.created_at,
        }

    @property
    def title(self) -> str:
        return self.display_name or self.login


@dataclass(frozen=True, slots=True)
class Session:
    token: str
    user_id: int | str
    expires_at: str


@runtime_checkable
class UserRepository(Protocol):
    def get_by_login(self, login: str, provider: str = "local") -> User | None: ...

    def get(self, user_id: int | str) -> User | None: ...

    def upsert(self, user: User, password: str | None = None) -> User: ...

    def verify_password(self, login: str, password: str) -> User | None: ...

    def count(self) -> int: ...


@runtime_checkable
class SessionStore(Protocol):
    def create(self, user_id: int | str, ttl_seconds: int = 604800) -> Session: ...

    def get(self, token: str) -> Session | None: ...

    def delete(self, token: str) -> bool: ...


@runtime_checkable
class OAuthProvider(Protocol):
    name: str

    def enabled(self) -> bool: ...

    def authorize_url(self, state: str, redirect_uri: str) -> str: ...

    def exchange_code(self, code: str, redirect_uri: str) -> User | None: ...
