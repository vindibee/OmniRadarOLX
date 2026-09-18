"""Web API для Telegram Mini App — единственный интерфейс управления.

Отдельный процесс, те же сервисы и та же БД, что у бота (см. ``app.composition``).
Ни одного обращения к aiogram-хендлерам: бот открывает приложение и шлёт уведомления,
всё остальное делает Mini App через этот API.

Запуск: ``uvicorn app.api.main:create_app --factory``
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import (
    Admin,
    AdminUser,
    AppSettings,
    Billing,
    CryptoBot,
    CurrentUser,
    Filters,
    Stars,
)
from app.api.schemas import (
    AccessOut,
    AdminUserOut,
    CatalogOut,
    FilterIn,
    FilterOut,
    GrantIn,
    InvoiceOut,
    ItemOut,
    LanguageIn,
    MarketplaceOut,
    MeOut,
    OfferOut,
    OverviewOut,
    TariffIn,
)
from app.composition import (
    build_admin_service,
    build_billing_service,
    build_bot,
    build_cache,
    build_cryptobot_payments,
    build_engine,
    build_filter_service,
    build_parser_registry,
    build_stars_payments,
    build_uow_factory,
)
from app.config import Settings
from app.domain.errors import DomainError
from app.domain.tariffs import PAID_TARIFFS
from app.payments.cryptobot import CRYPTOBOT_PROVIDER, CryptoBotPayments
from app.services.billing import BillingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


@router.get("/health")
async def health() -> dict[str, str]:
    """Проба для healthcheck: не требует ни подписи, ни БД."""
    return {"status": "ok"}


# ---------- Личный кабинет ----------


@router.get("/me")
async def me(
    user: CurrentUser, billing: Billing, filters: Filters, settings: AppSettings, request: Request
) -> MeOut:
    """Стартовый экран Mini App. Первый вход сюда же и регистрирует пользователя."""
    await filters.register_user(user.id, user.username, user.full_name)
    stored = await filters.get_user(user.id)
    access = await billing.access(user.id)
    methods: list[Literal["stars", "cryptobot"]] = ["stars"]
    if request.app.state.cryptobot is not None:
        methods.append("cryptobot")
    return MeOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        # Язык из Telegram — только подсказка для предвыбора на экране онбординга.
        language_code=(stored.language_code if stored else None) or user.language_code,
        needs_onboarding=stored is None or stored.language_code is None,
        access=AccessOut.from_domain(access),
        marketplaces=[
            MarketplaceOut(code=item.code, title=item.title) for item in filters.marketplaces()
        ],
        offers=[OfferOut.from_domain(billing.offer(tariff)) for tariff in PAID_TARIFFS],
        payment_methods=methods,
    )


@router.put("/me/language")
async def set_language(user: CurrentUser, filters: Filters, body: LanguageIn) -> dict[str, str]:
    await filters.set_language(user.id, body.language_code)
    return {"language_code": body.language_code}


@router.post("/trial", status_code=status.HTTP_201_CREATED)
async def start_trial(user: CurrentUser, billing: Billing) -> AccessOut:
    await billing.activate_trial(user.id)  # TrialAlreadyUsedError → 400
    return AccessOut.from_domain(await billing.access(user.id))


@router.get("/catalog")
async def catalog(
    user: CurrentUser, filters: Filters, marketplace: str | None = None
) -> CatalogOut:
    """Справочник площадки: области с городами и категории с подкатегориями.

    Отсюда Mini App строит выпадающие списки — числовых id пользователь не видит.
    """
    code = marketplace or next(iter(filters.marketplaces())).code
    return CatalogOut.model_validate(filters.catalog(code).to_json())


# ---------- Фильтры и их история ----------


@router.get("/filters")
async def list_filters(user: CurrentUser, filters: Filters) -> list[FilterOut]:
    return [FilterOut.from_domain(item) for item in await filters.list(user.id)]


@router.post("/filters", status_code=status.HTTP_201_CREATED)
async def create_filter(
    user: CurrentUser, filters: Filters, billing: Billing, body: FilterIn
) -> FilterOut:
    await _require_access(user.id, billing)
    created = await filters.create(
        user.id, body.marketplace, body.criteria.to_domain(), title=body.name
    )
    return FilterOut.from_domain(created)


@router.post("/filters/{filter_id}/toggle")
async def toggle_filter(user: CurrentUser, filters: Filters, filter_id: int) -> FilterOut:
    return FilterOut.from_domain(await filters.toggle(user.id, filter_id))


@router.delete("/filters/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_filter(user: CurrentUser, filters: Filters, filter_id: int) -> None:
    await filters.delete(user.id, filter_id)


@router.get("/filters/{filter_id}/items")
async def filter_history(
    user: CurrentUser,
    filters: Filters,
    settings: AppSettings,
    filter_id: int,
    offset: int = Query(default=0, ge=0),
) -> list[ItemOut]:
    """История находок фильтра: что нашли, когда опубликовано, когда доставлено."""
    found = await filters.history(
        user.id, filter_id, limit=settings.api.history_page_size, offset=offset
    )
    return [ItemOut.from_domain(item) for item in found]


# ---------- Оплата ----------


@router.post("/payments/stars")
async def create_stars_invoice(
    user: CurrentUser, billing: Billing, stars: Stars, body: TariffIn
) -> InvoiceOut:
    """Ссылка на счёт в Stars; Mini App открывает её через ``WebApp.openInvoice``."""
    offer = billing.offer(body.tariff)
    link = await stars.create_invoice_link(user.id, offer, title=f"OmniRadar: {offer.days} дн.")
    return InvoiceOut(provider="stars", url=link)


@router.post("/payments/cryptobot")
async def create_crypto_invoice(
    user: CurrentUser, billing: Billing, cryptobot: CryptoBot, body: TariffIn
) -> InvoiceOut:
    offer = billing.offer(body.tariff)
    invoice = await cryptobot.create_invoice(
        user.id, offer, description=f"OmniRadar: доступ на {offer.days} дн."
    )
    return InvoiceOut(provider="cryptobot", url=invoice.pay_url)


@router.post("/payments/cryptobot/webhook", include_in_schema=False)
async def cryptobot_webhook(
    request: Request,
    settings: AppSettings,
    crypto_pay_api_signature: str = Header(default=""),
) -> Response:
    """Подтверждение оплаты от CryptoBot. Подпись проверяется до разбора тела."""
    token = settings.billing.cryptobot_token
    body = await request.body()
    if token is None or not CryptoBotPayments.verify(
        token.get_secret_value(), body, crypto_pay_api_signature
    ):
        logger.warning("CryptoBot: вебхук с неверной подписью")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature")

    paid = CryptoBotPayments.parse_webhook(body)
    if paid is None:
        return Response(status_code=status.HTTP_200_OK)  # не наше событие — просто подтверждаем

    billing: BillingService = request.app.state.billing
    await billing.activate_paid(
        paid.user_id,
        paid.tariff,
        provider=CRYPTOBOT_PROVIDER,
        payment_id=paid.invoice_id,  # повтор вебхука не продлит подписку дважды
        amount=paid.amount,
        currency=paid.asset,
    )
    logger.info("CryptoBot: пользователь %d оплатил тариф %s", paid.user_id, paid.tariff.value)
    return Response(status_code=status.HTTP_200_OK)


# ---------- Админка владельца ----------


@router.get("/admin/overview")
async def admin_overview(user: AdminUser, admin: Admin) -> OverviewOut:
    return OverviewOut.from_domain(await admin.overview())


@router.get("/admin/users")
async def admin_users(user: AdminUser, admin: Admin) -> list[AdminUserOut]:
    return [AdminUserOut.from_domain(item) for item in await admin.users()]


@router.post("/admin/grant", status_code=status.HTTP_201_CREATED)
async def admin_grant(user: AdminUser, admin: Admin, body: GrantIn) -> dict[str, str]:
    """Ручная выдача доступа: продлевает от конца текущего периода, как и оплата."""
    subscription = await admin.grant(body.user_id, body.days, by_admin_id=user.id)
    return {"user_id": str(body.user_id), "until": subscription.ends_at.isoformat()}


async def _require_access(user_id: int, billing: BillingService) -> None:
    """Мониторинг — платная часть."""
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
        bot = build_bot(app_settings)  # нужен только чтобы выставлять счета в Stars

        app.state.uow_factory = uow_factory
        app.state.billing = build_billing_service(app_settings, uow_factory)
        app.state.filters = build_filter_service(app_settings, uow_factory, parsers)
        app.state.stars = build_stars_payments(bot)
        app.state.admin = build_admin_service(uow_factory, app.state.billing)
        app.state.cryptobot = build_cryptobot_payments(app_settings)
        logger.info("Web API запущен, площадки: %s", ", ".join(parsers.codes))
        try:
            yield
        finally:
            await parsers.aclose()
            if app.state.cryptobot is not None:
                await app.state.cryptobot.aclose()
            await bot.session.close()
            if cache is not None:
                await cache.aclose()
            await engine.dispose()

    app = FastAPI(title="OmniRadar Mini App API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.api.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "X-Telegram-Init-Data", "Content-Type"],
    )
    app.add_exception_handler(DomainError, _domain_error)
    app.include_router(router)
    app.state.settings = app_settings
    app.state.cryptobot = None  # до lifespan состояние должно быть предсказуемым
    return app
