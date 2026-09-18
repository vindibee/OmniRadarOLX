# OmniRadarOLX

*[Версия на русском](README.ru.md)*

A Telegram bot that watches marketplace listings and pushes new ones to you. OLX.ua is supported today;
the architecture is built so that another marketplace (Gumtree Australia, for example) can be added
without touching the core.

The whole interface is a Telegram Mini App. In the chat the bot only launches the app and
delivers what it finds.

**Stack:** Python 3.12 · aiogram 3 (bot) · FastAPI (Mini App API) · HTML/JS + Tailwind (Mini App) ·
curl_cffi (scraping) · PostgreSQL · SQLAlchemy 2.0 (async) + asyncpg · Alembic · Redis (search cache
and FSM) · Telegram Stars and CryptoBot (payments) · Docker Compose.

## Quick start (Docker)

```bash
cp .env.example .env         # fill in BOT__TOKEN and the database password
docker compose up -d --build # db + redis → migrate → bot + api + frontend
docker compose logs -f bot api
curl http://127.0.0.1:8080/api/health
```

A Mini App opens over https only, so the frontend goes behind a reverse proxy with a certificate;
put that address into `BOT__WEBAPP_URL` and into @BotFather. After that, `/start` in the chat hands
the user a "📱 Open the app" button.

## Local development

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d db                          # PostgreSQL only, bound to 127.0.0.1:DB__PORT
alembic upgrade head
python -m app.main
```

Checks:

```bash
pytest            # parser unit tests + integration tests against PostgreSQL (database <name>_test)
pytest -m live    # smoke test against the real OLX API: did the response format change?
ruff check app tests
mypy app tests    # strict
```

## How it works

**In the chat** the bot does exactly two things:

1. `/start` → a short greeting and one inline button, "📱 Open the app" (`web_app`). The same
   entry point appears as the Menu Button next to the input field. Any other message gets the
   same reply: the chat is not a menu.
2. Delivers finds: a card with price, location, publication time and buttons for the listing
   itself and for the app.

**Everything else lives in the Mini App:**

1. First launch: language choice (українська / русский / English), a three-step plain-language
   onboarding, and an "Activate the 7-day demo" button.
2. Plan: current status and tariffs (day, week, month −10%, year −30%), paid with Telegram Stars
   (`WebApp.openInvoice`) or CryptoBot.
3. Search: a form — query, price from/to, condition, city, category, filter name.
4. My filters: a list with pause and delete; tapping one opens the history of everything it has
   found, with the publication date and time of each listing.

## Architecture

```
app/                        # Python: bot, Web API, scraper
├── main.py                 # bot entry point: aiogram dispatcher + background worker
├── composition.py          # dependency wiring shared by the bot and the API (DI, no singletons)
├── config.py               # pydantic-settings: BOT__*, DB__*, REDIS__*, CACHE__*, BILLING__*,
│                           #   API__*, HTTP__*, PARSER__*, MONITORING__*
├── domain/                 # plain dataclass entities, tariffs and domain errors, no frameworks
├── api/                    # Mini App Web API (FastAPI) — HTTP only, logic stays in services/
│   ├── security.py         #   Telegram initData signature check (FastAPI-free, unit-tested)
│   ├── deps.py             #   dependencies: current user, services from app.state
│   ├── schemas.py          #   pydantic HTTP contract, separate from domain entities
│   └── main.py             #   create_app(): lifespan, CORS, /api/* routes
├── handlers/               # The bot in chat: /start with the launch button, Stars payments
│   ├── common.py           #   greeting + the single "Open the app" button
│   ├── payments.py         #   pre_checkout + successful_payment → BillingService
│   └── errors.py
├── payments/               # Payment providers: Telegram Stars and CryptoBot
├── services/               # Business logic shared by the bot and the API
│   ├── interfaces.py       #   ports: UnitOfWork, repositories, Notifier, Cache (Protocol)
│   ├── filters.py          #   filters, user language, history of finds
│   ├── billing.py          #   subscriptions: trial, renewal, access check
│   ├── monitoring.py       #   the cycle: search → store → deduplicate → deliver
│   └── parsers/            #   MarketplaceParser contract, registry, curl_cffi, OLX.ua, cache
├── repositories/           # Data access: SQLAlchemy implementations of the ports + Unit of Work
├── database/               # ORM models (JSONB), engine/session, Alembic migrations
├── cache/                  # RedisCache — implements the Cache port
├── notifications/          # TelegramNotifier — implements the Notifier port
└── workers/                # background monitoring loop with graceful degradation

frontend/                   # Mini App: static files served by nginx, which also proxies /api
├── index.html              #   screen markup + Telegram Web Apps API + Tailwind
├── app.js                  #   state and rendering: onboarding, plan, search, history
├── i18n.js                 #   uk / ru / en translations
├── api.js                  #   Web API client: initData header on every request
├── nginx.conf              #   static + proxy_pass /api → api:8080 (one origin, no CORS)
└── Dockerfile

tests/                      # pytest: parser, cache, billing, API, bot entry point
```

Dependencies point inward: `handlers → services → domain` and `api → services → domain`.
`repositories`, `cache` and `notifications` implement the ports declared in `services/interfaces.py`,
and `composition.py` wires everything together — which is why the bot and the API run the very same
services over one database. There are no global singletons: handlers get services through aiogram's DI
(`Dispatcher(filter_service=...)`), endpoints through `Depends` over `app.state`.

### Data

| Table | Purpose |
|---|---|
| `users` | Telegram users (id = Telegram user id); `language_code` is the interface language |
| `filters` | monitoring filters; search parameters live in `criteria JSONB` |
| `subscriptions` | paid access periods: tariff, term, payment |
| `search_presets` | saved search forms from the Mini App, `UNIQUE (user_id, name)` |
| `listings` | listings, `UNIQUE (marketplace, external_id)`; attributes in `attributes JSONB` + GIN index |
| `deliveries` | the filter-to-listing link, `PRIMARY KEY (filter_id, listing_id)` |

**Duplicate protection** is enforced by the database: `INSERT ... ON CONFLICT DO NOTHING` into
`deliveries`. A listing physically cannot be queued twice for the same filter. `sent_at` is committed
after every message that goes out, so restarting the bot never re-sends anything.

**What counts as "new".** OLX sorts results by *update* time, so an old listing the seller bumped shows
up at the top. A listing is therefore only sent if it was created no earlier than the filter itself
(with a `MONITORING__PUBLISH_GRACE_MINUTES` margin). The rest are recorded as "seen" — `sent_at` is
filled in without sending anything.

**How deep the crawl goes.** A popular query can produce more updates in one interval than fit on a
single page of results. The parser therefore pages through the feed until it reaches listings older than
the `since` boundary (the filter's last check minus the grace window), and never beyond
`PARSER__MAX_PAGES`. When one request serves a group of identical filters, the boundary is taken from
the one that lags furthest behind. A brand-new filter sets no boundary — one page is enough for it,
since history is not delivered anyway. Hitting the page limit is logged as a warning: that is the signal
to lower `MONITORING__INTERVAL_SECONDS`.

### Subscriptions and access

One access period is one row in `subscriptions`. Paying while a subscription is still running does not
burn the remainder: the new period starts where the previous one ends, and `Access.until` reports the end
of the whole chain. Tariffs (`app/domain/tariffs.py`): day, week, month (−10%), year (−30%); the price is
derived from `BILLING__DAY_PRICE_*`, so the price list is a setting, not code.

Two invariants are held by the database rather than by code:

- the trial is granted once — a partial unique index `(user_id) WHERE is_trial`, so two simultaneous
  taps on "Demo access" cannot create two trials;
- a repeated provider webhook cannot extend access twice — a unique `(payment_provider, payment_id)`
  plus the check in `BillingService.activate_paid`.

### Mini App and Web API

The frontend is static (`frontend/`) served by nginx, which also proxies `/api` to the `api`
container — so the browser sees a single origin and CORS is not involved. Only the frontend is
exposed, and it belongs behind an HTTPS reverse proxy: Telegram opens a Mini App over https only,
and that address goes into `BOT__WEBAPP_URL` (and into @BotFather).

Endpoints: `GET /api/health`, `GET /api/me`, `PUT /api/me/language`, `POST /api/trial`,
`GET|POST /api/filters`, `POST /api/filters/{id}/toggle`, `DELETE /api/filters/{id}`,
`GET /api/filters/{id}/items`, `POST /api/payments/stars`, `POST /api/payments/cryptobot`,
`POST /api/payments/cryptobot/webhook`.

Authentication is the Telegram `initData` signature and nothing else (`Authorization: tma <initData>`
or the `X-Telegram-Init-Data` header). The signature is verified with HMAC-SHA256 as specified by
the Bot API, and anything older than 24 hours is rejected. Nothing inside `initData` may be trusted
before that check: swapping your own `id` for someone else's is a one-line edit in a browser. The
frontend never sends its own `user_id` — the server takes it from the signed data.

### Payments

- **Telegram Stars.** The Mini App asks the API for an invoice link and opens it via
  `WebApp.openInvoice`; the bot answers `pre_checkout_query` and credits the period on
  `successful_payment`. Telegram's `telegram_payment_charge_id` is stored, so a redelivered update
  cannot extend the subscription twice.
- **CryptoBot.** A USDT invoice through the Crypto Pay API, confirmed by a webhook at
  `/api/payments/cryptobot/webhook` whose signature is checked before the body is parsed. Without
  `BILLING__CRYPTOBOT_TOKEN` the method simply is not offered in the app.

The official `aiocryptopay` wrapper could not be used: it pins `certifi<2024` and an old `pydantic`,
which conflicts with curl_cffi and aiogram 3.15+. Crypto Pay API is a handful of POST requests, so
the client is written directly on aiohttp (`app/payments/cryptobot.py`).

### Search cache (Redis)

`CachingParser` wraps any parser and takes its place in the registry — neither monitoring nor the
handlers know a cache exists. It closes two situations at once:

- a repeated identical query within `CACHE__TTL_SECONDS` (90s) is served from the cache;
- simultaneous identical queries do not become several visits: the first takes a lock (`SET NX`) and
  scrapes, the rest wait for its result.

Crawl depth is respected: a crawl with an earlier `since` covers a shallower request, never the other
way around — otherwise a filter would receive an incomplete feed. Set `CACHE__ENABLED=false` to turn it off.

### Resilience

- **HTTP** (`services/parsers/http_client.py`): Chrome TLS fingerprint via curl_cffi, timeouts, retries
  with exponential backoff and jitter, `Retry-After` support, a fresh session after a 403 or after HTML
  arrives instead of JSON (a captcha tell), a minimum interval between requests, and proxy support
  through `HTTP__PROXY`.
- **Monitoring:** one failing parser or filter does not break the cycle; identical queries from
  different users are executed once; transient Telegram errors are retried up to
  `MONITORING__MAX_DELIVERY_ATTEMPTS` times; if a user blocks the bot, their filters are switched off.
- **Worker:** if the whole cycle fails (an unreachable database, say), the error is logged and the next
  attempt is delayed with a growing pause. The process stays alive.
- **Database:** `pool_pre_ping` — connections recover after PostgreSQL restarts.
- **Dialogs:** FSM state is kept in Redis (`BOT__FSM_STORAGE=redis`, enabled by default in Docker
  Compose), so a half-finished filter survives a bot restart. Locally, without Redis, `memory` works.
- **API format changes:** if a marketplace response contains listings but none of them parse, the parser
  raises `ParserResponseError` instead of returning an empty result — the bot does not go quiet in
  silence. Whether the parsing is still current is checked by the live test, `pytest -m live`.

## Adding a marketplace

1. Create `app/services/parsers/gumtree_au.py`:

   ```python
   class GumtreeAuParser(MarketplaceParser):
       code = "gumtree_au"
       title = "Gumtree Australia"

       def __init__(self, http: HttpClient) -> None:
           self._http = http

       async def search(
           self, criteria: SearchCriteria, *, since: datetime | None = None
       ) -> Sequence[Listing]:
           payload = await self._http.get_json(API_URL, params={...})
           return [Listing(marketplace=self.code, external_id=..., url=..., title=..., ...)]

       async def aclose(self) -> None:
           await self._http.aclose()
   ```

2. Register the factory in `PARSER_FACTORIES` in `app/main.py`.
3. Add the code to `.env`: `ENABLED_MARKETPLACES=["olx_ua","gumtree_au"]`.

Services, database, migrations and handlers stay untouched: as soon as there is more than one
marketplace, the FSM grows a marketplace-picking step on its own. Marketplace-specific search
parameters (city, category) travel in `SearchCriteria.extra` and are stored as JSONB.

## Known limitations and room to grow

- Crawl depth is capped by `PARSER__MAX_PAGES` (5 pages = 200 listings per pass by default). A query
  that produces more than that within one interval needs a shorter `MONITORING__INTERVAL_SECONDS` —
  hitting the cap is written to the log as a warning.
- The OLX API (`/api/v1/offers/`) is undocumented. Parsing is isolated in `parse_offers`, covered by
  fixture-based tests, checked against the live format by `pytest -m live`, and a wholly unparsable
  response is an error rather than an empty result. A change in the meaning of a single field, however,
  is not something the tests can catch.
- Single instance: the Redis FSM storage is ready for several replicas, but the monitoring worker does
  not split filters across processes — with more than one replica the same searches would run twice.
- City and category in the search form are numeric IDs copied from an OLX link: proper dropdowns
  need OLX's region list and category tree, which is the next step.
- CryptoBot is written against the Crypto Pay API docs but has never run against a real token:
  only the webhook signature check and body parsing are covered by tests.
- Translations live in `frontend/i18n.js` and cover the Mini App. The bot's own texts (greeting,
  payment confirmation) are still Russian only and deserve the same treatment.
