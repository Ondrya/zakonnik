#!/usr/bin/env python
"""API + браузерный UI. Запуск: python serve.py [порт]"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from kadastr.interfaces.http_api import AuthContext, serve  # noqa: E402
from kadastr.infrastructure.auth import SqliteAuthStore, YandexOAuth  # noqa: E402


def main():
    port = 8765
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    cfg = {}
    cfg_path = ROOT / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    delay = float(cfg.get("delay_ms", 700) or 700) / 1000.0
    retries = int(cfg.get("retries", 3))
    proxy = cfg.get("proxy")

    oauth = cfg.get("oauth") or {}
    yandex_cfg = oauth.get("yandex") or {}
    yandex = YandexOAuth(
        client_id=str(yandex_cfg.get("client_id") or ""),
        client_secret=str(yandex_cfg.get("client_secret") or ""),
    )
    store = SqliteAuthStore(ROOT / "data" / "auth.sqlite")
    auth = AuthContext(
        store=store,
        yandex=yandex,
        require_auth=bool(cfg.get("require_auth")),
        allow_local=bool(cfg.get("allow_local", True)),
    )
    serve(host="127.0.0.1", port=port, delay=delay, retries=retries, proxy=proxy, auth=auth)


if __name__ == "__main__":
    main()
