"""Обработчик ошибок. В чате бот почти ничего не делает, поэтому ответ один и короткий."""

import logging

from aiogram import Router
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)


def register_error_handlers(router: Router) -> None:
    router.error()(_unexpected_error)


async def _unexpected_error(event: ErrorEvent) -> None:
    logger.error("Необработанная ошибка в хендлере", exc_info=event.exception)
    message = event.update.message
    if message is None:
        return
    try:
        await message.answer("Что-то пошло не так, попробуйте позже.")
    except Exception:  # сеть могла пропасть — не роняем обработку апдейтов
        logger.debug("Не удалось сообщить пользователю об ошибке", exc_info=True)
