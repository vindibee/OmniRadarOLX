"""Зависимости FastAPI: общие с ботом сервисы + аутентификация Mini App.

Ресурсы (engine, сервисы, провайдеры оплаты) создаются один раз при старте приложения
и живут в ``app.state`` — см. ``app.api.main.create_app``. Здесь только их извлечение
и проверка личности пользователя.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.api.security import InitDataError, WebAppUser, parse_init_data
from app.config import Settings
from app.payments.cryptobot import CryptoBotPayments
from app.payments.stars import StarsPayments
from app.services.admin import AdminService
from app.services.billing import BillingService
from app.services.collections import CollectionService
from app.services.filters import FilterService
from app.services.status import StatusService

INIT_DATA_MAX_AGE = timedelta(hours=24)


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_billing(request: Request) -> BillingService:
    billing: BillingService = request.app.state.billing
    return billing


def get_filters(request: Request) -> FilterService:
    filters: FilterService = request.app.state.filters
    return filters


def get_stars(request: Request) -> StarsPayments:
    stars: StarsPayments = request.app.state.stars
    return stars


def get_cryptobot(request: Request) -> CryptoBotPayments:
    cryptobot: CryptoBotPayments | None = request.app.state.cryptobot
    if cryptobot is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Оплата криптовалютой не подключена",
        )
    return cryptobot


def get_admin(request: Request) -> AdminService:
    admin: AdminService = request.app.state.admin
    return admin


def get_collections(request: Request) -> CollectionService:
    collections: CollectionService = request.app.state.collections
    return collections


def get_status(request: Request) -> StatusService:
    status_service: StatusService = request.app.state.status
    return status_service


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


async def get_admin_user(
    user: Annotated[WebAppUser, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WebAppUser:
    """Админка — только для Telegram id из ADMIN_IDS. Личность уже подтверждена подписью."""
    if user.id not in set(settings.admin_ids):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    return user


CurrentUser = Annotated[WebAppUser, Depends(get_current_user)]
AdminUser = Annotated[WebAppUser, Depends(get_admin_user)]
Admin = Annotated[AdminService, Depends(get_admin)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Billing = Annotated[BillingService, Depends(get_billing)]
Filters = Annotated[FilterService, Depends(get_filters)]
Collections = Annotated[CollectionService, Depends(get_collections)]
Status = Annotated[StatusService, Depends(get_status)]
Stars = Annotated[StarsPayments, Depends(get_stars)]
CryptoBot = Annotated[CryptoBotPayments, Depends(get_cryptobot)]
