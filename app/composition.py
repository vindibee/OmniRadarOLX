"""Сборка зависимостей, общая для бота и Web API.

Оба процесса (aiogram и FastAPI) поднимают одни и те же сервисы поверх одной БД и одного Redis,
поэтому правила сборки лежат здесь, а не дублируются в двух точках входа.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.cache.redis import RedisCache
from app.config import HttpSettings, Settings
from app.database.session import create_engine, create_session_factory
from app.payments.cryptobot import CryptoBotPayments
from app.payments.stars import StarsPayments
from app.repositories import SqlAlchemyUnitOfWork
from app.services.admin import AdminService
from app.services.billing import BillingOptions, BillingService
from app.services.filters import FilterService
from app.services.interfaces import Cache, UnitOfWork, UnitOfWorkFactory
from app.services.parsers import MarketplaceParser, ParserRegistry
from app.services.parsers.cache import CacheOptions, CachingParser
from app.services.parsers.catalog import load_catalog
from app.services.parsers.http_client import HttpClient, HttpClientOptions
from app.services.parsers.olx_ua import DEFAULT_HEADERS as OLX_HEADERS
from app.services.parsers.olx_ua import OlxUaParser

logger = logging.getLogger(__name__)

ParserFactory = Callable[[Settings], MarketplaceParser]


def _http_client(settings: HttpSettings, headers: dict[str, str]) -> HttpClient:
    return HttpClient(
        HttpClientOptions(
            impersonate=settings.impersonate,
            timeout_seconds=settings.timeout_seconds,
            max_retries=settings.max_retries,
            backoff_base_seconds=settings.backoff_base_seconds,
            backoff_max_seconds=settings.backoff_max_seconds,
            min_request_interval_seconds=settings.min_request_interval_seconds,
            proxy=settings.proxy,
        ),
        default_headers=headers,
    )


# Новая площадка = новая строка здесь + код площадки в ENABLED_MARKETPLACES.
# У каждого парсера свой HTTP-клиент: свои cookies, свой темп запросов.
PARSER_FACTORIES: dict[str, ParserFactory] = {
    OlxUaParser.code: lambda s: OlxUaParser(
        _http_client(s.http, OLX_HEADERS),
        page_size=s.parser.page_size,
        max_pages=s.parser.max_pages,
        catalog=load_catalog(OlxUaParser.code),
    ),
}


def build_engine(settings: Settings) -> AsyncEngine:
    return create_engine(settings.db)


def build_uow_factory(engine: AsyncEngine) -> UnitOfWorkFactory:
    session_factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    return uow_factory


def build_cache(settings: Settings) -> RedisCache | None:
    """Кэш выключается одним флагом: без него всё работает, только запросов к площадке больше."""
    if not settings.cache.enabled:
        logger.info("Кэш парсеров выключен (CACHE__ENABLED=false)")
        return None
    return RedisCache.from_url(settings.redis.url)


def build_parser_registry(settings: Settings, cache: Cache | None = None) -> ParserRegistry:
    registry = ParserRegistry()
    options = CacheOptions(
        ttl_seconds=settings.cache.ttl_seconds,
        lock_ttl_seconds=settings.cache.lock_ttl_seconds,
        wait_seconds=settings.cache.wait_seconds,
    )
    for code in settings.enabled_marketplaces:
        factory = PARSER_FACTORIES.get(code)
        if factory is None:
            raise ValueError(f"Неизвестная площадка в ENABLED_MARKETPLACES: {code}")
        parser = factory(settings)
        # Кэш подставляется вместо парсера: реестр и мониторинг разницы не видят.
        registry.register(CachingParser(parser, cache, options) if cache else parser)
    return registry


def build_filter_service(
    settings: Settings, uow_factory: UnitOfWorkFactory, parsers: ParserRegistry
) -> FilterService:
    return FilterService(
        uow_factory,
        parsers,
        max_filters_per_user=settings.monitoring.max_filters_per_user,
        admin_ids=settings.admin_ids,
    )


def build_billing_service(settings: Settings, uow_factory: UnitOfWorkFactory) -> BillingService:
    return BillingService(
        uow_factory,
        BillingOptions(
            day_price_stars=settings.billing.day_price_stars,
            day_price_usd=settings.billing.day_price_usd,
        ),
        admin_ids=settings.admin_ids,
    )


def build_admin_service(uow_factory: UnitOfWorkFactory, billing: BillingService) -> AdminService:
    return AdminService(uow_factory, billing)


def build_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot.token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def build_stars_payments(bot: Bot) -> StarsPayments:
    return StarsPayments(bot)


def build_cryptobot_payments(settings: Settings) -> CryptoBotPayments | None:
    """Без токена CryptoBot способ оплаты просто отсутствует — API отдаст 503."""
    token = settings.billing.cryptobot_token
    if token is None:
        logger.info("CryptoBot не настроен (BILLING__CRYPTOBOT_TOKEN пуст)")
        return None
    return CryptoBotPayments(
        token.get_secret_value(),
        network=settings.billing.cryptobot_network,
        asset=settings.billing.cryptobot_asset,
    )
