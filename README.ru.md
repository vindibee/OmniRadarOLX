# OmniRadarOLX

*[English version](README.md)*

Telegram-бот для мониторинга объявлений. Сейчас поддерживает OLX.ua; архитектура рассчитана на добавление
других площадок (например, Gumtree Australia) без изменения ядра.

**Стек:** Python 3.12 · aiogram 3 (бот) · FastAPI (API для Mini App) · curl_cffi (парсер) ·
PostgreSQL · SQLAlchemy 2.0 (async) + asyncpg · Alembic · Redis (FSM + кэш выдачи) ·
pydantic-settings · Docker Compose.

## Быстрый старт (Docker)

```bash
cp .env.example .env         # впишите BOT__TOKEN и пароль БД
docker compose up -d --build # db + redis → migrate (alembic upgrade head) → bot + api
docker compose logs -f bot api
curl http://127.0.0.1:8080/api/health
```

В Telegram откройте бота и нажмите «Начать» → «➕ Новый фильтр». Дальше всё управление —
инлайн-кнопками: команды вводить не нужно, а под каждым присланным объявлением есть
«🔗 Открыть объявление» и «🏠 Меню».

## Локальная разработка

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d db                          # только PostgreSQL, порт из DB__PORT на 127.0.0.1
alembic upgrade head
python -m app.main
```

Проверки:

```bash
pytest            # юнит-тесты парсера + интеграционные тесты на PostgreSQL (БД <name>_test)
pytest -m live    # smoke-тест по реальному API OLX: проверить, не изменился ли формат ответа
ruff check app tests
mypy app tests    # strict
```

## Архитектура

```
app/
├── main.py                 # точка входа бота: aiogram-диспетчер + фоновый воркер
├── composition.py          # общая сборка зависимостей для бота и API (DI, без синглтонов)
├── config.py               # pydantic-settings: BOT__*, DB__*, REDIS__*, CACHE__*, BILLING__*,
│                           #   API__*, HTTP__*, PARSER__*, MONITORING__*
├── domain/                 # чистые dataclass-сущности, тарифы и доменные ошибки, без фреймворков
├── api/                    # Web API для Mini App (FastAPI) — только HTTP, логика в services/
│   ├── security.py         #   проверка подписи Telegram initData (без FastAPI, тестируется сама)
│   ├── deps.py             #   зависимости: текущий пользователь, сервисы из app.state
│   ├── schemas.py          #   pydantic-контракт HTTP, отдельный от доменных сущностей
│   └── main.py             #   create_app(): lifespan, CORS, роуты /api/*
├── handlers/               # Presentation бота: aiogram-роутеры, FSM, клавиатуры
├── services/               # Бизнес-логика, общая для бота и API
│   ├── interfaces.py       #   порты: UnitOfWork, репозитории, Notifier, Cache (Protocol)
│   ├── filters.py          #   фильтры поиска: лимиты, права
│   ├── billing.py          #   подписки: триал, продление, проверка доступа
│   ├── presets.py          #   пресеты Mini App и разворачивание их в фильтр
│   ├── monitoring.py       #   цикл: поиск → сохранение → дедупликация → доставка
│   └── parsers/            #   MarketplaceParser, реестр, curl_cffi, OLX.ua, кэш выдачи
├── repositories/           # Data Access: SQLAlchemy-реализации портов + Unit of Work
├── database/               # ORM-модели (JSONB), engine/session, миграции Alembic
├── cache/                  # RedisCache — реализация порта Cache
├── notifications/          # TelegramNotifier — реализация порта Notifier
└── workers/                # фоновый цикл мониторинга с graceful degradation
```

Зависимости направлены внутрь: `handlers → services → domain` и `api → services → domain`.
`repositories`, `cache` и `notifications` реализуют порты из `services/interfaces.py`, а связывает
всё `composition.py` — поэтому бот и API поднимают одни и те же сервисы поверх одной БД.
Глобальных синглтонов нет: в хендлеры сервисы приходят через DI aiogram
(`Dispatcher(filter_service=...)`), в эндпоинты — через `Depends` поверх `app.state`.

### Данные

| Таблица | Назначение |
|---|---|
| `users` | пользователи Telegram (id = Telegram user id), `language_code` — язык интерфейса |
| `filters` | фильтры мониторинга; параметры поиска в `criteria JSONB` |
| `subscriptions` | оплаченные периоды доступа: тариф, срок, платёж |
| `search_presets` | сохранённые формы поиска из Mini App, `UNIQUE (user_id, name)` |
| `listings` | объявления, `UNIQUE (marketplace, external_id)`; характеристики в `attributes JSONB` + GIN-индекс |
| `deliveries` | связь «фильтр — объявление», `PRIMARY KEY (filter_id, listing_id)` |

**Защита от дублей** работает на уровне БД: `INSERT ... ON CONFLICT DO NOTHING` в `deliveries`.
Одно объявление по одному фильтру физически не может быть поставлено в очередь дважды.
Отметка `sent_at` фиксируется после каждого отправленного сообщения, поэтому перезапуск бота не приводит к повторам.

**Что считается «новым».** OLX сортирует выдачу по времени *обновления*, и старые объявления, которые
продавец «поднял», оказываются наверху. Поэтому объявление отправляется, только если оно создано не раньше,
чем был создан фильтр (с запасом `MONITORING__PUBLISH_GRACE_MINUTES`). Остальные запоминаются как
«виденные» (`sent_at` заполняется без отправки).

**Глубина обхода.** По популярному запросу за один интервал может обновиться больше объявлений, чем
помещается на странице выдачи. Поэтому парсер листает страницы, пока не дойдёт до объявлений старше
границы `since` (последняя проверка фильтра минус grace), но не больше `PARSER__MAX_PAGES`.
Если группа фильтров обслуживается одним запросом, граница берётся по самому отстающему из них.
Новый фильтр границы не задаёт — ему достаточно первой страницы, история всё равно не отправляется.
Упор в лимит страниц виден в логах как предупреждение: это сигнал уменьшить
`MONITORING__INTERVAL_SECONDS`.

### Подписки и доступ

Один период доступа — одна строка в `subscriptions`. Оплата во время действующей подписки
не сжигает остаток: новый период начинается с конца предыдущего, а `Access.until` показывает
конец всей цепочки. Тарифы (`app/domain/tariffs.py`): день, неделя, месяц (−10%), год (−30%);
цена считается от `BILLING__DAY_PRICE_*`, поэтому прайс меняется настройкой, а не кодом.

Два инварианта держит БД, а не код:

- пробный период выдаётся один раз — частичный уникальный индекс `(user_id) WHERE is_trial`,
  поэтому два одновременных нажатия «Демо-доступ» не создадут два триала;
- повторный вебхук провайдера не продлевает доступ дважды — уникальный
  `(payment_provider, payment_id)` плюс проверка в `BillingService.activate_paid`.

### Mini App API

`GET /api/health`, `GET /api/me`, `POST /api/trial`, `GET|POST /api/presets`,
`DELETE /api/presets/{id}`, `POST /api/presets/{id}/monitor`, `GET|POST /api/filters`,
`DELETE /api/filters/{id}`.

Аутентификация — только по подписи Telegram `initData` (`Authorization: tma <initData>` или
заголовок `X-Telegram-Init-Data`). Подпись проверяется HMAC-SHA256 по схеме Bot API, просроченная
дольше 24 часов отклоняется. Ничему из `initData` до проверки подписи доверять нельзя: подменить
в ней свой `id` на чужой — это одна строка в браузере. Создание фильтров требует активной подписки
(`402`), доменные ошибки превращаются в `400` с текстом, который можно показать пользователю.

### Кэш выдачи (Redis)

`CachingParser` оборачивает любой парсер и подставляется вместо него в реестре — мониторинг
и хендлеры о кэше не знают. Две ситуации закрыты сразу:

- повторный одинаковый запрос в пределах `CACHE__TTL_SECONDS` (90 с) берётся из кэша;
- одновременные одинаковые запросы не превращаются в несколько походов: первый берёт блокировку
  (`SET NX`) и парсит, остальные ждут его результат.

Глубина учитывается: обход с более ранним `since` покрывает запрос помельче, обратное — нет,
иначе фильтр получил бы неполную выдачу. Кэш выключается флагом `CACHE__ENABLED=false`.

### Устойчивость

- **HTTP** (`services/parsers/http_client.py`): TLS-отпечаток Chrome через curl_cffi, тайм-ауты,
  повторы с экспоненциальной задержкой и джиттером, учёт `Retry-After`, пересоздание сессии после
  403 или HTML вместо JSON (признак капчи), минимальный интервал между запросами, прокси через `HTTP__PROXY`.
- **Мониторинг:** ошибка одного парсера или фильтра не прерывает цикл; одинаковые запросы разных
  пользователей выполняются один раз; временные ошибки Telegram повторяются до
  `MONITORING__MAX_DELIVERY_ATTEMPTS` раз; если пользователь заблокировал бота, его фильтры отключаются.
- **Воркер:** если падает весь цикл (например, недоступна БД), ошибка логируется, а следующая
  попытка откладывается с растущей паузой. Процесс не завершается.
- **БД:** `pool_pre_ping` — соединения восстанавливаются после перезапуска PostgreSQL.
- **Диалоги:** состояние FSM хранится в Redis (`BOT__FSM_STORAGE=redis`, в Docker Compose включено
  по умолчанию), поэтому незаконченное создание фильтра переживает перезапуск бота. Локально без
  Redis работает `memory`.
- **Смена формата API:** если в ответе площадки есть объявления, но не разобралось ни одного, парсер
  поднимает `ParserResponseError` вместо пустой выдачи — бот не «замолкает» тихо. Актуальность
  разбора проверяется живым тестом `pytest -m live`.

## Как добавить площадку

1. Создайте `app/services/parsers/gumtree_au.py`:

   ```python
   class GumtreeAuParser(MarketplaceParser):
       code = "gumtree_au"
       title = "Gumtree Australia"

       def __init__(self, http: HttpClient) -> None:
           self._http = http

       async def search(self, criteria: SearchCriteria) -> Sequence[Listing]:
           payload = await self._http.get_json(API_URL, params={...})
           return [Listing(marketplace=self.code, external_id=..., url=..., title=..., ...)]

       async def aclose(self) -> None:
           await self._http.aclose()
   ```

2. Зарегистрируйте фабрику в `PARSER_FACTORIES` в `app/main.py`.
3. Добавьте код в `.env`: `ENABLED_MARKETPLACES=["olx_ua","gumtree_au"]`.

Сервисы, БД, миграции и хендлеры менять не нужно: если площадок больше одной, в FSM автоматически
появляется шаг выбора площадки. Специфичные параметры поиска (город, категория) передаются
через `SearchCriteria.extra` и сохраняются в JSONB.

## Известные ограничения и точки роста

- Глубина обхода ограничена `PARSER__MAX_PAGES` (по умолчанию 5 страниц = 200 объявлений за проход).
  Для запроса, по которому за интервал появляется больше, нужен более короткий
  `MONITORING__INTERVAL_SECONDS` — предупреждение об упоре в лимит пишется в лог.
- API OLX (`/api/v1/offers/`) не документирован. Разбор изолирован в `parse_offers`, покрыт тестами
  на фикстуре, живой формат проверяется `pytest -m live`, а полная неразбираемость ответа — ошибка,
  а не пустая выдача. Но изменение семантики отдельного поля тестами не ловится.
- Один экземпляр бота: Redis-хранилище FSM к нескольким репликам готово, но воркер мониторинга
  не разделяет фильтры между процессами — при нескольких репликах объявления будут искаться дважды.
- Не реализовано (следующие шаги): мультиязычность (`locales/` + aiogram-i18n) и выбор языка
  на онбординге, приём платежей Telegram Stars и CryptoBot (`BillingService.activate_paid`
  ждёт вызова от провайдера), middleware проверки подписки в боте, фронтенд Mini App
  и эндпоинт истории найденных объявлений.
