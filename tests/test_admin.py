"""Админка владельца: доступ без подписки и без лимитов, сводка и ручная выдача."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.api.main import create_app
from app.api.security import build_init_data
from app.config import BotSettings, Settings
from app.domain.entities import SearchCriteria, User
from app.domain.errors import FilterLimitExceededError
from app.services.admin import AdminService
from app.services.billing import BillingOptions, BillingService
from app.services.filters import FilterService
from app.services.parsers import ParserRegistry
from tests.conftest import FakeParser, UowFactory

TOKEN = "123456:TEST-TOKEN"
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
ADMIN_ID = 4242  # произвольный id: настоящему владельцу в публичном репозитории не место
STRANGER_ID = 777


def _init_data(user_id: int) -> str:
    user = {"id": user_id, "first_name": "Тест", "username": f"u{user_id}"}
    return build_init_data(
        {
            "auth_date": str(int(NOW.timestamp())),
            "user": json.dumps(user, ensure_ascii=False, separators=(",", ":")),
        },
        TOKEN,
    )


@pytest.fixture
def billing(uow_factory: UowFactory) -> BillingService:
    return BillingService(uow_factory, BillingOptions(), admin_ids=[ADMIN_ID], clock=lambda: NOW)


@pytest.fixture
def filters(uow_factory: UowFactory) -> FilterService:
    return FilterService(
        uow_factory,
        ParserRegistry([FakeParser()]),
        max_filters_per_user=2,
        admin_ids=[ADMIN_ID],
    )


@pytest.fixture
def api(uow_factory: UowFactory, billing: BillingService, filters: FilterService) -> FastAPI:
    settings = Settings(bot=BotSettings(token=SecretStr(TOKEN)), admin_ids=[ADMIN_ID])
    app = create_app(settings)
    app.state.uow_factory = uow_factory
    app.state.filters = filters
    app.state.billing = billing
    app.state.admin = AdminService(uow_factory, billing, clock=lambda: NOW)
    app.state.stars = None
    app.state.cryptobot = None
    return app


@pytest.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        yield client


def _headers(user_id: int) -> dict[str, str]:
    return {"X-Telegram-Init-Data": _init_data(user_id)}


async def test_owner_has_access_without_any_subscription(billing: BillingService) -> None:
    access = await billing.access(ADMIN_ID)

    assert (access.is_allowed, access.is_admin) == (True, True)
    assert access.until is None, "доступ владельца бессрочный"
    assert await billing.access(STRANGER_ID) == await billing.access(STRANGER_ID)
    assert (await billing.access(STRANGER_ID)).is_allowed is False


async def test_owner_is_not_limited_by_the_filter_quota(
    filters: FilterService, uow_factory: UowFactory
) -> None:
    async with uow_factory() as uow:
        for user_id in (ADMIN_ID, STRANGER_ID):
            await uow.users.upsert(User(id=user_id, username=None, full_name="U"))
        await uow.commit()

    for index in range(5):  # лимит в фикстуре — 2
        await filters.create(ADMIN_ID, "fake", SearchCriteria(query=f"admin-{index}"))

    await filters.create(STRANGER_ID, "fake", SearchCriteria(query="a"))
    await filters.create(STRANGER_ID, "fake", SearchCriteria(query="b"))
    with pytest.raises(FilterLimitExceededError):
        await filters.create(STRANGER_ID, "fake", SearchCriteria(query="c"))

    assert len(await filters.list(ADMIN_ID)) == 5


async def test_admin_endpoints_are_closed_for_everyone_else(client: AsyncClient) -> None:
    await client.get("/api/me", headers=_headers(STRANGER_ID))

    for path in ("/api/admin/overview", "/api/admin/users"):
        assert (await client.get(path, headers=_headers(STRANGER_ID))).status_code == 403
    denied = await client.post(
        "/api/admin/grant", json={"user_id": STRANGER_ID, "days": 30}, headers=_headers(STRANGER_ID)
    )
    assert denied.status_code == 403
    assert (await client.get("/api/admin/overview")).status_code == 401, "и без подписи тоже"


async def test_me_marks_the_owner(client: AsyncClient) -> None:
    body = (await client.get("/api/me", headers=_headers(ADMIN_ID))).json()

    assert body["access"]["is_admin"] is True
    assert body["access"]["is_allowed"] is True


async def test_overview_counts_what_happened(client: AsyncClient) -> None:
    await client.get("/api/me", headers=_headers(ADMIN_ID))
    await client.get("/api/me", headers=_headers(STRANGER_ID))
    await client.post(
        "/api/filters",
        json={"marketplace": "fake", "criteria": {"query": "bike"}},
        headers=_headers(ADMIN_ID),
    )

    overview = (await client.get("/api/admin/overview", headers=_headers(ADMIN_ID))).json()

    assert overview["users"] == 2
    assert (overview["filters"], overview["active_filters"]) == (1, 1)
    assert overview["paying_users"] == 0


async def test_owner_can_grant_access_by_hand(client: AsyncClient) -> None:
    await client.get("/api/me", headers=_headers(ADMIN_ID))
    await client.get("/api/me", headers=_headers(STRANGER_ID))

    granted = await client.post(
        "/api/admin/grant", json={"user_id": STRANGER_ID, "days": 14}, headers=_headers(ADMIN_ID)
    )
    users = (await client.get("/api/admin/users", headers=_headers(ADMIN_ID))).json()
    stranger = next(user for user in users if user["id"] == STRANGER_ID)

    assert granted.status_code == 201
    assert granted.json()["until"].startswith((NOW + timedelta(days=14)).strftime("%Y-%m-%d"))
    assert stranger["access_until"] is not None, "выданный доступ виден в списке пользователей"


async def test_granting_twice_extends_instead_of_overwriting(client: AsyncClient) -> None:
    await client.get("/api/me", headers=_headers(ADMIN_ID))
    await client.get("/api/me", headers=_headers(STRANGER_ID))

    first = await client.post(
        "/api/admin/grant", json={"user_id": STRANGER_ID, "days": 10}, headers=_headers(ADMIN_ID)
    )
    second = await client.post(
        "/api/admin/grant", json={"user_id": STRANGER_ID, "days": 10}, headers=_headers(ADMIN_ID)
    )

    assert second.json()["until"] > first.json()["until"], "вторая выдача продлевает, а не заменяет"
