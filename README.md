# OmniRadarOLX

Telegram-бот для мониторинга объявлений. Сейчас поддерживает OLX.ua; архитектура рассчитана на добавление
других площадок (например, Gumtree Australia) без изменения ядра.

**Стек:** Python 3.12 · aiogram 3 · curl_cffi · PostgreSQL · SQLAlchemy 2.0 (async) + asyncpg · Alembic ·
Redis (состояние диалогов) · pydantic-settings · Docker Compose.

## Быстрый старт (Docker)

```bash
cp .env.example .env         # впишите BOT__TOKEN и пароль БД
docker compose up -d --build # db + redis → migrate (alembic upgrade head) → bot
docker compose logs -f bot
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
├── main.py                 # корень композиции: создаёт и связывает зависимости (DI), запускает бота и воркер
├── config.py               # pydantic-settings: BOT__*, DB__*, HTTP__*, PARSER__*, MONITORING__*
├── domain/                 # чистые dataclass-сущности и доменные ошибки, без фреймворков
├── handlers/               # Presentation: aiogram-роутеры, FSM, клавиатуры. Только вызовы сервисов
├── services/               # Бизнес-логика
│   ├── interfaces.py       #   порты: UnitOfWork, репозитории, Notifier (Protocol)
│   ├── subscriptions.py    #   сценарии пользователя: фильтры, лимиты, права
│   ├── monitoring.py       #   цикл: поиск → сохранение → дедупликация → доставка
│   └── parsers/            #   контракт MarketplaceParser, реестр, HTTP-клиент curl_cffi, OLX.ua
├── repositories/           # Data Access: SQLAlchemy-реализации портов + Unit of Work
├── database/               # ORM-модели (JSONB), engine/session, миграции Alembic
├── notifications/          # TelegramNotifier — реализация порта Notifier (клавиатуры берёт
│                           #   из handlers/keyboards: это тот же телеграм-слой представления)
└── workers/                # фоновый цикл мониторинга с graceful degradation
```

Зависимости направлены внутрь: `handlers → services → domain`. `repositories` и `notifications` реализуют
порты из `services/interfaces.py`, а связывает всё `main.py`. Глобальных синглтонов нет: сервисы
передаются в хендлеры через DI aiogram (`Dispatcher(subscription_service=...)`).

### Данные

| Таблица | Назначение |
|---|---|
| `users` | пользователи Telegram (id = Telegram user id) |
| `subscriptions` | фильтры; параметры поиска в `criteria JSONB` |
| `listings` | объявления, `UNIQUE (marketplace, external_id)`; характеристики в `attributes JSONB` + GIN-индекс |
| `deliveries` | связь «фильтр — объявление», `PRIMARY KEY (subscription_id, listing_id)` |

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
