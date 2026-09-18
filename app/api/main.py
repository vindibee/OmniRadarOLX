"""Web API для Telegram Mini App.

Отдельный процесс, те же сервисы и та же БД, что у бота (см. ``app.composition``).
Ни одного обращения к aiogram: бот шлёт сообщения, API отдаёт данные.

Запуск: ``uvicorn app.api.main:create_app --factory``
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import Billing, CurrentUser, Filters, Presets
from app.api.schemas import (
    AccessOut,
    CriteriaIn,
    FilterOut,
    MarketplaceOut,
    MeOut,
    OfferOut,
    PresetIn,
    PresetOut,
)
from app.composition import (
    build_billing_service,
    build_cache,
    build_engine,
    build_filter_service,
    build_parser_registry,
    build_preset_service,
    build_uow_factory,
)
from app.config import Settings
from app.domain.errors import DomainError
from app.domain.tariffs import PAID_TARIFFS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    """Проба для healthcheck: не требует ни подписи, ни БД."""
    return {"status": "ok"}


@router.get("/me")
async def me(user: CurrentUser, billing: Billing, filters: Filters) -> MeOut:
    """Стартовый экран Mini App: кто вошёл, что ему доступно, чем можно продлить."""
    # Пользователь мог прийти сразу в Mini App, не написав боту, — регистрируем его здесь.
    await filters.register_user(user.id, user.username, user.full_name)
    access = await billing.access(user.id)
    return MeOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        language_code=user.language_code,
        access=AccessOut.from_domain(access),
        marketplaces=[
            MarketplaceOut(code=item.code, title=item.title) for item in filters.marketplaces()
        ],
        offers=[OfferOut.from_domain(billing.offer(tariff)) for tariff in PAID_TARIFFS],
    )


@router.post("/trial", status_code=status.HTTP_201_CREATED)
async def start_trial(user: CurrentUser, billing: Billing) -> AccessOut:
    await billing.activate_trial(user.id)  # TrialAlreadyUsedError → 400
    return AccessOut.from_domain(await billing.access(user.id))


@router.get("/presets")
async def list_presets(user: CurrentUser, presets: Presets) -> list[PresetOut]:
    return [PresetOut.from_domain(item) for item in await presets.list(user.id)]


@router.post("/presets", status_code=status.HTTP_201_CREATED)
async def save_preset(user: CurrentUser, presets: Presets, body: PresetIn) -> PresetOut:
    preset = await presets.save(user.id, body.name, body.marketplace, body.criteria.to_domain())
    return PresetOut.from_domain(preset)


@router.delete("/presets/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_preset(user: CurrentUser, presets: Presets, preset_id: int) -> None:
    await presets.delete(user.id, preset_id)


@router.post("/presets/{preset_id}/monitor", status_code=status.HTTP_201_CREATED)
async def monitor_preset(
    user: CurrentUser, presets: Presets, billing: Billing, preset_id: int
) -> FilterOut:
    """Включает мониторинг по сохранённому пресету."""
    await _require_access(user.id, billing)
    return FilterOut.from_domain(await presets.start_monitoring(user.id, preset_id))


@router.get("/filters")
async def list_filters(user: CurrentUser, filters: Filters) -> list[FilterOut]:
    return [FilterOut.from_domain(item) for item in await filters.list(user.id)]


@router.post("/filters", status_code=status.HTTP_201_CREATED)
async def create_filter(
    user: CurrentUser, filters: Filters, billing: Billing, marketplace: str, body: CriteriaIn
) -> FilterOut:
    await _require_access(user.id, billing)
    return FilterOut.from_domain(await filters.create(user.id, marketplace, body.to_domain()))


@router.delete("/filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(user: CurrentUser, filters: Filters, filter_id: int) -> None:
    await filters.delete(user.id, filter_id)


async def _require_access(user_id: int, billing: Billing) -> None:
    """Мониторинг — платная часть: та же проверка, что middleware бота."""
    access = await billing.access(user_id)
    if not access.is_allowed:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Нужна активная подписка"
        )


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    """Доменные ошибки безопасны для показа пользователю — отдаём текст как есть."""
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": str(exc)})


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = build_engine(app_settings)
        cache = build_cache(app_settings)
        uow_factory = build_uow_factory(engine)
        parsers = build_parser_registry(app_settings, cache)
        filters = build_filter_service(app_settings, uow_factory, parsers)

        app.state.uow_factory = uow_factory
        app.state.billing = build_billing_service(app_settings, uow_factory)
        app.state.filters = filters
        app.state.presets = build_preset_service(app_settings, uow_factory, parsers, filters)
        logger.info("Web API запущен, площадки: %s", ", ".join(parsers.codes))
        try:
            yield
        finally:
            await parsers.aclose()
            if cache is not None:
                await cache.aclose()
            await engine.dispose()

    app = FastAPI(title="OmniRadar Mini App API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.api.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "X-Telegram-Init-Data", "Content-Type"],
    )
    app.add_exception_handler(DomainError, _domain_error)
    app.include_router(router)
    app.state.settings = app_settings
    return app
