"""Корень композиции: здесь и только здесь создаются и связываются зависимости.

Запуск: ``python -m app.main``
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import BotCommand

from app.config import BotSettings, HttpSettings, Settings
from app.database.session import create_engine, create_session_factory
from app.handlers import create_root_router
from app.notifications.telegram import TelegramNotifier
from app.repositories import SqlAlchemyUnitOfWork
from app.services.interfaces import UnitOfWork
from app.services.monitoring import MonitoringOptions, MonitoringService
from app.services.parsers import MarketplaceParser, ParserRegistry
from app.services.parsers.http_client import HttpClient, HttpClientOptions
from app.services.parsers.olx_ua import DEFAULT_HEADERS as OLX_HEADERS
from app.services.parsers.olx_ua import OlxUaParser
from app.services.subscriptions import SubscriptionService
from app.workers.monitor import MonitoringWorker

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
    ),
}


def create_storage(bot_settings: BotSettings) -> BaseStorage:
    """MemoryStorage — по умолчанию; RedisStorage — если диалоги должны переживать перезапуск."""
    if bot_settings.fsm_storage == "redis":
        return RedisStorage.from_url(bot_settings.redis_url)
    return MemoryStorage()


def build_parser_registry(settings: Settings) -> ParserRegistry:
    registry = ParserRegistry()
    for code in settings.enabled_marketplaces:
        factory = PARSER_FACTORIES.get(code)
        if factory is None:
            raise ValueError(f"Неизвестная площадка в ENABLED_MARKETPLACES: {code}")
        registry.register(factory(settings))
    return registry


async def run(settings: Settings) -> None:
    engine = create_engine(settings.db)
    session_factory = create_session_factory(engine)

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    parsers = build_parser_registry(settings)
    bot = Bot(
        token=settings.bot.token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    subscription_service = SubscriptionService(
        uow_factory,
        parsers,
        max_subscriptions_per_user=settings.monitoring.max_subscriptions_per_user,
    )
    monitoring_service = MonitoringService(
        uow_factory,
        parsers,
        TelegramNotifier(bot),
        MonitoringOptions(
            concurrency=settings.monitoring.concurrency,
            publish_grace=timedelta(minutes=settings.monitoring.publish_grace_minutes),
            max_delivery_attempts=settings.monitoring.max_delivery_attempts,
            delivery_batch_size=settings.monitoring.delivery_batch_size,
        ),
    )
    worker = MonitoringWorker(
        monitoring_service, interval_seconds=settings.monitoring.interval_seconds
    )

    # Dependency Injection aiogram: всё, что передано в Dispatcher, доступно хендлерам по имени.
    dispatcher = Dispatcher(
        storage=create_storage(settings.bot), subscription_service=subscription_service
    )
    dispatcher.include_router(create_root_router())

    stop_event = asyncio.Event()
    monitor_task: asyncio.Task[None] | None = None

    async def on_startup() -> None:
        nonlocal monitor_task
        try:
            await bot.set_my_commands(
                [
                    BotCommand(command="start", description="Главное меню"),
                    BotCommand(command="filters", description="Мои фильтры"),
                    BotCommand(command="help", description="Помощь"),
                    BotCommand(command="cancel", description="Отменить ввод"),
                ]
            )
        except TelegramAPIError as exc:  # меню команд — косметика, запуск не блокирует
            logger.warning("Не удалось установить команды бота: %s", exc)
        monitor_task = asyncio.create_task(worker.run(stop_event), name="monitoring")

    async def on_shutdown() -> None:
        stop_event.set()
        if monitor_task is not None:
            try:
                await asyncio.wait_for(monitor_task, timeout=30)
            except TimeoutError:
                monitor_task.cancel()
                with suppress(asyncio.CancelledError):
                    await monitor_task

    dispatcher.startup.register(on_startup)
    dispatcher.shutdown.register(on_shutdown)

    try:
        logger.info(
            "Бот запускается, площадки: %s, FSM: %s",
            ", ".join(parsers.codes),
            settings.bot.fsm_storage,
        )
        await dispatcher.start_polling(bot)
    finally:
        await parsers.aclose()
        await dispatcher.storage.close()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    settings = Settings()  # значения приходят из окружения и .env
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
