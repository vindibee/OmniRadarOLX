"""Зависимости FastAPI: общие с ботом сервисы + аутентификация Mini App.

Ресурсы (engine, сервисы) живут в ``app.state`` и создаются один раз при старте приложения —
см. ``app.api.main.create_app``. Здесь только их извлечение и проверка пользователя.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.api.security import InitDataError, WebAppUser, parse_init_data
from app.config import Settings
from app.services.billing import BillingService
from app.services.filters import FilterService
from app.services.interfaces import UnitOfWorkFactory
from app.services.presets import PresetService

INIT_DATA_MAX_AGE = timedelta(hours=24)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_uow_factory(request: Request) -> UnitOfWorkFactory:
    factory: UnitOfWorkFactory = request.app.state.uow_factory
    return factory


def get_billing(request: Request) -> BillingService:
    billing: BillingService = request.app.state.billing
    return billing


def get_filters(request: Request) -> FilterService:
    filters: FilterService = request.app.state.filters
    return filters


def get_presets(request: Request) -> PresetService:
    presets: PresetService = request.app.state.presets
    return presets


async def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
    x_telegram_init_data: Annotated[str | None, Header()] = None,
) -> WebAppUser:
    """Личность пользователя Mini App — только из подписанной Telegram initData.

    Принимаются оба способа передачи: ``Authorization: tma <initData>`` (соглашение
    Telegram SDK) и заголовок ``X-Telegram-Init-Data``.
    """
    raw = x_telegram_init_data
    if not raw and authorization and authorization.lower().startswith("tma "):
        raw = authorization[4:]
    try:
        return parse_init_data(
            raw or "",
            settings.bot.token.get_secret_value(),
            max_age=INIT_DATA_MAX_AGE,
        )
    except InitDataError as exc:
        # Наружу — только факт отказа: детали проверки не подсказывают, как её обойти.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Некорректная initData",
            headers={"WWW-Authenticate": "tma"},
        ) from exc


CurrentUser = Annotated[WebAppUser, Depends(get_current_user)]
Billing = Annotated[BillingService, Depends(get_billing)]
Filters = Annotated[FilterService, Depends(get_filters)]
Presets = Annotated[PresetService, Depends(get_presets)]
UowFactory = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
