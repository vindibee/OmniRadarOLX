"""Кнопки под уведомлением: сохранить объявление и скрыть продавца.

Оба действия отвечают всплывающей подсказкой и меняют клавиатуру сообщения — чтобы
было видно, что нажатие сработало. Меню в чате по-прежнему нет.
"""

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.handlers.callbacks import BlockSellerCallback, FavoriteCallback
from app.services.collections import CollectionService

logger = logging.getLogger(__name__)

router = Router(name="actions")


@router.callback_query(FavoriteCallback.filter())
async def add_to_favorites(
    callback: CallbackQuery, callback_data: FavoriteCallback, collections: CollectionService
) -> None:
    if callback.from_user is None:
        return
    added = await collections.add_favorite(callback.from_user.id, callback_data.listing_id)
    await callback.answer("⭐ Сохранено в избранное" if added else "Уже в избранном")
    await _mark_pressed(callback, FavoriteCallback.__prefix__, "⭐ В избранном")


@router.callback_query(BlockSellerCallback.filter())
async def block_seller(
    callback: CallbackQuery, callback_data: BlockSellerCallback, collections: CollectionService
) -> None:
    if callback.from_user is None:
        return
    await collections.block_seller(
        callback.from_user.id, callback_data.marketplace, callback_data.seller_id
    )
    await callback.answer("🚫 Объявления этого продавца больше не придут", show_alert=True)
    await _mark_pressed(callback, BlockSellerCallback.__prefix__, "🚫 Продавец скрыт")


async def _mark_pressed(callback: CallbackQuery, prefix: str, text: str) -> None:
    """Заменяет нажатую кнопку на неактивную подпись, остальные оставляет как есть."""
    message = callback.message
    # У старого сообщения Telegram отдаёт «недоступную» заглушку без разметки.
    if not isinstance(message, Message) or not isinstance(
        message.reply_markup, InlineKeyboardMarkup
    ):
        return

    rows: list[list[InlineKeyboardButton]] = []
    for row in message.reply_markup.inline_keyboard:
        new_row = [
            InlineKeyboardButton(text=text, callback_data="noop")
            if (button.callback_data or "").startswith(f"{prefix}:")
            else button
            for button in row
        ]
        rows.append(new_row)
    try:
        await message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except TelegramBadRequest:
        # Сообщение старое или уже отредактировано — не повод показывать ошибку пользователю.
        logger.debug("Не удалось обновить клавиатуру уведомления", exc_info=True)


@router.callback_query(lambda callback: callback.data == "noop")
async def ignore(callback: CallbackQuery) -> None:
    await callback.answer()
