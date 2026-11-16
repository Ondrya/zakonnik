# kadastr — парсер кадастровой информации с подсветкой проблем

Публичная ПКК/НСПД → доменная модель (DDD / Clean Architecture) → отчёт, JSON API, браузерный UI.

## Быстрый старт

```powershell
# CLI
python run.py 50:14:0000000:156789
python run.py                 # номера из numbers.txt

# API + UI (http://127.0.0.1:8765)
python serve.py
python serve.py 9000
```

Номера меняются в `numbers.txt` или передаются аргументами / в поле UI.
Можно сразу с именем:

```
50:14:0000000:156789 | мой участок
50:14:0000000:156713 | дом родителей
```

История поиска — `data/history.json` (вне git). В UI: клик по записи — повторный анализ, ✎ — переименовать, ✕ — удалить.
CLI: `python run.py --history`, `python run.py --name "лот 15" 50:14:0000000:156789`.

Кэш проверок — `data/cache.json`, TTL 24 ч. Повторный запрос того же КН — из кэша. Принудительно: галочка в UI, `?refresh=1`, `python run.py --refresh`.

Поиск по адресу: начните печатать адрес (например «Москва, Старая площадь, 4») — подсказки из геокодера НСПД и найденных ОНС. Фильтр проблем по критичности — чипсы над деталями. Tooltip на карте — краткие проблемы и кнопка «Подробнее».

Результаты CLI: `output/report.html`, `output/report.json`.

## Что подсвечивается

Критично: не найден; нет границ; временный/снят; наложение ЗУ; красные линии; затопление; ООПТ; сам объект — ОКН.
Существенно: ВРИ не установлен; ЗОУИТ (ОКН, энергетика, природа, безопасность); проблемы связанного ЗУ.
Средне: КС массовым методом; нет площади/КС; ранее учтённый.
Инфо: смежные ЗУ; территориальная зона; ОКС на ЗУ; напоминание о выписке ЕГРН.

Аресты, ипотеки, сервитуты в ПКК не публикуются — парсер об этом напоминает.

## Архитектура

```
src/kadastr/
  domain/           # сущности, VO, правила проблем, порты, глоссарий
    entities.py     # агрегат CadastralObject, AnalysisRun
    value_objects.py# CadastralNumber, Problem, Severity, GeoGeometry…
    problems.py     # ProblemPolicy — правила подсветки
    gateways.py     # порты CadastralGateway, GlossaryRepository
    geometry.py     # чистая геометрия (без I/O)
    glossary.py     # модель терминов
  application/      # use cases + API-конверт {ok, data, error, meta}
    usecases.py     # AnalyzeCadastralObjects, GetGlossary
    dtos.py
  infrastructure/   # адаптеры
    nspd/
      http.py       # HTTP-клиент НСПД (WAF, ретраи)
      acl.py        # Антикоррупционный слой: НСПД → домен
      gateway.py    # реализация CadastralGateway
    reporting/html.py
    glossary_repo.py
  interfaces/
    cli.py          # командная строка
    http_api.py     # REST + раздача UI
    static/index.html
```

### Правило зависимостей

`interfaces → application → domain ← infrastructure`

Домен не знает про НСПД, HTTP и файлы. Use cases зависят от порта `CadastralGateway`.

### Замена парсера (ACL)

Если НСПД развалится или появится другой источник (выписки ЕГРН, файл, мок):

1. Реализовать `CadastralGateway` в новом адаптере.
2. Перевести сырые данные в агрегат через свой ACL.
3. Подставить в `AnalyzeCadastralObjects(gateway)`.

Домен, правила проблем, UI и API не меняются.

## API

```
GET  /api/health
GET  /api/glossary[?code=vri]
GET  /api/analyze?cn=50:14:0000000:156789
POST /api/analyze  {"numbers": ["50:14:0000000:156789 | мой участок"]}
GET  /api/history
POST /api/history  {"cn": "...", "name": "мой участок"}
PATCH /api/history {"cn": "...", "name": "лот 15"}
DELETE /api/history?cn=...
GET  /             # UI
```

Ответ:

```json
{
  "ok": true,
  "data": { "created_at": "...", "objects": [...], "warnings": [] },
  "error": null,
  "meta": { "api_version": "1.0", "source": "nspd" }
}
```

## Справочник терминов

`data/glossary.json` — для обычного человека (`for_citizen`) и для юриста
(`for_lawyer` + `regulations`). Показывается в UI и отдаётся API.
Связан с кодами проблем (`related_problems` ↔ `Problem.glossary`).

## Авторизация (issue #5)

По умолчанию вход необязателен (`require_auth: false`) — история общая.

**Локальный вход:** кнопка «Войти» в шапке, логин+пароль. Первый вход с новым логином — регистрация.

**Яндекс OAuth:**
1. Создайте приложение на https://oauth.yandex.ru/
2. Redirect URI: `http://127.0.0.1:8765/auth/yandex/callback`
3. Вставьте `client_id` и `client_secret` в `config.json` → `oauth.yandex`
4. Кнопка «Войти через Яндекс» появится автоматически
5. Не публикуйте `client_secret` в публичный репозиторий

Когда вошли — история и кэш отдельно по пользователю (`history_<id>.json`).
`require_auth: true` — без входа анализ/история недоступны (401).

Сессии: SQLite `data/auth.sqlite` (порт готов к замене на PostgreSQL). VK/Mail — по той же схеме провайдера, когда будут ключи.

## Настройки

`config.json`: `delay_ms`, `retries`, `proxy` (если 403 — российский резидентный IP).

## Тесты

```powershell
python -m unittest discover -s tests -v
```

## Ограничения

- ПКК ≠ выписка ЕГРН
- WAF Росреестра блокирует часть IP
- Не для массового парсинга тысяч номеров
