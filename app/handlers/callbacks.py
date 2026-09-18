"""Данные кнопок под уведомлением. Ровно два действия — меню в чате по-прежнему нет."""

from aiogram.filters.callback_data import CallbackData


class FavoriteCallback(CallbackData, prefix="fav"):
    listing_id: int


class BlockSellerCallback(CallbackData, prefix="ban"):
    marketplace: str
    seller_id: str
