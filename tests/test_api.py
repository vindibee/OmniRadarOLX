"""Mini App API: подпись initData, личный кабинет, фильтры с историей, счета на оплату."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.main import create_app
from app.api.security import InitDataError, build_init_data, parse_init_data
from app.config import BotSettings, Settings
from app.domain.entities import Listing
from app.payments.cryptobot import CryptoBotPayments
from app.payments.stars import build_payload, parse_payload
from app.services.billing import BillingOptions, BillingService, Offer
from app.services.filters import FilterService
from app.services.parsers import ParserRegistry
from tests.conftest import FakeParser, UowFactory

TOKEN = "123456:TEST-TOKEN"
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _init_data(user_id: int = 1, *, auth_date: datetime = NOW, token: str = TOKEN) -> str:
    user = {"id": user_id, "first_name": "Тест", "username": "tester", "language_code": "uk"}
    return build_init_data(
        {
            "auth_date": str(int(auth_date.timestamp())),
            "query_id": "AAEt",
            "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
        },
        token,
    )


# ---------- Подпись initData (без FastAPI) ----------


def test_valid_init_data_identifies_the_user() -> None:
    user = parse_init_data(_init_data(), TOKEN, now=NOW)

    assert (user.id, user.username, user.full_name) == (1, "tester", "Тест")
    assert user.language_code == "uk"


def test_init_data_signed_with_another_token_is_rejected() -> None:
    foreign = _init_data(token="999:SOMEONE-ELSES-TOKEN")

    with pytest.raises(InitDataError):
        parse_init_data(foreign, TOKEN, now=NOW)


def test_tampered_user_id_is_rejected() -> None:
    """Главное, ради чего всё это: подменить свой id на чужой нельзя."""
    raw = _init_data(user_id=1).replace("%22id%22%3A1", "%22id%22%3A2")

    with pytest.raises(InitDataError):
        parse_init_data(raw, TOKEN, now=NOW)


def test_stale_init_data_is_rejected() -> None:
    raw = _init_data(auth_date=NOW - timedelta(days=2))

    with pytest.raises(InitDataError):
        parse_init_data(raw, TOKEN, max_age=timedelta(hours=24), now=NOW)


@pytest.mark.parametrize("raw", ["", "user=%7B%7D", "auth_date=1&hash=deadbeef"])
def test_broken_init_data_is_rejected(raw: str) -> None:
    with pytest.raises(InitDataError):
        parse_init_data(raw, TOKEN, now=NOW)


# ---------- Эндпоинты ----------


class FakeStars:
    """Вместо похода в Bot API за ссылкой на счёт — запоминаем, о чём просили."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, Offer]] = []

    async def create_invoice_link(self, user_id: int, offer: Offer, *, title: str) -> str:
        self.calls.append((user_id, offer))
        return f"https://t.me/invoice/{offer.tariff.value}"


@pytest.fixture
def parser() -> FakeParser:
    return FakeParser()


@pytest.fixture
def api(uow_factory: UowFactory, parser: FakeParser) -> FastAPI:
    """Приложение с теми же сервисами, но на тестовой БД и фейковых зависимостях."""
    settings = Settings(bot=BotSettings(token=SecretStr(TOKEN)))
    app = create_app(settings)
    parsers = ParserRegistry([parser])
    app.state.uow_factory = uow_factory
    app.state.filters = FilterService(uow_factory, parsers, max_filters_per_user=5)
    app.state.billing = BillingService(uow_factory, BillingOptions(), clock=lambda: NOW)
    app.state.stars = FakeStars()
    app.state.cryptobot = None
    return app


@pytest.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    # ASGITransport не запускает lifespan — ресурсы подставлены фикстурой выше.
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        yield client


@pytest.fixture
def headers() -> dict[str, str]:
    return {"Authorization": f"tma {_init_data()}"}


async def test_request_without_init_data_is_unauthorized(client: AsyncClient) -> None:
    assert (await client.get("/api/me")).status_code == 401


async def test_first_visit_asks_for_onboarding_and_shows_the_price_list(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    body = (await client.get("/api/me", headers=headers)).json()

    assert body["needs_onboarding"] is True, "язык в приложении ещё не выбирали"
    assert body["access"]["is_allowed"] is False
    assert body["access"]["trial_available"] is True
    assert [offer["tariff"] for offer in body["offers"]] == ["day", "week", "month", "year"]
    assert body["payment_methods"] == ["stars"], "CryptoBot не настроен — способ не предлагается"


async def test_language_choice_ends_onboarding(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    await client.get("/api/me", headers=headers)

    saved = await client.put("/api/me/language", json={"language_code": "ru"}, headers=headers)
    body = (await client.get("/api/me", headers=headers)).json()

    assert saved.status_code == 200
    assert (body["language_code"], body["needs_onboarding"]) == ("ru", False)


async def test_unsupported_language_is_rejected(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    await client.get("/api/me", headers=headers)

    response = await client.put("/api/me/language", json={"language_code": "fr"}, headers=headers)

    assert response.status_code == 422


async def test_filter_requires_subscription_and_works_after_trial(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    await client.get("/api/me", headers=headers)
    body = {
        "name": "Айфоны до 20к",
        "marketplace": "fake",
        "criteria": {"query": "iphone 13", "price_max": "20000", "extra": {"city_id": 268}},
    }

    denied = await client.post("/api/filters", json=body, headers=headers)
    trial = await client.post("/api/trial", headers=headers)
    allowed = await client.post("/api/filters", json=body, headers=headers)

    assert denied.status_code == 402, "без подписки мониторинг не включается"
    assert trial.status_code == 201 and trial.json()["is_trial"] is True
    assert allowed.status_code == 201
    assert allowed.json()["title"] == "Айфоны до 20к", "имя из формы, а не автозаголовок"
    assert allowed.json()["criteria"]["extra"] == {"city_id": 268}


async def test_second_trial_is_refused(client: AsyncClient, headers: dict[str, str]) -> None:
    await client.get("/api/me", headers=headers)

    first = await client.post("/api/trial", headers=headers)
    second = await client.post("/api/trial", headers=headers)

    assert first.status_code == 201
    assert second.status_code == 400
    assert "Демо" in second.json()["detail"]


async def test_filter_history_shows_what_was_found(
    client: AsyncClient, headers: dict[str, str], parser: FakeParser, uow_factory: UowFactory
) -> None:
    """Раздел «История»: клик по фильтру показывает находки с датой публикации."""
    await client.get("/api/me", headers=headers)
    await client.post("/api/trial", headers=headers)
    created = await client.post(
        "/api/filters",
        json={"marketplace": "fake", "criteria": {"query": "bike"}},
        headers=headers,
    )
    filter_id = created.json()["id"]

    listing = Listing(
        marketplace="fake",
        external_id="b1",
        url="https://fake.example/b1",
        title="Велосипед",
        price=Decimal("3500"),
        published_at=NOW - timedelta(minutes=5),
    )
    async with uow_factory() as uow:
        ids = await uow.listings.upsert_many([listing])
        await uow.deliveries.add_many(filter_id, [ids[("fake", "b1")]])
        await uow.commit()

    items = await client.get(f"/api/filters/{filter_id}/items", headers=headers)

    assert items.status_code == 200
    (item,) = items.json()
    assert item["title"] == "Велосипед"
    assert item["published_at"] is not None and item["found_at"] is not None
    assert item["sent_at"] is None, "ещё не доставлено"


async def test_history_of_someone_elses_filter_is_not_served(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    await client.get("/api/me", headers=headers)
    await client.post("/api/trial", headers=headers)
    created = await client.post(
        "/api/filters", json={"marketplace": "fake", "criteria": {"query": "bike"}}, headers=headers
    )

    stranger = {"X-Telegram-Init-Data": _init_data(user_id=2)}
    response = await client.get(f"/api/filters/{created.json()['id']}/items", headers=stranger)

    assert response.status_code == 400
    assert "не найден" in response.json()["detail"].lower()


async def test_filter_can_be_paused_and_deleted(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    await client.get("/api/me", headers=headers)
    await client.post("/api/trial", headers=headers)
    created = await client.post(
        "/api/filters", json={"marketplace": "fake", "criteria": {"query": "bike"}}, headers=headers
    )
    filter_id = created.json()["id"]

    paused = await client.post(f"/api/filters/{filter_id}/toggle", headers=headers)
    deleted = await client.delete(f"/api/filters/{filter_id}", headers=headers)
    left = await client.get("/api/filters", headers=headers)

    assert paused.json()["is_active"] is False
    assert deleted.status_code == 204
    assert left.json() == []


async def test_stars_invoice_is_issued_for_the_chosen_tariff(
    client: AsyncClient, headers: dict[str, str], api: FastAPI
) -> None:
    await client.get("/api/me", headers=headers)

    response = await client.post("/api/payments/stars", json={"tariff": "month"}, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"provider": "stars", "url": "https://t.me/invoice/month"}
    user_id, offer = api.state.stars.calls[0]
    assert user_id == 1
    assert offer.price_stars == 675, "30 дней по 25 звёзд минус 10%"


async def test_crypto_payment_is_absent_until_configured(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post("/api/payments/cryptobot", json={"tariff": "day"}, headers=headers)

    assert response.status_code == 503


async def test_cryptobot_webhook_without_valid_signature_is_rejected(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/payments/cryptobot/webhook",
        content=b'{"update_type": "invoice_paid"}',
        headers={"crypto-pay-api-signature": "deadbeef"},
    )

    assert response.status_code == 401


# ---------- Платёжные payload и подпись CryptoBot ----------


def test_payment_payload_survives_the_round_trip() -> None:
    from app.domain.tariffs import Tariff

    assert parse_payload(build_payload(Tariff.YEAR, 42)) == (Tariff.YEAR, 42)
    assert parse_payload("hacked:42") is None


def test_cryptobot_signature_check_accepts_only_its_own_token() -> None:
    import hashlib
    import hmac

    token = "12345:crypto-token"
    body = b'{"update_type":"invoice_paid"}'
    signature = hmac.new(hashlib.sha256(token.encode()).digest(), body, hashlib.sha256).hexdigest()

    assert CryptoBotPayments.verify(token, body, signature) is True
    assert CryptoBotPayments.verify("другой-токен", body, signature) is False


def test_cryptobot_webhook_body_is_parsed_into_a_payment() -> None:
    from app.domain.tariffs import Tariff

    body = json.dumps(
        {
            "update_type": "invoice_paid",
            "payload": {
                "invoice_id": 777,
                "payload": build_payload(Tariff.WEEK, 42),
                "amount": "3.50",
                "asset": "USDT",
            },
        }
    ).encode()

    paid = CryptoBotPayments.parse_webhook(body)

    assert paid is not None
    assert (paid.invoice_id, paid.tariff, paid.user_id) == ("777", Tariff.WEEK, 42)
    assert paid.amount == Decimal("3.50")
    assert CryptoBotPayments.parse_webhook(b'{"update_type":"invoice_expired"}') is None
