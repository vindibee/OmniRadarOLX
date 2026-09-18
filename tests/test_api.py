"""Mini App API: подпись initData и эндпоинты поверх тех же сервисов, что у бота."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.main import create_app
from app.api.security import InitDataError, build_init_data, parse_init_data
from app.config import BotSettings, Settings
from app.services.billing import BillingOptions, BillingService
from app.services.filters import FilterService
from app.services.parsers import ParserRegistry
from app.services.presets import PresetService
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


@pytest.fixture
def api(uow_factory: UowFactory) -> FastAPI:
    """Приложение с теми же сервисами, но на тестовой БД и фейковом парсере."""
    settings = Settings(bot=BotSettings(token=SecretStr(TOKEN)))
    app = create_app(settings)
    parsers = ParserRegistry([FakeParser()])
    filters = FilterService(uow_factory, parsers, max_filters_per_user=5)
    app.state.uow_factory = uow_factory
    app.state.filters = filters
    app.state.billing = BillingService(uow_factory, BillingOptions(), clock=lambda: NOW)
    app.state.presets = PresetService(uow_factory, parsers, filters)
    return app


@pytest.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    # ASGITransport не запускает lifespan — ресурсы подставлены фикстурой выше.
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        yield client


async def test_request_without_init_data_is_unauthorized(client: AsyncClient) -> None:
    response = await client.get("/api/me")

    assert response.status_code == 401


async def test_me_returns_access_and_price_list(client: AsyncClient) -> None:
    response = await client.get("/api/me", headers={"X-Telegram-Init-Data": _init_data()})

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert body["access"] == {
        "is_allowed": False,
        "until": None,
        "tariff": None,
        "is_trial": False,
        "trial_available": True,
    }
    assert [offer["tariff"] for offer in body["offers"]] == ["day", "week", "month", "year"]
    assert body["marketplaces"] == [{"code": "fake", "title": "Fake Market"}]


async def test_filter_requires_subscription_and_works_after_trial(client: AsyncClient) -> None:
    headers = {"Authorization": f"tma {_init_data()}"}
    await client.get("/api/me", headers=headers)  # регистрация пользователя
    body = {"query": "iphone 13", "price_max": "20000"}

    denied = await client.post("/api/filters?marketplace=fake", json=body, headers=headers)
    trial = await client.post("/api/trial", headers=headers)
    allowed = await client.post("/api/filters?marketplace=fake", json=body, headers=headers)

    assert denied.status_code == 402, "без подписки мониторинг не включается"
    assert trial.status_code == 201 and trial.json()["is_trial"] is True
    assert allowed.status_code == 201
    assert allowed.json()["title"] == "iphone 13 (… – 20 000)"


async def test_second_trial_is_refused(client: AsyncClient) -> None:
    headers = {"X-Telegram-Init-Data": _init_data()}
    await client.get("/api/me", headers=headers)

    first = await client.post("/api/trial", headers=headers)
    second = await client.post("/api/trial", headers=headers)

    assert first.status_code == 201
    assert second.status_code == 400
    assert "Демо" in second.json()["detail"]


async def test_presets_are_saved_listed_and_deleted(client: AsyncClient) -> None:
    headers = {"X-Telegram-Init-Data": _init_data()}
    await client.get("/api/me", headers=headers)
    preset = {
        "name": "Айфоны до 20к",
        "marketplace": "fake",
        "criteria": {"query": "iphone 13", "price_max": "20000", "extra": {"city_id": 268}},
    }

    created = await client.post("/api/presets", json=preset, headers=headers)
    listed = await client.get("/api/presets", headers=headers)
    deleted = await client.delete(f"/api/presets/{created.json()['id']}", headers=headers)
    empty = await client.get("/api/presets", headers=headers)

    assert created.status_code == 201
    assert listed.json()[0]["criteria"]["extra"] == {"city_id": 268}
    assert deleted.status_code == 204
    assert empty.json() == []


async def test_preset_becomes_a_monitored_filter(client: AsyncClient) -> None:
    headers = {"X-Telegram-Init-Data": _init_data()}
    await client.get("/api/me", headers=headers)
    await client.post("/api/trial", headers=headers)
    preset = {
        "name": "Велосипеды",
        "marketplace": "fake",
        "criteria": {"query": "bike"},
    }
    preset_id = (await client.post("/api/presets", json=preset, headers=headers)).json()["id"]

    response = await client.post(f"/api/presets/{preset_id}/monitor", headers=headers)
    filters = await client.get("/api/filters", headers=headers)

    assert response.status_code == 201
    assert [item["title"] for item in filters.json()] == ["bike"]
