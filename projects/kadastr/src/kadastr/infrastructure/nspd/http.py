"""Низкоуровневый HTTP-клиент НСПД. Знает про заголовки, WAF, ретраи.

Заменяем. Домен и application его не импортируют.
"""
from __future__ import annotations

import gzip
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


HOST = "nspd.gov.ru"
BASE = f"https://{HOST}"
REFERER = f"{BASE}/map?thematic=PKK"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SSL_CTX = ssl._create_unverified_context()
try:
    SSL_CTX.set_ciphers("ALL:@SECLEVEL=1")
except ssl.SSLError:
    pass


class NspdHttpError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class NspdBlocked(NspdHttpError):
    """403 / WAF."""


class NspdNotFound(NspdHttpError):
    pass


class NspdHttpClient:
    def __init__(
        self,
        *,
        delay: float = 0.7,
        retries: int = 3,
        proxy: str | None = None,
        timeout: float = 30.0,
    ):
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._last_call = 0.0
        handlers: list[urllib.request.BaseHandler] = [
            urllib.request.HTTPSHandler(context=SSL_CTX)
        ]
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        self._opener = urllib.request.build_opener(*handlers)

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": UA,
            "Referer": REFERER,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Accept-Encoding": "gzip",
            "X-TIMEZONE": "Europe/Moscow",
            "Origin": BASE,
        }

    def _throttle(self) -> None:
        wait = self.delay - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def request(
        self,
        path: str,
        *,
        params: dict | None = None,
        method: str = "GET",
        json_body: Any = None,
    ) -> Any:
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = None
        headers = self._headers()
        if json_body is not None:
            data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    ctype = resp.headers.get("Content-Type", "")
                    if "json" in ctype or raw[:1] in (b"{", b"["):
                        return json.loads(raw.decode("utf-8", "replace"))
                    return raw.decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                raw = e.read()
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
                text = (raw or b"").decode("utf-8", "replace")
                if e.code == 404:
                    raise NspdNotFound(f"404 {path}: {text[:200]}", status=404, body=text)
                if e.code == 403 or "Forbidden" in text[:80]:
                    raise NspdBlocked(
                        "Доступ запрещён (403). Вероятна блокировка IP (WAF Росреестра). "
                        "Используйте российский резидентный IP или прокси.",
                        status=403,
                        body=text,
                    )
                if e.code == 429 and attempt < self.retries:
                    time.sleep(2.0)
                    continue
                if e.code >= 500 and attempt < self.retries:
                    time.sleep(1.5)
                    continue
                raise NspdHttpError(f"HTTP {e.code} {path}: {text[:200]}", status=e.code, body=text)
            except Exception as e:
                last_err = e
                if attempt < self.retries:
                    time.sleep(1.5)
                    continue
                raise NspdHttpError(f"Сетевая ошибка {path}: {e}") from e
        raise NspdHttpError(f"Не удалось выполнить {path}: {last_err}")

    # --- конкретные вызовы API ---

    def search(self, query: str, thematic_search_id: int = 1) -> list[dict] | None:
        try:
            data = self.request(
                "/api/geoportal/v2/search/geoportal",
                params={"query": query, "thematicSearchId": thematic_search_id},
            )
        except NspdNotFound:
            return None
        return ((data or {}).get("data") or {}).get("features") or None

    def intersects(self, geom_wgs84: dict, category_ids: list[int]) -> list[dict] | None:
        payload = {
            "categories": [{"id": c} for c in category_ids],
            "geom": {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            **geom_wgs84,
                            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
                        },
                    }
                ],
            },
        }
        try:
            data = self.request(
                "/api/geoportal/v1/intersects",
                params={"typeIntersect": "fullObject"},
                method="POST",
                json_body=payload,
            )
        except NspdNotFound:
            return None
        except NspdHttpError as e:
            if e.status == 400:
                return None
            raise
        return data.get("features") or None

    def tab_values(self, feat: dict, tab_class: str) -> list[str] | None:
        params = self._tab_params(feat, tab_class)
        try:
            data = self.request("/api/geoportal/v1/tab-values-data", params=params)
        except NspdNotFound:
            return None
        return data.get("value")

    @staticmethod
    def _tab_params(feat: dict, tab_class: str) -> dict:
        props = feat.get("properties") or {}
        opts = props.get("options") or {}
        if opts.get("geocoderObject"):
            return {
                "tabClass": tab_class,
                "objdocId": opts.get("objdocId"),
                "registersId": opts.get("registersId"),
            }
        return {
            "tabClass": tab_class,
            "categoryId": props.get("category"),
            "geomId": feat.get("id"),
        }
