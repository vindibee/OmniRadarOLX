# OmniRadarOLX

*[Версия на русском](README.ru.md)*

A Telegram bot that watches marketplace listings and pushes new ones to you. OLX.ua is supported today;
the architecture is built so that another marketplace (Gumtree Australia, for example) can be added
without touching the core.

**Stack:** Python 3.12 · aiogram 3 · curl_cffi · PostgreSQL · SQLAlchemy 2.0 (async) + asyncpg · Alembic ·
Redis (dialog state) · pydantic-settings · Docker Compose.

## Quick start (Docker)

```bash
cp .env.example .env         # fill in BOT__TOKEN and the database password
docker compose up -d --build # db + redis → migrate (alembic upgrade head) → bot
docker compose logs -f bot
```

Open the bot in Telegram and press "Start" → "➕ New filter". Everything else is inline buttons:
no commands to type, and every listing the bot sends carries "🔗 Open listing" and "🏠 Menu".

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

## Architecture

```
app/
├── main.py                 # composition root: builds and wires dependencies, starts bot and worker
├── config.py               # pydantic-settings: BOT__*, DB__*, HTTP__*, PARSER__*, MONITORING__*
├── domain/                 # plain dataclass entities and domain errors, no frameworks
├── handlers/               # Presentation: aiogram routers, FSM, keyboards. Service calls only
├── services/               # Business logic
│   ├── interfaces.py       #   ports: UnitOfWork, repositories, Notifier (Protocol)
│   ├── subscriptions.py    #   user scenarios: filters, limits, ownership
│   ├── monitoring.py       #   the cycle: search → store → deduplicate → deliver
│   └── parsers/            #   MarketplaceParser contract, registry, curl_cffi HTTP client, OLX.ua
├── repositories/           # Data access: SQLAlchemy implementations of the ports + Unit of Work
├── database/               # ORM models (JSONB), engine/session, Alembic migrations
├── notifications/          # TelegramNotifier — implements the Notifier port (keyboards come from
│                           #   handlers/keyboards: the same Telegram presentation layer)
└── workers/                # background monitoring loop with graceful degradation
```

Dependencies point inward: `handlers → services → domain`. `repositories` and `notifications` implement
the ports declared in `services/interfaces.py`, and `main.py` wires everything together. There are no
global singletons: services reach handlers through aiogram's DI (`Dispatcher(subscription_service=...)`).

### Data

| Table | Purpose |
|---|---|
| `users` | Telegram users (id = Telegram user id) |
| `subscriptions` | filters; search parameters live in `criteria JSONB` |
| `listings` | listings, `UNIQUE (marketplace, external_id)`; attributes in `attributes JSONB` + GIN index |
| `deliveries` | the filter-to-listing link, `PRIMARY KEY (subscription_id, listing_id)` |

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
