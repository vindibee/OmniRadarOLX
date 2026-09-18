"""Обработчики ошибок. Подключаются к корневому роутеру, поэтому видят ошибки всех дочерних."""

import logging

from aiogram import F, Router
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import CallbackQuery, ErrorEvent, Message

from app.domain.errors import DomainError

logger = logging.getLogger(__name__)


def register_error_handlers(router: Router) -> None:
    router.error(ExceptionTypeFilter(DomainError), F.update.message.as_("message"))(
        _domain_error_in_message
    )
    router.error(ExceptionTypeFilter(DomainError), F.update.callback_query.as_("callback"))(
        _domain_error_in_callback
    )
    router.error()(_unexpected_error)


async def _domain_error_in_message(event: ErrorEvent, message: Message) -> None:
    await message.answer(f"⚠️ {event.exception}")


async def _domain_error_in_callback(event: ErrorEvent, callback: CallbackQuery) -> None:
    await callback.answer(f"⚠️ {event.exception}", show_alert=True)


async def _unexpected_error(event: ErrorEvent) -> None:
    logger.error("Необработанная ошибка в хендлере", exc_info=event.exception)
    callback = event.update.callback_query
    message = event.update.message
    try:
        if callback is not None:
            await callback.answer("Что-то пошло не так, попробуйте позже", show_alert=True)
        elif message is not None:
            await message.answer("Что-то пошло не так, попробуйте позже.")
    except Exception:  # сеть могла пропасть — не роняем обработку апдейтов
        logger.debug("Не удалось сообщить пользователю об ошибке", exc_info=True)
