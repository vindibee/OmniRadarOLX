"""Корень композиции: здесь и только здесь создаются и связываются зависимости.

Запуск: ``python -m app.main``
"""

import asyncio
import logging
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

from app.composition import (
    build_cache,
    build_engine,
    build_filter_service,
    build_parser_registry,
    build_uow_factory,
)
from app.config import BotSettings, Settings
from app.handlers import create_root_router
from app.notifications.telegram import TelegramNotifier
from app.services.monitoring import MonitoringOptions, MonitoringService
from app.workers.monitor import MonitoringWorker

logger = logging.getLogger(__name__)


def create_storage(bot_settings: BotSettings, redis_url: str) -> BaseStorage:
    """MemoryStorage — по умолчанию; RedisStorage — если диалоги должны переживать перезапуск."""
    if bot_settings.fsm_storage == "redis":
        return RedisStorage.from_url(redis_url)
    return MemoryStorage()


async def run(settings: Settings) -> None:
    engine = build_engine(settings)
    uow_factory = build_uow_factory(engine)
    cache = build_cache(settings)
    parsers = build_parser_registry(settings, cache)
    bot = Bot(
        token=settings.bot.token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    filter_service = build_filter_service(settings, uow_factory, parsers)
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
        storage=create_storage(settings.bot, settings.redis.url), filter_service=filter_service
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
        if cache is not None:
            await cache.aclose()
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
